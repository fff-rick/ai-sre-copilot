// Command server starts the trusted tool gateway HTTP shell.
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

	"ai-sre-copilot.local/tool-gateway/internal/health"
)

func main() {
	address := os.Getenv("SERVER_ADDRESS")
	if address == "" {
		address = ":8081"
	}

	mux := http.NewServeMux()
	mux.Handle("GET /health/live", health.Handler("ok"))
	mux.Handle("GET /health/ready", health.Handler("ready"))

	server := &http.Server{
		Addr:              address,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			slog.Error("server shutdown failed", "error", err)
		}
	}()

	slog.Info("tool gateway listening", "address", address)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("tool gateway stopped unexpectedly", "error", err)
		os.Exit(1)
	}
}
