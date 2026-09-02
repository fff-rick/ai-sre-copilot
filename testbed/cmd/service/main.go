// Command service runs one configured role of the observable testbed.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"ai-sre-copilot.local/testbed/internal/app"
	"ai-sre-copilot.local/testbed/internal/config"
	"ai-sre-copilot.local/testbed/internal/fault"
	"ai-sre-copilot.local/testbed/internal/telemetry"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	cfg := config.Load()
	runtime, err := telemetry.Start(ctx, cfg.ServiceName, cfg.ServiceVersion, cfg.LogPath)
	if err != nil {
		slog.Error("telemetry initialization failed", "error", err)
		os.Exit(1)
	}
	logger := runtime.Logger()

	var database *pgxpool.Pool
	if cfg.ServiceName == "inventory" {
		if cfg.DatabaseURL == "" {
			logger.Error("DATABASE_URL is required for inventory")
			os.Exit(1)
		}
		database, err = pgxpool.New(ctx, cfg.DatabaseURL)
		if err != nil {
			logger.Error("database pool initialization failed", "error", err)
			os.Exit(1)
		}
		defer database.Close()
	}

	handler, err := app.New(cfg, logger, database, fault.NewManager())
	if err != nil {
		logger.Error("application initialization failed", "error", err)
		os.Exit(1)
	}
	server := &http.Server{
		Addr:              cfg.ListenAddress,
		Handler:           handler,
		ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      35 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			logger.Error("HTTP shutdown failed", "error", err)
		}
		if err := runtime.Shutdown(shutdownCtx); err != nil {
			logger.Error("telemetry shutdown failed", "error", err)
		}
	}()

	logger.Info("testbed service listening", "address", cfg.ListenAddress)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("HTTP server failed", "error", err)
		os.Exit(1)
	}
}
