package outbox

import (
	"testing"
	"time"
)

func TestBackoffIsExponentialAndBounded(t *testing.T) {
	tests := []struct {
		attempts int
		want     time.Duration
	}{{0, 2 * time.Second}, {1, 2 * time.Second}, {2, 4 * time.Second}, {8, 256 * time.Second}, {9, 300 * time.Second}, {100, 300 * time.Second}}
	for _, test := range tests {
		if got := Backoff(test.attempts); got != test.want {
			t.Fatalf("Backoff(%d) = %s, want %s", test.attempts, got, test.want)
		}
	}
}
