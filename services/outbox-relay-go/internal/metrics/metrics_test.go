package metrics

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
)

func TestEventOutcomesAreExposedBeforeFirstDelivery(t *testing.T) {
	registry := prometheus.NewRegistry()
	New(registry)

	families, err := registry.Gather()
	if err != nil {
		t.Fatalf("gather metrics: %v", err)
	}
	for _, family := range families {
		if family.GetName() != "hireflow_outbox_relay_events_total" {
			continue
		}
		if got := len(family.Metric); got != 3 {
			t.Fatalf("event outcome series = %d, want 3", got)
		}
		return
	}
	t.Fatal("event outcome metric was not registered")
}

func TestBrokerDisconnectMetricIsExposed(t *testing.T) {
	registry := prometheus.NewRegistry()
	relayMetrics := New(registry)
	relayMetrics.BrokerDisconnects.Inc()

	families, err := registry.Gather()
	if err != nil {
		t.Fatalf("gather metrics: %v", err)
	}
	for _, family := range families {
		if family.GetName() == "hireflow_outbox_relay_broker_disconnects_total" {
			if got := family.Metric[0].Counter.GetValue(); got != 1 {
				t.Fatalf("disconnect counter = %v, want 1", got)
			}
			return
		}
	}
	t.Fatal("disconnect metric was not registered")
}
