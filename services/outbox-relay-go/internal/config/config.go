package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

type Config struct {
	DatabaseURL    string
	RabbitMQURL    string
	HTTPAddress    string
	BatchSize      int
	PollInterval   time.Duration
	LeaseDuration  time.Duration
	PublishTimeout time.Duration
}

func Load() (Config, error) {
	cfg := Config{
		DatabaseURL:    env("HIREFLOW_DATABASE_URL", "postgresql://hireflow:hireflow_dev_only@localhost:5432/hireflow"),
		RabbitMQURL:    env("HIREFLOW_RABBITMQ_URL", "amqp://hireflow:hireflow_dev_only@localhost/"),
		HTTPAddress:    env("HIREFLOW_HTTP_ADDRESS", ":9101"),
		BatchSize:      100,
		PollInterval:   time.Second,
		LeaseDuration:  time.Minute,
		PublishTimeout: 5 * time.Second,
	}

	var err error
	if cfg.BatchSize, err = positiveInt("HIREFLOW_OUTBOX_BATCH_SIZE", cfg.BatchSize); err != nil {
		return Config{}, err
	}
	if cfg.PollInterval, err = positiveDuration("HIREFLOW_OUTBOX_POLL_INTERVAL", cfg.PollInterval); err != nil {
		return Config{}, err
	}
	if cfg.LeaseDuration, err = positiveDuration("HIREFLOW_OUTBOX_LEASE_DURATION", cfg.LeaseDuration); err != nil {
		return Config{}, err
	}
	if cfg.PublishTimeout, err = positiveDuration("HIREFLOW_PUBLISH_TIMEOUT", cfg.PublishTimeout); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func positiveInt(name string, fallback int) (int, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 1 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return parsed, nil
}

func positiveDuration(name string, fallback time.Duration) (time.Duration, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 {
		return 0, fmt.Errorf("%s must be a positive duration", name)
	}
	return parsed, nil
}
