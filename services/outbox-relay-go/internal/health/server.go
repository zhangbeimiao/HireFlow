package health

import (
	"context"
	"encoding/json"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type Server struct {
	server      *http.Server
	pool        *pgxpool.Pool
	brokerReady atomic.Bool
}

func New(address string, pool *pgxpool.Pool) *Server {
	health := &Server{pool: pool}
	mux := http.NewServeMux()
	mux.HandleFunc("/live", health.live)
	mux.HandleFunc("/ready", health.ready)
	mux.Handle("/metrics", promhttp.Handler())
	health.server = &http.Server{Addr: address, Handler: mux, ReadHeaderTimeout: 3 * time.Second}
	return health
}

func (s *Server) SetBrokerReady(ready bool) { s.brokerReady.Store(ready) }
func (s *Server) ListenAndServe() error     { return s.server.ListenAndServe() }
func (s *Server) Shutdown(ctx context.Context) error {
	return s.server.Shutdown(ctx)
}

func (s *Server) live(response http.ResponseWriter, _ *http.Request) {
	writeJSON(response, http.StatusOK, map[string]any{"status": "alive"})
}

func (s *Server) ready(response http.ResponseWriter, request *http.Request) {
	ctx, cancel := context.WithTimeout(request.Context(), time.Second)
	defer cancel()
	databaseReady := s.pool.Ping(ctx) == nil
	brokerReady := s.brokerReady.Load()
	status, overall := http.StatusOK, "ready"
	if !databaseReady || !brokerReady {
		status, overall = http.StatusServiceUnavailable, "not_ready"
	}
	writeJSON(response, status, map[string]any{
		"status":       overall,
		"dependencies": map[string]string{"postgres": state(databaseReady), "rabbitmq": state(brokerReady)},
	})
}

func state(ready bool) string {
	if ready {
		return "ok"
	}
	return "unavailable"
}

func writeJSON(response http.ResponseWriter, status int, body map[string]any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(body)
}
