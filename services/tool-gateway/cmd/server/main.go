// Command server starts the trusted tool gateway health and gRPC endpoints.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	toolgatewayv1 "ai-sre-copilot.local/tool-gateway/gen/ai/sre/toolgateway/v1"
	"ai-sre-copilot.local/tool-gateway/internal/gateway"
	"ai-sre-copilot.local/tool-gateway/internal/health"
	"google.golang.org/grpc"
	grpcHealth "google.golang.org/grpc/health"
	grpcHealthV1 "google.golang.org/grpc/health/grpc_health_v1"
)

func main() {
	address := os.Getenv("SERVER_ADDRESS")
	if address == "" {
		address = ":8081"
	}
	grpcAddress := os.Getenv("GRPC_ADDRESS")
	if grpcAddress == "" {
		grpcAddress = ":9091"
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)
	toolServer, warnings, err := gateway.NewProductionServer(gateway.ConfigFromEnvironment(), logger)
	if err != nil {
		slog.Error("configure tool gateway", "error", err)
		os.Exit(1)
	}
	for _, warning := range warnings {
		slog.Warn("optional connector disabled", "error", warning)
	}
	listener, err := net.Listen("tcp", grpcAddress)
	if err != nil {
		slog.Error("listen for gRPC", "error", err)
		os.Exit(1)
	}
	grpcServer := grpc.NewServer(grpc.MaxRecvMsgSize(256*1024), grpc.MaxSendMsgSize(4*1024*1024))
	toolgatewayv1.RegisterToolGatewayV1Server(grpcServer, toolServer)
	healthServer := grpcHealth.NewServer()
	healthServer.SetServingStatus("ai.sre.toolgateway.v1.ToolGatewayV1", grpcHealthV1.HealthCheckResponse_SERVING)
	grpcHealthV1.RegisterHealthServer(grpcServer, healthServer)

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
		healthServer.Shutdown()
		grpcServer.GracefulStop()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			slog.Error("server shutdown failed", "error", err)
		}
	}()
	go func() {
		slog.Info("tool gateway gRPC listening", "address", grpcAddress)
		if serveErr := grpcServer.Serve(listener); serveErr != nil {
			slog.Error("gRPC server stopped unexpectedly", "error", serveErr)
			stop()
		}
	}()

	slog.Info("tool gateway listening", "address", address)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("tool gateway stopped unexpectedly", "error", err)
		os.Exit(1)
	}
}
