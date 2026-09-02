// Package telemetry configures OTLP traces, metrics, propagation, and correlated JSON logs.
package telemetry

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"time"

	otelruntime "go.opentelemetry.io/contrib/instrumentation/runtime"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

// Runtime owns providers and the log file lifetime.
type Runtime struct {
	logger         *slog.Logger
	tracerProvider *sdktrace.TracerProvider
	meterProvider  *sdkmetric.MeterProvider
	logFile        *os.File
}

// Start configures standard OTLP HTTP exporters from OTEL_* environment variables.
func Start(ctx context.Context, serviceName, serviceVersion, logPath string) (*Runtime, error) {
	res, err := resource.New(
		ctx,
		resource.WithFromEnv(),
		resource.WithAttributes(
			attribute.String("service.name", serviceName),
			attribute.String("service.version", serviceVersion),
			attribute.String("deployment.environment.name", "testbed"),
		),
	)
	if err != nil {
		return nil, err
	}

	traceExporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, err
	}
	tracerProvider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(traceExporter),
		sdktrace.WithResource(res),
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
	)

	metricExporter, err := otlpmetrichttp.New(ctx)
	if err != nil {
		_ = tracerProvider.Shutdown(ctx)
		return nil, err
	}
	meterProvider := sdkmetric.NewMeterProvider(
		sdkmetric.WithResource(res),
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExporter, sdkmetric.WithInterval(5*time.Second))),
	)
	if err := otelruntime.Start(
		otelruntime.WithMeterProvider(meterProvider),
		otelruntime.WithMinimumReadMemStatsInterval(time.Second),
	); err != nil {
		_ = errors.Join(meterProvider.Shutdown(ctx), tracerProvider.Shutdown(ctx))
		return nil, err
	}

	otel.SetTracerProvider(tracerProvider)
	otel.SetMeterProvider(meterProvider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	writers := []io.Writer{os.Stdout}
	var logFile *os.File
	if logPath != "" {
		logFile, err = os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			_ = errors.Join(meterProvider.Shutdown(ctx), tracerProvider.Shutdown(ctx))
			return nil, err
		}
		writers = append(writers, logFile)
	}
	logger := slog.New(slog.NewJSONHandler(io.MultiWriter(writers...), &slog.HandlerOptions{Level: slog.LevelInfo})).With(
		"service", serviceName,
		"service_version", serviceVersion,
	)

	return &Runtime{
		logger:         logger,
		tracerProvider: tracerProvider,
		meterProvider:  meterProvider,
		logFile:        logFile,
	}, nil
}

// Logger returns the process-wide structured logger.
func (r *Runtime) Logger() *slog.Logger { return r.logger }

// Shutdown flushes all telemetry within the caller's deadline.
func (r *Runtime) Shutdown(ctx context.Context) error {
	providerErr := errors.Join(
		r.meterProvider.Shutdown(ctx),
		r.tracerProvider.Shutdown(ctx),
	)
	if r.logFile != nil {
		return errors.Join(providerErr, r.logFile.Close())
	}
	return providerErr
}

// TraceAttributes adds W3C identifiers to logs for Loki-to-Tempo correlation.
func TraceAttributes(ctx context.Context) []any {
	spanContext := trace.SpanContextFromContext(ctx)
	if !spanContext.IsValid() {
		return nil
	}
	return []any{
		"trace_id", spanContext.TraceID().String(),
		"span_id", spanContext.SpanID().String(),
	}
}
