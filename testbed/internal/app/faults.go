package app

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"time"

	"ai-sre-copilot.local/testbed/internal/fault"
	"ai-sre-copilot.local/testbed/internal/telemetry"
	"github.com/jackc/pgx/v5/pgxpool"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

func (a *App) validateFaultTarget(spec fault.Spec) error {
	switch spec.Type {
	case "connection_pool":
		if a.config.ServiceName != "inventory" || a.database == nil {
			return errors.New("connection_pool fault requires the inventory service")
		}
	case "dependency_unavailable", "configuration_error":
		if a.config.ServiceName != "order" {
			return fmt.Errorf("%s fault requires the order service", spec.Type)
		}
	case "release_regression":
		if a.config.ServiceName != "payment" {
			return errors.New("release_regression fault requires the payment service")
		}
	}
	return nil
}

func (a *App) activeFault(faultType string) (fault.Spec, bool) {
	spec, ok := a.faults.Active()
	return spec, ok && spec.Type == faultType
}

func (a *App) faultedEndpoint(ctx context.Context, dependency, endpoint string) string {
	spec, ok := a.faults.Active()
	if !ok {
		return endpoint
	}

	switch spec.Type {
	case "dependency_unavailable":
		if spec.Dependency == dependency {
			a.logger.ErrorContext(ctx, "injected dependency unavailable", append(telemetry.TraceAttributes(ctx), faultLogAttributes(spec)...)...)
			return "http://127.0.0.1:1/" + dependency
		}
	case "configuration_error":
		if spec.ConfigKey == dependency+"_path" {
			parsed, err := url.Parse(endpoint)
			if err == nil {
				parsed.Path = spec.ConfigValue
				a.logger.ErrorContext(ctx, "injected invalid configuration applied", append(telemetry.TraceAttributes(ctx), faultLogAttributes(spec)...)...)
				return parsed.String()
			}
		}
	}
	return endpoint
}

func (a *App) exhaustConnectionPool(ctx context.Context, spec fault.Spec) error {
	maxConnections := int(a.database.Stat().MaxConns())
	held := make([]*pgxpool.Conn, 0, maxConnections)
	defer func() {
		for _, connection := range held {
			connection.Release()
		}
	}()

	for range maxConnections {
		acquireCtx, cancel := context.WithTimeout(ctx, time.Second)
		connection, err := a.database.Acquire(acquireCtx)
		cancel()
		if err != nil {
			return fmt.Errorf("fill database pool: %w", err)
		}
		held = append(held, connection)
	}

	started := time.Now()
	waitCtx, cancel := context.WithTimeout(ctx, time.Duration(spec.PoolWaitMillis)*time.Millisecond)
	defer cancel()
	connection, err := a.database.Acquire(waitCtx)
	waitMillis := float64(time.Since(started).Microseconds()) / 1000
	serviceOption := metric.WithAttributes(attribute.String("service.name", a.config.ServiceName))
	a.poolWaitTime.Record(ctx, waitMillis, serviceOption)
	if connection != nil {
		connection.Release()
		return errors.New("pool acquire unexpectedly succeeded")
	}
	a.poolFailures.Add(ctx, 1, serviceOption)
	stats := a.database.Stat()
	a.logger.ErrorContext(ctx, "database connection pool exhausted", append(
		telemetry.TraceAttributes(ctx),
		"scenario_id", spec.ScenarioID,
		"pool_acquired", stats.AcquiredConns(),
		"pool_max", stats.MaxConns(),
		"pool_wait_ms", waitMillis,
		"error", err,
	)...)
	return fmt.Errorf("database connection pool exhausted after %.3f ms: %w", waitMillis, err)
}

func (a *App) registerObservableMetrics(meter metric.Meter) error {
	activeGauge, err := meter.Int64ObservableGauge("testbed.fault.active")
	if err != nil {
		return err
	}
	memoryGauge, err := meter.Int64ObservableGauge("testbed.fault.memory.allocated", metric.WithUnit("By"))
	if err != nil {
		return err
	}
	cpuGauge, err := meter.Int64ObservableGauge("testbed.fault.cpu.workers", metric.WithUnit("{worker}"))
	if err != nil {
		return err
	}
	_, err = meter.RegisterCallback(func(_ context.Context, observer metric.Observer) error {
		usage := a.faults.Usage()
		faultType := usage.ActiveFaultType
		active := int64(1)
		if faultType == "" {
			faultType = "none"
			active = 0
		}
		options := metric.WithAttributes(
			attribute.String("fault.type", faultType),
			attribute.String("service.name", a.config.ServiceName),
		)
		observer.ObserveInt64(activeGauge, active, options)
		observer.ObserveInt64(memoryGauge, int64(usage.MemoryBytes), options)
		observer.ObserveInt64(cpuGauge, int64(usage.CPUWorkers), options)
		return nil
	}, activeGauge, memoryGauge, cpuGauge)
	if err != nil {
		return err
	}

	if a.database == nil {
		return nil
	}
	connectionsGauge, err := meter.Int64ObservableGauge("testbed.db.pool.connections")
	if err != nil {
		return err
	}
	canceledCounter, err := meter.Int64ObservableCounter("testbed.db.pool.canceled_acquires")
	if err != nil {
		return err
	}
	_, err = meter.RegisterCallback(func(_ context.Context, observer metric.Observer) error {
		stats := a.database.Stat()
		observer.ObserveInt64(connectionsGauge, int64(stats.AcquiredConns()), metric.WithAttributes(attribute.String("pool.state", "acquired"), attribute.String("service.name", a.config.ServiceName)))
		observer.ObserveInt64(connectionsGauge, int64(stats.IdleConns()), metric.WithAttributes(attribute.String("pool.state", "idle"), attribute.String("service.name", a.config.ServiceName)))
		observer.ObserveInt64(connectionsGauge, int64(stats.MaxConns()), metric.WithAttributes(attribute.String("pool.state", "max"), attribute.String("service.name", a.config.ServiceName)))
		observer.ObserveInt64(canceledCounter, stats.CanceledAcquireCount(), metric.WithAttributes(attribute.String("service.name", a.config.ServiceName)))
		return nil
	}, connectionsGauge, canceledCounter)
	return err
}

func faultLogAttributes(spec fault.Spec) []any {
	return []any{
		"scenario_id", spec.ScenarioID,
		"fault_type", spec.Type,
		"started_at", spec.StartedAt,
		"expires_at", spec.ExpiresAt,
		"latency_ms", spec.LatencyMillis,
		"error_rate_percent", spec.ErrorRatePercent,
		"cpu_workers", spec.CPUWorkers,
		"memory_megabytes", spec.MemoryMegabytes,
		"pool_wait_ms", spec.PoolWaitMillis,
		"dependency", spec.Dependency,
		"config_key", spec.ConfigKey,
		"config_value", spec.ConfigValue,
		"previous_version", spec.PreviousVersion,
		"release_version", spec.ReleaseVersion,
		"trigger_sku", spec.TriggerSKU,
	}
}
