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
	if err := awaitPublishOutcome(context.Background(), confirms, make(chan amqp091.Return)); err != nil {
		t.Fatalf("ACK returned error: %v", err)
	}
}

func TestAwaitConfirmationNACK(t *testing.T) {
	confirms := make(chan amqp091.Confirmation, 1)
	confirms <- amqp091.Confirmation{Ack: false}
	err := awaitPublishOutcome(context.Background(), confirms, make(chan amqp091.Return))
	if err == nil || !strings.Contains(err.Error(), "negatively acknowledged") {
		t.Fatalf("NACK error = %v", err)
	}
}

func TestAwaitConfirmationClosedChannel(t *testing.T) {
	confirms := make(chan amqp091.Confirmation)
	close(confirms)
	err := awaitPublishOutcome(context.Background(), confirms, make(chan amqp091.Return))
	if err == nil || !strings.Contains(err.Error(), "confirm channel closed") {
		t.Fatalf("closed confirm channel error = %v", err)
	}
}

func TestAwaitConfirmationContextTimeout(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	err := awaitPublishOutcome(ctx, make(chan amqp091.Confirmation), make(chan amqp091.Return))
	if err == nil || !strings.Contains(err.Error(), "deadline exceeded") {
		t.Fatalf("timeout error = %v", err)
	}
}

func TestAwaitConfirmationRejectsReturnedMessage(t *testing.T) {
	confirms := make(chan amqp091.Confirmation, 1)
	returns := make(chan amqp091.Return, 1)
	returns <- amqp091.Return{ReplyCode: 312, ReplyText: "NO_ROUTE", RoutingKey: "missing.queue"}
	confirms <- amqp091.Confirmation{Ack: true}
	err := awaitPublishOutcome(context.Background(), confirms, returns)
	if err == nil || !strings.Contains(err.Error(), "unroutable") {
		t.Fatalf("returned message error = %v", err)
	}
}
