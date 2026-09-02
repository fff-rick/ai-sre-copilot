// Package app implements all four testbed service roles in one small binary.
package app

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"sync/atomic"
	"time"

	"ai-sre-copilot.local/testbed/internal/config"
	"ai-sre-copilot.local/testbed/internal/fault"
	"ai-sre-copilot.local/testbed/internal/model"
	"ai-sre-copilot.local/testbed/internal/telemetry"
	"github.com/jackc/pgx/v5/pgxpool"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/metric"
)

const controlToken = "stage1-local"

// App wires one configured role to its dependencies.
type App struct {
	config       config.Config
	logger       *slog.Logger
	client       *http.Client
	database     *pgxpool.Pool
	faults       *fault.Manager
	requestCount metric.Int64Counter
	requestTime  metric.Float64Histogram
	poolFailures metric.Int64Counter
	poolWaitTime metric.Float64Histogram
	orders       atomic.Uint64
}

// New builds an instrumented handler for api, order, inventory, or payment.
func New(cfg config.Config, logger *slog.Logger, database *pgxpool.Pool, faults *fault.Manager) (http.Handler, error) {
	meter := otel.Meter("ai-sre-testbed/http")
	requestCount, err := meter.Int64Counter("testbed.http.server.requests")
	if err != nil {
		return nil, err
	}
	requestTime, err := meter.Float64Histogram("testbed.http.server.duration", metric.WithUnit("ms"))
	if err != nil {
		return nil, err
	}
	poolFailures, err := meter.Int64Counter("testbed.db.pool.exhaustions")
	if err != nil {
		return nil, err
	}
	poolWaitTime, err := meter.Float64Histogram("testbed.db.pool.wait.duration", metric.WithUnit("ms"))
	if err != nil {
		return nil, err
	}

	application := &App{
		config:       cfg,
		logger:       logger,
		client:       &http.Client{Transport: otelhttp.NewTransport(http.DefaultTransport), Timeout: 5 * time.Second},
		database:     database,
		faults:       faults,
		requestCount: requestCount,
		requestTime:  requestTime,
		poolFailures: poolFailures,
		poolWaitTime: poolWaitTime,
	}
	if err := application.registerObservableMetrics(meter); err != nil {
		return nil, err
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", application.live)
	mux.HandleFunc("GET /health/ready", application.ready)
	mux.HandleFunc("GET /_test/fault", application.faultControl)
	mux.HandleFunc("POST /_test/fault", application.faultControl)
	mux.HandleFunc("DELETE /_test/fault", application.faultControl)

	switch cfg.ServiceName {
	case "api":
		mux.Handle("POST /checkout", application.business("checkout", http.HandlerFunc(application.checkout)))
	case "order":
		mux.Handle("POST /orders", application.business("create_order", http.HandlerFunc(application.createOrder)))
	case "inventory":
		if database == nil {
			return nil, errors.New("inventory service requires a database pool")
		}
		mux.Handle("POST /reserve", application.business("reserve_inventory", http.HandlerFunc(application.reserve)))
	case "payment":
		mux.Handle("POST /charge", application.business("charge", http.HandlerFunc(application.charge)))
	default:
		return nil, fmt.Errorf("unsupported service role: %s", cfg.ServiceName)
	}

	handler := application.observe(mux)
	return otelhttp.NewHandler(handler, cfg.ServiceName+".http"), nil
}

func (a *App) live(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"service": a.config.ServiceName, "status": "ok"})
}

func (a *App) ready(w http.ResponseWriter, request *http.Request) {
	if a.database != nil {
		ctx, cancel := context.WithTimeout(request.Context(), time.Second)
		defer cancel()
		if err := a.database.Ping(ctx); err != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "not_ready"})
			return
		}
	}
	writeJSON(w, http.StatusOK, map[string]string{"service": a.config.ServiceName, "status": "ready"})
}

func (a *App) faultControl(w http.ResponseWriter, request *http.Request) {
	if request.Header.Get("X-Testbed-Control") != controlToken {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "testbed control token required"})
		return
	}

	switch request.Method {
	case http.MethodGet:
		active, ok := a.faults.Active()
		if !ok {
			writeJSON(w, http.StatusOK, map[string]string{"status": "clear"})
			return
		}
		writeJSON(w, http.StatusOK, active)
	case http.MethodPost:
		var spec fault.Spec
		if err := decodeJSON(request.Body, &spec); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		if spec.Type == "clear" {
			a.faults.Clear()
			a.logger.InfoContext(request.Context(), "fault recovered", telemetry.TraceAttributes(request.Context())...)
			writeJSON(w, http.StatusOK, map[string]string{"status": "clear"})
			return
		}
		if err := a.validateFaultTarget(spec); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		active, err := a.faults.Apply(spec)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		a.logger.WarnContext(request.Context(), "fault activated", append(telemetry.TraceAttributes(request.Context()), faultLogAttributes(active)...)...)
		writeJSON(w, http.StatusAccepted, active)
	case http.MethodDelete:
		a.faults.Clear()
		a.logger.InfoContext(request.Context(), "fault recovered", telemetry.TraceAttributes(request.Context())...)
		writeJSON(w, http.StatusOK, map[string]string{"status": "clear"})
	}
}

