package tool

import (
	"context"
	"errors"
	"testing"
)

func TestRegistryExecutesRegisteredTool(t *testing.T) {
	t.Parallel()

	want := Result{Data: map[string]any{"value": 1}, SourceRef: "fake://prometheus.query"}
	registry, err := NewRegistry(Fake{ToolName: "prometheus.query", Result: want})
	if err != nil {
		t.Fatalf("NewRegistry() error = %v", err)
	}

	got, err := registry.ExecuteRead(context.Background(), "prometheus.query", Request{InvestigationID: "inv-1"})
	if err != nil {
		t.Fatalf("ExecuteRead() error = %v", err)
	}
	if got.SourceRef != want.SourceRef {
		t.Fatalf("source ref = %q, want %q", got.SourceRef, want.SourceRef)
	}
}

func TestRegistryRejectsUnknownTool(t *testing.T) {
	t.Parallel()

	registry, err := NewRegistry()
	if err != nil {
		t.Fatalf("NewRegistry() error = %v", err)
	}

	_, err = registry.ExecuteRead(context.Background(), "shell.exec", Request{})
	if !errors.Is(err, ErrNotRegistered) {
		t.Fatalf("ExecuteRead() error = %v, want ErrNotRegistered", err)
	}
}

func TestRegistryRejectsDuplicateNames(t *testing.T) {
	t.Parallel()

	_, err := NewRegistry(Fake{ToolName: "logs.query"}, Fake{ToolName: "logs.query"})
	if err == nil {
		t.Fatal("NewRegistry() error = nil, want duplicate error")
	}
}
