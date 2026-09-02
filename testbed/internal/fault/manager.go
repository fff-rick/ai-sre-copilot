// Package fault implements bounded, automatically expiring test-only failures.
package fault

import (
	"context"
	"errors"
	"fmt"
	"runtime/debug"
	"sync"
	"sync/atomic"
	"time"
)

// ErrInjected is returned when a configured deterministic request failure fires.
var ErrInjected = errors.New("fault injected")

var cpuSink atomic.Uint64

const (
	maxDurationSeconds = 900
	maxLatencyMillis   = 30_000
	maxCPUWorkers      = 8
	maxMemoryMegabytes = 96
	maxPoolWaitMillis  = 10_000
)

// Spec is the external contract shared by all stage-1 fault families.
type Spec struct {
	ScenarioID       string    `json:"scenario_id,omitempty"`
	Type             string    `json:"type"`
	DurationSeconds  int       `json:"duration_seconds"`
	LatencyMillis    int       `json:"latency_ms,omitempty"`
	ErrorRatePercent int       `json:"error_rate_percent,omitempty"`
	CPUWorkers       int       `json:"cpu_workers,omitempty"`
	MemoryMegabytes  int       `json:"memory_megabytes,omitempty"`
	PoolWaitMillis   int       `json:"pool_wait_ms,omitempty"`
	Dependency       string    `json:"dependency,omitempty"`
	ConfigKey        string    `json:"config_key,omitempty"`
	ConfigValue      string    `json:"config_value,omitempty"`
	PreviousVersion  string    `json:"previous_version,omitempty"`
	ReleaseVersion   string    `json:"release_version,omitempty"`
	TriggerSKU       string    `json:"trigger_sku,omitempty"`
	StartedAt        time.Time `json:"started_at"`
	ExpiresAt        time.Time `json:"expires_at"`
}

// Usage reports resources intentionally retained by the current fault.
type Usage struct {
	CPUWorkers      int
	MemoryBytes     int
	ActiveFaultType string
}

// Manager stores at most one active fault per service and owns its bounded workload.
type Manager struct {
	mu         sync.RWMutex
	active     Spec
	sequence   atomic.Uint64
	now        func() time.Time
	generation uint64
	stop       chan struct{}
	timer      *time.Timer
	memory     []byte
}

// NewManager creates an empty fault manager.
func NewManager() *Manager {
	return &Manager{now: time.Now}
}

// Apply validates and activates a bounded fault, replacing any existing fault.
func (m *Manager) Apply(spec Spec) (Spec, error) {
	if err := validate(spec); err != nil {
		return Spec{}, err
	}

	now := m.now().UTC()
	spec.StartedAt = now
	spec.ExpiresAt = now.Add(time.Duration(spec.DurationSeconds) * time.Second)

	m.mu.Lock()
	hadMemory := m.clearLocked()
	m.generation++
	generation := m.generation
	m.active = spec
	m.sequence.Store(0)
	m.stop = make(chan struct{})

	if spec.MemoryMegabytes > 0 {
		m.memory = make([]byte, spec.MemoryMegabytes<<20)
		for offset := 0; offset < len(m.memory); offset += 4096 {
			m.memory[offset] = byte(offset)
		}
	}
	for range spec.CPUWorkers {
		go burnCPU(m.stop)
	}
	m.timer = time.AfterFunc(time.Duration(spec.DurationSeconds)*time.Second, func() {
		m.expire(generation)
	})
	m.mu.Unlock()

	if hadMemory {
		debug.FreeOSMemory()
	}
	return spec, nil
}

// Clear recovers the service immediately and releases fault-owned resources.
func (m *Manager) Clear() {
	m.mu.Lock()
	hadMemory := m.clearLocked()
	m.mu.Unlock()
	if hadMemory {
		debug.FreeOSMemory()
	}
}

func (m *Manager) clearLocked() bool {
	hadMemory := len(m.memory) > 0
	if m.timer != nil {
		m.timer.Stop()
		m.timer = nil
	}
	if m.stop != nil {
		close(m.stop)
		m.stop = nil
	}
	m.memory = nil
	m.active = Spec{}
	m.sequence.Store(0)
	return hadMemory
}

