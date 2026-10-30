package broker

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/zhangbeimiao/HireFlow/services/outbox-relay-go/internal/outbox"
)

func integrationPublisher(t *testing.T) *Publisher {
	t.Helper()
	rabbitURL := os.Getenv("HIREFLOW_TEST_RABBITMQ_URL")
	if rabbitURL == "" {
		t.Skip("HIREFLOW_TEST_RABBITMQ_URL is not configured")
	}
	publisher, err := Connect(rabbitURL)
	if err != nil {
		t.Fatalf("connect test publisher: %v", err)
	}
	t.Cleanup(func() { _ = publisher.Close() })
	return publisher
}

func integrationEvent() outbox.Event {
	return outbox.Event{
		ID: uuid.New(), TenantID: uuid.New(), AggregateID: uuid.New(),
		EventType: "relay.integration", Payload: json.RawMessage(`{"probe":true}`),
	}
}

func TestPublishFailsAfterChannelClosure(t *testing.T) {
	publisher := integrationPublisher(t)
	if err := publisher.channel.Close(); err != nil {
		t.Fatalf("close channel: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := publisher.Publish(ctx, integrationEvent())
	if err == nil || !strings.Contains(err.Error(), "publish event") {
		t.Fatalf("publish after channel close error = %v", err)
	}
}

func TestPublishFailsAfterConnectionClosure(t *testing.T) {
	publisher := integrationPublisher(t)
	if err := publisher.connection.Close(); err != nil {
		t.Fatalf("close connection: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := publisher.Publish(ctx, integrationEvent())
	if err == nil || !strings.Contains(err.Error(), "publish event") {
		t.Fatalf("publish after connection close error = %v", err)
	}
}

func TestConnectRejectsUnavailableBroker(t *testing.T) {
	_, err := Connect("amqp://guest:guest@127.0.0.1:1/")
	if err == nil || !strings.Contains(err.Error(), "connect RabbitMQ") {
		t.Fatalf("unavailable broker error = %v", err)
	}
}
