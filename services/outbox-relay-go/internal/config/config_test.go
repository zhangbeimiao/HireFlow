package config

import (
	"testing"
	"time"
)

func TestLoadDefaults(t *testing.T) {
	for _, name := range []string{
		"HIREFLOW_DATABASE_URL", "HIREFLOW_RABBITMQ_URL", "HIREFLOW_HTTP_ADDRESS",
		"HIREFLOW_OUTBOX_BATCH_SIZE", "HIREFLOW_OUTBOX_POLL_INTERVAL",
		"HIREFLOW_OUTBOX_LEASE_DURATION", "HIREFLOW_PUBLISH_TIMEOUT",
	} {
		t.Setenv(name, "")
	}
	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if cfg.BatchSize != 100 || cfg.PollInterval != time.Second || cfg.LeaseDuration != time.Minute {
		t.Fatalf("unexpected defaults: %+v", cfg)
	}
}

func TestLoadRejectsInvalidValues(t *testing.T) {
	t.Setenv("HIREFLOW_OUTBOX_BATCH_SIZE", "0")
	if _, err := Load(); err == nil {
		t.Fatal("Load() accepted an invalid batch size")
	}
}
