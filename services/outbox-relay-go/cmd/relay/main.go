package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"

	"github.com/zhangbeimiao/HireFlow/services/outbox-relay-go/internal/broker"
	"github.com/zhangbeimiao/HireFlow/services/outbox-relay-go/internal/config"
	"github.com/zhangbeimiao/HireFlow/services/outbox-relay-go/internal/health"
	"github.com/zhangbeimiao/HireFlow/services/outbox-relay-go/internal/metrics"
	"github.com/zhangbeimiao/HireFlow/services/outbox-relay-go/internal/outbox"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	cfg, err := config.Load()
	if err != nil {
		logger.Error("invalid configuration", "error", err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	pool, err := pgxpool.New(ctx, cfg.DatabaseURL)
	if err != nil {
		logger.Error("create PostgreSQL pool", "error", err)
		os.Exit(1)
	}
	defer pool.Close()

	repository := outbox.NewRepository(pool)
	relayMetrics := metrics.New(prometheus.DefaultRegisterer)
	healthServer := health.New(cfg.HTTPAddress, pool)
	go func() {
		if err := healthServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("health server stopped", "error", err)
			stop()
		}
	}()

	logger.Info("Go outbox relay started", "http_address", cfg.HTTPAddress, "batch_size", cfg.BatchSize)
	run(ctx, logger, cfg, repository, relayMetrics, healthServer)
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := healthServer.Shutdown(shutdownCtx); err != nil {
		logger.Warn("health server shutdown", "error", err)
	}
	logger.Info("Go outbox relay stopped")
}

func run(ctx context.Context, logger *slog.Logger, cfg config.Config, repository *outbox.Repository, relayMetrics *metrics.Metrics, healthServer *health.Server) {
	var publisher *broker.Publisher
	var brokerGeneration atomic.Uint64
	defer func() {
		if publisher != nil {
			_ = publisher.Close()
		}
	}()

	for ctx.Err() == nil {
		if publisher == nil {
			connected, err := broker.Connect(cfg.RabbitMQURL)
			if err != nil {
				healthServer.SetBrokerReady(false)
				logger.Warn("RabbitMQ unavailable; retrying", "error", err)
				wait(ctx, cfg.PollInterval)
				continue
			}
			publisher = connected
			healthServer.SetBrokerReady(true)
			generation := brokerGeneration.Add(1)
			go func(active *broker.Publisher, activeGeneration uint64) {
				<-active.NotifyClose()
				if brokerGeneration.Load() == activeGeneration {
					healthServer.SetBrokerReady(false)
				}
			}(publisher, generation)
			logger.Info("RabbitMQ publisher confirm channel ready")
		}

		events, err := repository.Claim(ctx, cfg.BatchSize, cfg.LeaseDuration)
		if err != nil {
			logger.Warn("outbox claim failed", "error", err)
			wait(ctx, cfg.PollInterval)
			continue
		}
		relayMetrics.Claims.Add(float64(len(events)))
		for index, event := range events {
			if ctx.Err() != nil {
				releaseEvents(logger, repository, events[index:])
				return
			}
			publishCtx, cancel := context.WithTimeout(context.Background(), cfg.PublishTimeout)
			started := time.Now()
			err := publisher.Publish(publishCtx, event)
			cancel()
			relayMetrics.PublishDuration.Observe(time.Since(started).Seconds())
			if err != nil {
				relayMetrics.Events.WithLabelValues("retry").Inc()
				logger.Warn("event publish was not confirmed", "event_id", event.ID, "error", err)
				releaseEvents(logger, repository, events[index:])
				_ = publisher.Close()
				publisher = nil
				healthServer.SetBrokerReady(false)
				break
			}

			updateCtx, updateCancel := context.WithTimeout(context.Background(), 5*time.Second)
			err = repository.MarkPublished(updateCtx, event.ID)
			updateCancel()
			if err != nil {
				relayMetrics.Events.WithLabelValues("state_update_failure").Inc()
				logger.Warn("confirmed event remains recoverable by lease", "event_id", event.ID, "error", err)
			} else {
				relayMetrics.Events.WithLabelValues("published").Inc()
			}
		}

		metricsCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		if counts, err := repository.Backlog(metricsCtx); err == nil {
			relayMetrics.SetBacklog(counts)
		} else {
			logger.Warn("refresh backlog metrics", "error", err)
		}
		cancel()
		wait(ctx, cfg.PollInterval)
	}
}

func releaseEvents(logger *slog.Logger, repository *outbox.Repository, events []outbox.Event) {
	releaseCtx, releaseCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer releaseCancel()
	for _, event := range events {
		if err := repository.MarkFailed(releaseCtx, event.ID, event.Attempts); err != nil {
			logger.Warn("event remains protected by lease", "event_id", event.ID, "error", err)
		}
	}
}

func wait(ctx context.Context, duration time.Duration) {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}
