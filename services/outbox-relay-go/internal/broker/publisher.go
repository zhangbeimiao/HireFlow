package broker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"

	"github.com/rabbitmq/amqp091-go"

	"github.com/zhangbeimiao/HireFlow/services/outbox-relay-go/internal/outbox"
)

const (
	EventQueue = "hireflow.events"
	RetryQueue = "hireflow.events.retry"
	DLQ        = "hireflow.events.dlq"
)

type Publisher struct {
	connection *amqp091.Connection
	channel    *amqp091.Channel
	confirms   <-chan amqp091.Confirmation
	returns    <-chan amqp091.Return
	routingKey string
	mu         sync.Mutex
}

type envelope struct {
	ID        string          `json:"id"`
	TenantID  string          `json:"tenant_id"`
	EventType string          `json:"event_type"`
	Payload   json.RawMessage `json:"payload"`
}

func Connect(url string) (*Publisher, error) {
	connection, err := amqp091.Dial(url)
	if err != nil {
		return nil, fmt.Errorf("connect RabbitMQ: %w", err)
	}
	channel, err := connection.Channel()
	if err != nil {
		_ = connection.Close()
		return nil, fmt.Errorf("open RabbitMQ channel: %w", err)
	}
	publisher := &Publisher{connection: connection, channel: channel, routingKey: EventQueue}
	if err := publisher.declareTopology(); err != nil {
		_ = publisher.Close()
		return nil, err
	}
	if err := channel.Confirm(false); err != nil {
		_ = publisher.Close()
		return nil, fmt.Errorf("enable publisher confirms: %w", err)
	}
	publisher.confirms = channel.NotifyPublish(make(chan amqp091.Confirmation, 1))
	publisher.returns = channel.NotifyReturn(make(chan amqp091.Return, 1))
	return publisher, nil
}

func (p *Publisher) declareTopology() error {
	if _, err := p.channel.QueueDeclare(DLQ, true, false, false, false, nil); err != nil {
		return fmt.Errorf("declare dead-letter queue: %w", err)
	}
	if _, err := p.channel.QueueDeclare(RetryQueue, true, false, false, false, amqp091.Table{
		"x-message-ttl":             int32(5000),
		"x-dead-letter-exchange":    "",
		"x-dead-letter-routing-key": EventQueue,
	}); err != nil {
		return fmt.Errorf("declare retry queue: %w", err)
	}
	if _, err := p.channel.QueueDeclare(EventQueue, true, false, false, false, nil); err != nil {
		return fmt.Errorf("declare event queue: %w", err)
	}
	return nil
}

func (p *Publisher) Publish(ctx context.Context, event outbox.Event) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	body, err := json.Marshal(envelope{
		ID: event.ID.String(), TenantID: event.TenantID.String(), EventType: event.EventType, Payload: event.Payload,
	})
	if err != nil {
		return fmt.Errorf("encode event: %w", err)
	}
	if err := p.channel.PublishWithContext(ctx, "", p.routingKey, true, false, amqp091.Publishing{
		ContentType: "application/json", DeliveryMode: amqp091.Persistent,
		MessageId: event.ID.String(), Type: event.EventType, Body: body,
	}); err != nil {
		return fmt.Errorf("publish event: %w", err)
	}
	return awaitPublishOutcome(ctx, p.confirms, p.returns)
}

func awaitPublishOutcome(ctx context.Context, confirms <-chan amqp091.Confirmation, returns <-chan amqp091.Return) error {
	var returned *amqp091.Return
	for {
		select {
		case message, ok := <-returns:
			if !ok {
				returns = nil
				continue
			}
			returned = &message
		case confirmation, ok := <-confirms:
			if !ok {
				return errors.New("publisher confirm channel closed")
			}
			if !confirmation.Ack {
				return errors.New("broker negatively acknowledged event")
			}
			// RabbitMQ sends basic.return before the confirm for a mandatory
			// unroutable publish. Drain a return already queued in the client in
			// case both notification channels became ready together.
			if returned == nil && returns != nil {
				select {
				case message, ok := <-returns:
					if ok {
						returned = &message
					}
				default:
				}
			}
			if returned != nil {
				return fmt.Errorf("broker returned unroutable event: code=%d text=%s routing_key=%s", returned.ReplyCode, returned.ReplyText, returned.RoutingKey)
			}
			return nil
		case <-ctx.Done():
			if returned != nil {
				return fmt.Errorf("broker returned unroutable event before confirm timeout: code=%d text=%s routing_key=%s", returned.ReplyCode, returned.ReplyText, returned.RoutingKey)
			}
			return fmt.Errorf("wait for publisher confirm: %w", ctx.Err())
		}
	}
}

func (p *Publisher) NotifyClose() <-chan *amqp091.Error {
	return p.connection.NotifyClose(make(chan *amqp091.Error, 1))
}

func (p *Publisher) Close() error {
	if p.channel != nil && !p.channel.IsClosed() {
		_ = p.channel.Close()
	}
	if p.connection != nil && !p.connection.IsClosed() {
		return p.connection.Close()
	}
	return nil
}
