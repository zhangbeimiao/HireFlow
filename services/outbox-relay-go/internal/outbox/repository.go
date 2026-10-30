package outbox

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Event struct {
	ID          uuid.UUID
	TenantID    uuid.UUID
	AggregateID uuid.UUID
	EventType   string
	Payload     json.RawMessage
	Attempts    int
	CreatedAt   time.Time
}

type Repository struct{ pool *pgxpool.Pool }

func NewRepository(pool *pgxpool.Pool) *Repository { return &Repository{pool: pool} }

func (r *Repository) Claim(ctx context.Context, limit int, lease time.Duration) ([]Event, error) {
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
    lease_expires_at = now() + ($2 * interval '1 millisecond')
FROM claimable
WHERE event.id = claimable.id
RETURNING event.id, event.tenant_id, event.aggregate_id, event.event_type,
          event.payload, event.attempts, event.created_at`
	rows, err := r.pool.Query(ctx, query, limit, lease.Milliseconds())
	if err != nil {
		return nil, fmt.Errorf("claim outbox events: %w", err)
	}
	defer rows.Close()
	events := make([]Event, 0, limit)
	for rows.Next() {
		var event Event
		if err := rows.Scan(&event.ID, &event.TenantID, &event.AggregateID, &event.EventType, &event.Payload, &event.Attempts, &event.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan claimed event: %w", err)
		}
		events = append(events, event)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate claimed events: %w", err)
	}
	return events, nil
}

func (r *Repository) MarkPublished(ctx context.Context, eventID uuid.UUID) error {
	result, err := r.pool.Exec(ctx, `UPDATE outbox_events
SET status = 'published', lease_expires_at = NULL, next_attempt_at = NULL
WHERE id = $1 AND status = 'publishing'`, eventID)
	if err != nil {
		return fmt.Errorf("mark event published: %w", err)
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("mark event published: expected one publishing row, updated %d", result.RowsAffected())
	}
	return nil
}

func (r *Repository) MarkFailed(ctx context.Context, eventID uuid.UUID, attempts int) error {
	result, err := r.pool.Exec(ctx, `UPDATE outbox_events
SET status = 'pending', next_attempt_at = now() + ($2 * interval '1 second'), lease_expires_at = NULL
WHERE id = $1 AND status = 'publishing'`, eventID, int64(Backoff(attempts)/time.Second))
	if err != nil {
		return fmt.Errorf("schedule event retry: %w", err)
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("schedule event retry: expected one publishing row, updated %d", result.RowsAffected())
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
