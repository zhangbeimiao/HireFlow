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

func TestConcurrentClaimUsesSkipLockedWithoutDuplicates(t *testing.T) {
	databaseURL := os.Getenv("HIREFLOW_TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("HIREFLOW_TEST_DATABASE_URL is not configured")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("connect test database: %v", err)
	}
	defer pool.Close()

	tenantID := uuid.New()
	if _, err := pool.Exec(ctx, "INSERT INTO tenants (id, name) VALUES ($1, $2)", tenantID, "go-relay-test-"+tenantID.String()); err != nil {
		t.Fatalf("insert tenant: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cleanupCancel()
		_, _ = pool.Exec(cleanupCtx, "DELETE FROM outbox_events WHERE tenant_id = $1", tenantID)
		_, _ = pool.Exec(cleanupCtx, "DELETE FROM tenants WHERE id = $1", tenantID)
	})

	const eventCount = 20
	for index := 0; index < eventCount; index++ {
		if _, err := pool.Exec(ctx, `INSERT INTO outbox_events
            (id, tenant_id, aggregate_id, event_type, payload, status, attempts)
            VALUES ($1, $2, $3, 'relay.test', $4::jsonb, 'pending', 0)`,
			uuid.New(), tenantID, uuid.New(), fmt.Sprintf(`{"index":%d}`, index)); err != nil {
			t.Fatalf("insert event %d: %v", index, err)
		}
	}

	repository := NewRepository(pool)
	claimed := make(chan []Event, 2)
	errors := make(chan error, 2)
	var workers sync.WaitGroup
	for worker := 0; worker < 2; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			events, err := repository.Claim(ctx, eventCount/2, time.Minute)
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
