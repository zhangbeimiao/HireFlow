package outbox

import (
	"context"
	"fmt"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

type relayIntegrationFixture struct {
	ctx      context.Context
	pool     *pgxpool.Pool
	tenantID uuid.UUID
}

func newRelayIntegrationFixture(t *testing.T) relayIntegrationFixture {
	t.Helper()
	databaseURL := os.Getenv("HIREFLOW_TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("HIREFLOW_TEST_DATABASE_URL is not configured")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		cancel()
		t.Fatalf("connect test database: %v", err)
	}
	tenantID := uuid.New()
	if _, err := pool.Exec(ctx, "INSERT INTO tenants (id, name) VALUES ($1, $2)", tenantID, "go-relay-test-"+tenantID.String()); err != nil {
		pool.Close()
		cancel()
		t.Fatalf("insert tenant: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cleanupCancel()
		_, _ = pool.Exec(cleanupCtx, "DELETE FROM outbox_events WHERE tenant_id = $1", tenantID)
		_, _ = pool.Exec(cleanupCtx, "DELETE FROM tenants WHERE id = $1", tenantID)
		pool.Close()
		cancel()
	})
	return relayIntegrationFixture{ctx: ctx, pool: pool, tenantID: tenantID}
}

func (fixture relayIntegrationFixture) insertEvents(t *testing.T, count int) {
	t.Helper()
	for index := 0; index < count; index++ {
		if _, err := fixture.pool.Exec(fixture.ctx, `INSERT INTO outbox_events
            (id, tenant_id, aggregate_id, event_type, payload, status, attempts)
            VALUES ($1, $2, $3, 'relay.test', $4::jsonb, 'pending', 0)`,
			uuid.New(), fixture.tenantID, uuid.New(), fmt.Sprintf(`{"index":%d}`, index)); err != nil {
			t.Fatalf("insert event %d: %v", index, err)
		}
	}
}

func TestMultipleRelayInstancesClaimWithoutDuplicates(t *testing.T) {
	fixture := newRelayIntegrationFixture(t)
	const (
		eventCount    = 80
		relayCount    = 4
		claimPerRelay = eventCount / relayCount
	)
	fixture.insertEvents(t, eventCount)

	claimed := make(chan []Event, relayCount)
	errors := make(chan error, relayCount)
	var workers sync.WaitGroup
	for worker := 0; worker < relayCount; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			// Separate Repository values model independent Relay processes sharing PostgreSQL.
			events, err := NewRepository(fixture.pool).Claim(fixture.ctx, claimPerRelay, time.Minute)
			claimed <- events
			errors <- err
		}()
	}
	workers.Wait()
	close(claimed)
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatalf("concurrent claim: %v", err)
		}
	}

	seen := make(map[uuid.UUID]struct{}, eventCount)
	for events := range claimed {
		for _, event := range events {
			if _, duplicate := seen[event.ID]; duplicate {
				t.Fatalf("event %s was claimed twice", event.ID)
			}
			seen[event.ID] = struct{}{}
			if event.Attempts != 1 {
				t.Fatalf("event %s attempts = %d, want 1", event.ID, event.Attempts)
			}
		}
	}
	if len(seen) != eventCount {
		t.Fatalf("claimed %d unique events, want %d", len(seen), eventCount)
	}
}

func TestExpiredLeaseCanBeReclaimed(t *testing.T) {
	fixture := newRelayIntegrationFixture(t)
	fixture.insertEvents(t, 1)
	firstRelay := NewRepository(fixture.pool)
	secondRelay := NewRepository(fixture.pool)

	first, err := firstRelay.Claim(fixture.ctx, 1, 100*time.Millisecond)
	if err != nil || len(first) != 1 {
		t.Fatalf("first claim returned %d events, error %v", len(first), err)
	}
	blocked, err := secondRelay.Claim(fixture.ctx, 1, time.Minute)
	if err != nil || len(blocked) != 0 {
		t.Fatalf("active lease should block reclaim; got %d events, error %v", len(blocked), err)
	}

	time.Sleep(150 * time.Millisecond)
	reclaimed, err := secondRelay.Claim(fixture.ctx, 1, time.Minute)
	if err != nil || len(reclaimed) != 1 {
		t.Fatalf("expired lease returned %d events, error %v", len(reclaimed), err)
	}
	if reclaimed[0].ID != first[0].ID || reclaimed[0].Attempts != 2 {
		t.Fatalf("reclaimed event = %s attempts=%d, want %s attempts=2", reclaimed[0].ID, reclaimed[0].Attempts, first[0].ID)
	}
}
