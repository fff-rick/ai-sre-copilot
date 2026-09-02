package app

import (
	"bytes"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"ai-sre-copilot.local/testbed/internal/config"
	"ai-sre-copilot.local/testbed/internal/fault"
)

func TestAPIForwardsSuccessfulCheckout(t *testing.T) {
	t.Parallel()

	order := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/orders" {
			http.NotFound(w, request)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"order_id":"ord-000001","status":"confirmed"}`))
	}))
	defer order.Close()

	handler, err := New(
		config.Config{ServiceName: "api", OrderURL: order.URL},
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		nil,
		fault.NewManager(),
	)
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	request := httptest.NewRequest(http.MethodPost, "/checkout", bytes.NewBufferString(`{"sku":"widget-blue","quantity":1,"amount_cents":1299}`))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusCreated {
		t.Fatalf("status = %d, want %d; body=%s", recorder.Code, http.StatusCreated, recorder.Body.String())
	}
}

func TestFaultControlIsProtectedAndInterruptsBusinessRequest(t *testing.T) {
	t.Parallel()

	handler, err := New(
		config.Config{ServiceName: "payment"},
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		nil,
		fault.NewManager(),
	)
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	unauthorized := httptest.NewRequest(http.MethodGet, "/_test/fault", nil)
	unauthorizedRecorder := httptest.NewRecorder()
	handler.ServeHTTP(unauthorizedRecorder, unauthorized)
	if unauthorizedRecorder.Code != http.StatusForbidden {
		t.Fatalf("unauthorized status = %d, want %d", unauthorizedRecorder.Code, http.StatusForbidden)
	}

	activate := httptest.NewRequest(http.MethodPost, "/_test/fault", bytes.NewBufferString(`{"type":"error_rate","duration_seconds":10,"error_rate_percent":100}`))
	activate.Header.Set("X-Testbed-Control", controlToken)
	activateRecorder := httptest.NewRecorder()
	handler.ServeHTTP(activateRecorder, activate)
	if activateRecorder.Code != http.StatusAccepted {
		t.Fatalf("activate status = %d, want %d; body=%s", activateRecorder.Code, http.StatusAccepted, activateRecorder.Body.String())
	}

	charge := httptest.NewRequest(http.MethodPost, "/charge", bytes.NewBufferString(`{"sku":"widget-blue","quantity":1,"amount_cents":1299}`))
	chargeRecorder := httptest.NewRecorder()
	handler.ServeHTTP(chargeRecorder, charge)
	if chargeRecorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("charge status = %d, want %d", chargeRecorder.Code, http.StatusServiceUnavailable)
	}
}

func TestUnknownRoleIsRejected(t *testing.T) {
	t.Parallel()

	_, err := New(
		config.Config{ServiceName: "unknown"},
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		nil,
		fault.NewManager(),
	)
	if err == nil {
		t.Fatal("New() error = nil, want unsupported role error")
	}
}

func TestReleaseRegressionIsScopedToConfiguredSKU(t *testing.T) {
	t.Parallel()

	manager := fault.NewManager()
	defer manager.Clear()
	_, err := manager.Apply(fault.Spec{
		Type:            "release_regression",
		DurationSeconds: 10,
		PreviousVersion: "1.0.0",
		ReleaseVersion:  "1.1.0",
		TriggerSKU:      "widget-red",
	})
	if err != nil {
		t.Fatalf("Apply() error = %v", err)
	}
	handler, err := New(
		config.Config{ServiceName: "payment"},
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		nil,
		manager,
	)
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	regressed := httptest.NewRequest(http.MethodPost, "/charge", bytes.NewBufferString(`{"sku":"widget-red","quantity":1,"amount_cents":1299}`))
	regressedRecorder := httptest.NewRecorder()
	handler.ServeHTTP(regressedRecorder, regressed)
	if regressedRecorder.Code != http.StatusInternalServerError {
		t.Fatalf("regressed status = %d, want %d", regressedRecorder.Code, http.StatusInternalServerError)
	}

	healthy := httptest.NewRequest(http.MethodPost, "/charge", bytes.NewBufferString(`{"sku":"widget-blue","quantity":1,"amount_cents":1299}`))
	healthyRecorder := httptest.NewRecorder()
	handler.ServeHTTP(healthyRecorder, healthy)
	if healthyRecorder.Code != http.StatusOK {
		t.Fatalf("healthy status = %d, want %d", healthyRecorder.Code, http.StatusOK)
	}
}

func TestConfigurationFaultChangesDownstreamPath(t *testing.T) {
	t.Parallel()

	inventory := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"sku": "widget-blue", "remaining": 9})
	}))
	defer inventory.Close()
	payment := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/charge" {
			http.NotFound(w, request)
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "authorized"})
	}))
	defer payment.Close()

	manager := fault.NewManager()
	defer manager.Clear()
	_, err := manager.Apply(fault.Spec{
		Type:            "configuration_error",
		DurationSeconds: 10,
		ConfigKey:       "payment_path",
		ConfigValue:     "/charge-v2",
	})
	if err != nil {
		t.Fatalf("Apply() error = %v", err)
	}
	handler, err := New(
		config.Config{ServiceName: "order", InventoryURL: inventory.URL, PaymentURL: payment.URL},
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		nil,
		manager,
	)
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	request := httptest.NewRequest(http.MethodPost, "/orders", bytes.NewBufferString(`{"sku":"widget-blue","quantity":1,"amount_cents":1299}`))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want %d; body=%s", recorder.Code, http.StatusBadGateway, recorder.Body.String())
	}
}
