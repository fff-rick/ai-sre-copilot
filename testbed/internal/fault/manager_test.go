package fault

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestApplyRejectsUnboundedFault(t *testing.T) {
	t.Parallel()

	manager := NewManager()
	_, err := manager.Apply(Spec{Type: "latency", DurationSeconds: 0, LatencyMillis: 100})
	if err == nil {
		t.Fatal("Apply() error = nil, want duration validation error")
	}
}

func TestLatencyExpiresAutomatically(t *testing.T) {
	t.Parallel()

	manager := NewManager()
	now := time.Date(2026, 9, 2, 10, 0, 0, 0, time.UTC)
	manager.now = func() time.Time { return now }
	if _, err := manager.Apply(Spec{Type: "latency", DurationSeconds: 1, LatencyMillis: 1}); err != nil {
		t.Fatalf("Apply() error = %v", err)
	}

	now = now.Add(2 * time.Second)
	if _, ok := manager.Active(); ok {
		t.Fatal("Active() = true after expiry")
	}
}

func TestErrorRateCanFailEveryRequest(t *testing.T) {
	t.Parallel()

	manager := NewManager()
	if _, err := manager.Apply(Spec{Type: "error_rate", DurationSeconds: 10, ErrorRatePercent: 100}); err != nil {
		t.Fatalf("Apply() error = %v", err)
	}

	if err := manager.BeforeRequest(context.Background()); !errors.Is(err, ErrInjected) {
		t.Fatalf("BeforeRequest() error = %v, want ErrInjected", err)
	}
}

func TestResourceFaultsAreBoundedAndReleased(t *testing.T) {
	t.Parallel()

	manager := NewManager()
	defer manager.Clear()
	if _, err := manager.Apply(Spec{Type: "memory_pressure", DurationSeconds: 10, MemoryMegabytes: 1}); err != nil {
		t.Fatalf("Apply(memory_pressure) error = %v", err)
	}
	if usage := manager.Usage(); usage.MemoryBytes != 1<<20 {
		t.Fatalf("memory bytes = %d, want %d", usage.MemoryBytes, 1<<20)
	}

	if _, err := manager.Apply(Spec{Type: "cpu_saturation", DurationSeconds: 10, CPUWorkers: 1}); err != nil {
		t.Fatalf("Apply(cpu_saturation) error = %v", err)
	}
	if usage := manager.Usage(); usage.CPUWorkers != 1 || usage.MemoryBytes != 0 {
		t.Fatalf("usage = %+v, want one CPU worker and released memory", usage)
	}

	manager.Clear()
	if usage := manager.Usage(); usage != (Usage{}) {
		t.Fatalf("usage after Clear() = %+v, want empty", usage)
	}
}

func TestEveryStageOneFaultTypeValidatesRequiredParameters(t *testing.T) {
	t.Parallel()

	tests := []Spec{
		{Type: "connection_pool", DurationSeconds: 10, PoolWaitMillis: 500},
		{Type: "dependency_unavailable", DurationSeconds: 10, Dependency: "payment"},
		{Type: "configuration_error", DurationSeconds: 10, ConfigKey: "payment_path", ConfigValue: "/charge-v2"},
		{Type: "release_regression", DurationSeconds: 10, PreviousVersion: "1.0.0", ReleaseVersion: "1.1.0", TriggerSKU: "widget-red"},
	}
	for _, spec := range tests {
		manager := NewManager()
		if _, err := manager.Apply(spec); err != nil {
			t.Errorf("Apply(%s) error = %v", spec.Type, err)
		}
		manager.Clear()
	}
}

func TestRejectsUnsafeResourceParameters(t *testing.T) {
	t.Parallel()

	manager := NewManager()
	tests := []Spec{
		{Type: "cpu_saturation", DurationSeconds: 10, CPUWorkers: maxCPUWorkers + 1},
		{Type: "memory_pressure", DurationSeconds: 10, MemoryMegabytes: maxMemoryMegabytes + 1},
		{Type: "connection_pool", DurationSeconds: 10, PoolWaitMillis: maxPoolWaitMillis + 1},
	}
	for _, spec := range tests {
		if _, err := manager.Apply(spec); err == nil {
			t.Errorf("Apply(%s) error = nil, want validation error", spec.Type)
		}
	}
}

func TestResourceFaultExpiresWithoutAnotherRequest(t *testing.T) {
	manager := NewManager()
	if _, err := manager.Apply(Spec{Type: "memory_pressure", DurationSeconds: 1, MemoryMegabytes: 1}); err != nil {
		t.Fatalf("Apply() error = %v", err)
	}

	deadline := time.Now().Add(2 * time.Second)
	for manager.Usage().ActiveFaultType != "" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if usage := manager.Usage(); usage != (Usage{}) {
		t.Fatalf("usage after automatic expiry = %+v, want empty", usage)
	}
}
