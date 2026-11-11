package main

import (
	"testing"
	"time"
)

func TestShouldRenewLeaseBeforePublishSafetyWindow(t *testing.T) {
	now := time.Now()
	publishTimeout := 5 * time.Second

	if shouldRenewLease(now, now.Add(30*time.Second), publishTimeout) {
		t.Fatal("lease with ample time remaining should not be renewed")
	}
	if !shouldRenewLease(now, now.Add(15*time.Second), publishTimeout) {
		t.Fatal("lease at the three-timeout safety boundary should be renewed")
	}
	if !shouldRenewLease(now, time.Time{}, publishTimeout) {
		t.Fatal("missing lease deadline must trigger renewal")
	}
}