func (m *Manager) expire(generation uint64) {
	m.mu.Lock()
	if generation != m.generation {
		m.mu.Unlock()
		return
	}
	hadMemory := m.clearLocked()
	m.mu.Unlock()
	if hadMemory {
		debug.FreeOSMemory()
	}
}

// Active returns the current non-expired fault.
func (m *Manager) Active() (Spec, bool) {
	m.mu.Lock()
	spec := m.active
	if spec.Type == "" {
		m.mu.Unlock()
		return Spec{}, false
	}
	if !m.now().Before(spec.ExpiresAt) {
		hadMemory := m.clearLocked()
		m.mu.Unlock()
		if hadMemory {
			debug.FreeOSMemory()
		}
		return Spec{}, false
	}
	m.mu.Unlock()
	return spec, true
}

// Usage returns a safe snapshot for observable gauges and tests.
func (m *Manager) Usage() Usage {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return Usage{
		CPUWorkers:      m.active.CPUWorkers,
		MemoryBytes:     len(m.memory),
		ActiveFaultType: m.active.Type,
	}
}

// BeforeRequest applies latency or a deterministic error before business logic.
func (m *Manager) BeforeRequest(ctx context.Context) error {
	spec, ok := m.Active()
	if !ok {
		return nil
	}

	switch spec.Type {
	case "latency":
		timer := time.NewTimer(time.Duration(spec.LatencyMillis) * time.Millisecond)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
			return nil
		}
	case "error_rate":
		sequence := m.sequence.Add(1)
		if int((sequence*37)%100) < spec.ErrorRatePercent {
			return ErrInjected
		}
	}
	return nil
}

func validate(spec Spec) error {
	if spec.DurationSeconds < 1 || spec.DurationSeconds > maxDurationSeconds {
		return fmt.Errorf("duration_seconds must be between 1 and %d", maxDurationSeconds)
	}
	switch spec.Type {
	case "latency":
		if spec.LatencyMillis < 1 || spec.LatencyMillis > maxLatencyMillis {
			return fmt.Errorf("latency_ms must be between 1 and %d", maxLatencyMillis)
		}
	case "error_rate":
		if spec.ErrorRatePercent < 1 || spec.ErrorRatePercent > 100 {
			return errors.New("error_rate_percent must be between 1 and 100")
		}
	case "cpu_saturation":
		if spec.CPUWorkers < 1 || spec.CPUWorkers > maxCPUWorkers {
			return fmt.Errorf("cpu_workers must be between 1 and %d", maxCPUWorkers)
		}
	case "memory_pressure":
		if spec.MemoryMegabytes < 1 || spec.MemoryMegabytes > maxMemoryMegabytes {
			return fmt.Errorf("memory_megabytes must be between 1 and %d", maxMemoryMegabytes)
		}
	case "connection_pool":
		if spec.PoolWaitMillis < 100 || spec.PoolWaitMillis > maxPoolWaitMillis {
			return fmt.Errorf("pool_wait_ms must be between 100 and %d", maxPoolWaitMillis)
		}
	case "dependency_unavailable":
		if spec.Dependency == "" {
			return errors.New("dependency is required")
		}
	case "configuration_error":
		if spec.ConfigKey == "" || spec.ConfigValue == "" {
			return errors.New("config_key and config_value are required")
		}
	case "release_regression":
		if spec.PreviousVersion == "" || spec.ReleaseVersion == "" || spec.TriggerSKU == "" {
			return errors.New("previous_version, release_version, and trigger_sku are required")
		}
	default:
		return fmt.Errorf("unsupported fault type: %s", spec.Type)
	}
	return nil
}

func burnCPU(stop <-chan struct{}) {
	var value uint64 = 1
	for {
		select {
		case <-stop:
			return
		default:
			for range 10_000 {
				value = value*1_664_525 + 1_013_904_223
			}
			cpuSink.Store(value)
		}
	}
}
