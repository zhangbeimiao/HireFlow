package metrics

import "github.com/prometheus/client_golang/prometheus"

type Metrics struct {
	Events            *prometheus.CounterVec
	Backlog           *prometheus.GaugeVec
	PublishDuration   prometheus.Histogram
	Claims            prometheus.Counter
	BrokerDisconnects prometheus.Counter
}

func New(registry prometheus.Registerer) *Metrics {
	metrics := &Metrics{
		Events: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "hireflow_outbox_relay_events_total", Help: "Outbox relay events by durable publish outcome.",
		}, []string{"outcome"}),
		Backlog: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "hireflow_outbox_backlog", Help: "Persisted outbox event backlog by state.",
		}, []string{"state"}),
		PublishDuration: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name: "hireflow_outbox_publish_duration_seconds", Help: "RabbitMQ publish and confirm latency.",
		}),
		Claims: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "hireflow_outbox_claimed_total", Help: "Outbox rows atomically claimed by this relay.",
		}),
		BrokerDisconnects: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "hireflow_outbox_relay_broker_disconnects_total", Help: "RabbitMQ connection closures observed by this relay.",
		}),
	}
	registry.MustRegister(metrics.Events, metrics.Backlog, metrics.PublishDuration, metrics.Claims, metrics.BrokerDisconnects)
	for _, outcome := range []string{"published", "retry", "state_update_failure"} {
		metrics.Events.WithLabelValues(outcome)
	}
	return metrics
}

func (m *Metrics) SetBacklog(counts map[string]float64) {
	for _, state := range []string{"pending", "publishing", "retry"} {
		m.Backlog.WithLabelValues(state).Set(counts[state])
	}
}
