package broker

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/rabbitmq/amqp091-go"
)

func TestAwaitConfirmationACK(t *testing.T) {
	confirms := make(chan amqp091.Confirmation, 1)
	confirms <- amqp091.Confirmation{Ack: true}
	if err := awaitConfirmation(context.Background(), confirms); err != nil {
		t.Fatalf("ACK returned error: %v", err)
	}
}

func TestAwaitConfirmationNACK(t *testing.T) {
	confirms := make(chan amqp091.Confirmation, 1)
	confirms <- amqp091.Confirmation{Ack: false}
	err := awaitConfirmation(context.Background(), confirms)
	if err == nil || !strings.Contains(err.Error(), "negatively acknowledged") {
		t.Fatalf("NACK error = %v", err)
	}
}

func TestAwaitConfirmationClosedChannel(t *testing.T) {
	confirms := make(chan amqp091.Confirmation)
	close(confirms)
	err := awaitConfirmation(context.Background(), confirms)
	if err == nil || !strings.Contains(err.Error(), "confirm channel closed") {
		t.Fatalf("closed confirm channel error = %v", err)
	}
}

func TestAwaitConfirmationContextTimeout(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	err := awaitConfirmation(ctx, make(chan amqp091.Confirmation))
	if err == nil || !strings.Contains(err.Error(), "deadline exceeded") {
		t.Fatalf("timeout error = %v", err)
	}
}