func (a *App) checkout(w http.ResponseWriter, request *http.Request) {
	var input model.CheckoutRequest
	if err := decodeCheckout(request.Body, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	var output model.CheckoutResponse
	if err := a.postJSON(request.Context(), "order", a.config.OrderURL+"/orders", input, &output); err != nil {
		a.downstreamError(w, request, "order", err)
		return
	}
	writeJSON(w, http.StatusCreated, output)
}

func (a *App) createOrder(w http.ResponseWriter, request *http.Request) {
	var input model.CheckoutRequest
	if err := decodeCheckout(request.Body, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}

	var inventory model.InventoryResponse
	if err := a.postJSON(request.Context(), "inventory", a.config.InventoryURL+"/reserve", input, &inventory); err != nil {
		a.downstreamError(w, request, "inventory", err)
		return
	}
	var payment model.PaymentResponse
	if err := a.postJSON(request.Context(), "payment", a.config.PaymentURL+"/charge", input, &payment); err != nil {
		a.downstreamError(w, request, "payment", err)
		return
	}

	orderID := fmt.Sprintf("ord-%06d", a.orders.Add(1))
	a.logger.InfoContext(request.Context(), "order created", append(telemetry.TraceAttributes(request.Context()), "order_id", orderID, "sku", input.SKU)...)
	writeJSON(w, http.StatusCreated, model.CheckoutResponse{OrderID: orderID, Status: "confirmed"})
}

func (a *App) reserve(w http.ResponseWriter, request *http.Request) {
	var input model.CheckoutRequest
	if err := decodeCheckout(request.Body, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}

	ctx, span := otel.Tracer("ai-sre-testbed/database").Start(request.Context(), "inventory.select")
	defer span.End()
	span.SetAttributes(attribute.String("db.system.name", "postgresql"), attribute.String("db.namespace", "testbed"))
	if spec, ok := a.activeFault("connection_pool"); ok {
		if err := a.exhaustConnectionPool(ctx, spec); err != nil {
			span.RecordError(err)
			span.SetStatus(codes.Error, "database connection pool exhausted")
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "database_connection_pool_exhausted"})
			return
		}
	}
	var available int
	if err := a.database.QueryRow(ctx, "SELECT available FROM inventory WHERE sku = $1", input.SKU).Scan(&available); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "inventory query failed")
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "inventory query failed"})
		return
	}
	if available < input.Quantity {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "insufficient inventory"})
		return
	}
	writeJSON(w, http.StatusOK, model.InventoryResponse{SKU: input.SKU, Remaining: available - input.Quantity})
}

func (a *App) charge(w http.ResponseWriter, request *http.Request) {
	var input model.CheckoutRequest
	if err := decodeCheckout(request.Body, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if spec, ok := a.activeFault("release_regression"); ok && input.SKU == spec.TriggerSKU {
		a.logger.ErrorContext(request.Context(), "release regression triggered", append(telemetry.TraceAttributes(request.Context()), faultLogAttributes(spec)...)...)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "release_regression"})
		return
	}
	writeJSON(w, http.StatusOK, model.PaymentResponse{Status: "authorized"})
}

func (a *App) business(operation string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if err := a.faults.BeforeRequest(request.Context()); err != nil {
			a.logger.ErrorContext(request.Context(), "injected fault interrupted request", append(telemetry.TraceAttributes(request.Context()), "operation", operation, "error", err)...)
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "fault_injected"})
			return
		}
		next.ServeHTTP(w, request)
	})
}

func (a *App) observe(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		started := time.Now()
		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, request)
		duration := time.Since(started)
		attrs := metric.WithAttributes(
			attribute.String("service.name", a.config.ServiceName),
			attribute.String("http.request.method", request.Method),
			attribute.String("url.path", request.URL.Path),
			attribute.Int("http.response.status_code", recorder.status),
		)
		a.requestCount.Add(request.Context(), 1, attrs)
		a.requestTime.Record(request.Context(), float64(duration.Microseconds())/1000, attrs)
		a.logger.InfoContext(request.Context(), "request completed", append(telemetry.TraceAttributes(request.Context()), "method", request.Method, "path", request.URL.Path, "status", recorder.status, "duration_ms", float64(duration.Microseconds())/1000)...)
	})
}

func (a *App) postJSON(ctx context.Context, dependency, endpoint string, input, output any) error {
	endpoint = a.faultedEndpoint(ctx, dependency, endpoint)
	payload, err := json.Marshal(input)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := a.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 1024))
		return fmt.Errorf("status %d: %s", response.StatusCode, body)
	}
	return json.NewDecoder(io.LimitReader(response.Body, 64*1024)).Decode(output)
}

func (a *App) downstreamError(w http.ResponseWriter, request *http.Request, dependency string, err error) {
	a.logger.ErrorContext(request.Context(), "downstream request failed", append(telemetry.TraceAttributes(request.Context()), "dependency", dependency, "error", err)...)
	writeJSON(w, http.StatusBadGateway, map[string]string{"error": dependency + " unavailable"})
}

func decodeCheckout(body io.Reader, output *model.CheckoutRequest) error {
	if err := decodeJSON(body, output); err != nil {
		return err
	}
	if output.SKU == "" || output.Quantity < 1 || output.Quantity > 100 || output.AmountCents < 1 {
		return errors.New("sku, quantity 1..100, and positive amount_cents are required")
	}
	return nil
}

func decodeJSON(body io.Reader, output any) error {
	decoder := json.NewDecoder(io.LimitReader(body, 64*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(output); err != nil {
		return fmt.Errorf("invalid JSON: %w", err)
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}
