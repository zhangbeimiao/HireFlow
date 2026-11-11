package outbox

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Event struct {
	ID             uuid.UUID
	TenantID       uuid.UUID
	AggregateID    uuid.UUID
	EventType      string
	Payload        json.RawMessage
	Attempts       int
	CreatedAt      time.Time
	LeaseOwner     uuid.UUID
	LeaseExpiresAt time.Time
}

var ErrLeaseLost = errors.New("outbox lease lost")

type Repository struct{ pool *pgxpool.Pool }

func NewRepository(pool *pgxpool.Pool) *Repository { return &Repository{pool: pool} }

func (r *Repository) Claim(ctx context.Context, limit int, lease time.Duration) ([]Event, error) {
	leaseOwner := uuid.New()
	const query = `
WITH claimable AS (
    SELECT id FROM outbox_events
    WHERE (status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
       OR (status = 'publishing' AND lease_expires_at < now())
    ORDER BY created_at, id
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
UPDATE outbox_events AS event
SET status = 'publishing', attempts = event.attempts + 1,
    lease_expires_at = now() + ($2 * interval '1 millisecond'),
    lease_owner = $3
FROM claimable
WHERE event.id = claimable.id
RETURNING event.id, event.tenant_id, event.aggregate_id, event.event_type,
          event.payload, event.attempts, event.created_at, event.lease_owner,
          event.lease_expires_at`
	rows, err := r.pool.Query(ctx, query, limit, lease.Milliseconds(), leaseOwner)
	if err != nil {
		return nil, fmt.Errorf("claim outbox events: %w", err)
	}
	defer rows.Close()
	events := make([]Event, 0, limit)
	for rows.Next() {
		var event Event
		if err := rows.Scan(&event.ID, &event.TenantID, &event.AggregateID, &event.EventType, &event.Payload, &event.Attempts, &event.CreatedAt, &event.LeaseOwner, &event.LeaseExpiresAt); err != nil {
			return nil, fmt.Errorf("scan claimed event: %w", err)
		}
		events = append(events, event)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate claimed events: %w", err)
	}
	return events, nil
}

// RenewLease extends the lease for the unprocessed tail of a claimed batch. The
// owner predicate is the fencing token: a stale Relay cannot extend rows that a
// different instance reclaimed after expiry.
func (r *Repository) RenewLease(ctx context.Context, events []Event, lease time.Duration) (time.Time, error) {
	if len(events) == 0 {
		return time.Time{}, nil
	}
	leaseOwner := events[0].LeaseOwner
	ids := make([]uuid.UUID, 0, len(events))
	for _, event := range events {
		if event.LeaseOwner != leaseOwner {
			return time.Time{}, fmt.Errorf("renew event leases: batch contains multiple lease owners")
		}
		ids = append(ids, event.ID)
	}

	rows, err := r.pool.Query(ctx, `UPDATE outbox_events
SET lease_expires_at = clock_timestamp() + ($3 * interval '1 millisecond')
WHERE id = ANY($1::uuid[]) AND status = 'publishing' AND lease_owner = $2
RETURNING lease_expires_at`, ids, leaseOwner, lease.Milliseconds())
	if err != nil {
		return time.Time{}, fmt.Errorf("renew event leases: %w", err)
	}
	defer rows.Close()

	var renewed int
	var expiresAt time.Time
	for rows.Next() {
		var rowExpiresAt time.Time
		if err := rows.Scan(&rowExpiresAt); err != nil {
			return time.Time{}, fmt.Errorf("scan renewed event lease: %w", err)
		}
		if renewed == 0 || rowExpiresAt.Before(expiresAt) {
			expiresAt = rowExpiresAt
		}
		renewed++
	}
	if err := rows.Err(); err != nil {
		return time.Time{}, fmt.Errorf("iterate renewed event leases: %w", err)
	}
	if renewed != len(events) {
		return time.Time{}, fmt.Errorf("%w: renew event leases: expected %d owned rows, renewed %d", ErrLeaseLost, len(events), renewed)
	}
	return expiresAt, nil
}

func (r *Repository) MarkPublished(ctx context.Context, eventID, leaseOwner uuid.UUID) error {
	result, err := r.pool.Exec(ctx, `UPDATE outbox_events
SET status = 'published', lease_expires_at = NULL, lease_owner = NULL, next_attempt_at = NULL
WHERE id = $1 AND status = 'publishing' AND lease_owner = $2`, eventID, leaseOwner)
	if err != nil {
		return fmt.Errorf("mark event published: %w", err)
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("%w: mark event published: expected one owned publishing row, updated %d", ErrLeaseLost, result.RowsAffected())
	}
	return nil
}

func (r *Repository) MarkFailed(ctx context.Context, eventID, leaseOwner uuid.UUID, attempts int) error {
	result, err := r.pool.Exec(ctx, `UPDATE outbox_events
SET status = 'pending', next_attempt_at = now() + ($3 * interval '1 second'),
    lease_expires_at = NULL, lease_owner = NULL
WHERE id = $1 AND status = 'publishing' AND lease_owner = $2`, eventID, leaseOwner, int64(Backoff(attempts)/time.Second))
	if err != nil {
		return fmt.Errorf("schedule event retry: %w", err)
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("%w: schedule event retry: expected one owned publishing row, updated %d", ErrLeaseLost, result.RowsAffected())
	}
	return nil
}

func (r *Repository) Backlog(ctx context.Context) (map[string]float64, error) {
	const query = `SELECT
    count(*) FILTER (WHERE status = 'pending'),
    count(*) FILTER (WHERE status = 'publishing'),
    count(*) FILTER (WHERE status = 'pending' AND attempts > 0)
FROM outbox_events`
	var pending, publishing, retry int64
	if err := r.pool.QueryRow(ctx, query).Scan(&pending, &publishing, &retry); err != nil {
		return nil, fmt.Errorf("query outbox backlog: %w", err)
	}
	return map[string]float64{
		"pending": float64(pending), "publishing": float64(publishing), "retry": float64(retry),
	}, nil
}

func Backoff(attempts int) time.Duration {
	if attempts < 1 {
		attempts = 1
	}
	seconds := math.Pow(2, float64(attempts))
	if seconds > 300 {
		seconds = 300
	}
	return time.Duration(seconds) * time.Second
}
