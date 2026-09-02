package gateway

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

// Config contains only startup-controlled source locations and trust settings.
// No source URL can be supplied by an RPC caller.
type Config struct {
	PrometheusURL  string
	LokiURL        string
	TempoURL       string
	ReleaseFile    string
	GitRepository  string
	Kubeconfig     string
	ArtifactDir    string
	AuthToken      string
	InlineBytes    int
	MaximumBytes   int
	RequestsPerSec float64
	Burst          int
}

// NewProductionServer wires fixed connectors. Optional Git and Kubernetes
// sources remain explicit unavailable connectors when they are not configured.
func NewProductionServer(config Config, logger *slog.Logger) (*Server, []error, error) {
	httpConnector, err := newHTTPObservabilityConnector(&http.Client{Timeout: 15 * time.Second}, config.PrometheusURL, config.LokiURL, config.TempoURL, int64(config.MaximumBytes))
	if err != nil {
		return nil, nil, err
	}
	artifacts, err := newArtifactStore(config.ArtifactDir, config.MaximumBytes)
	if err != nil {
		return nil, nil, err
	}
	warnings := make([]error, 0, 2)
	var gitSource gitConnector = unavailableGitConnector{}
	if connector, openErr := newGoGitConnector(config.GitRepository); openErr == nil {
		gitSource = connector
	} else {
		warnings = append(warnings, openErr)
	}
	var kubernetesSource kubernetesConnector = unavailableKubernetesConnector{}
	if client, clientErr := kubernetesClient(config.Kubeconfig); clientErr == nil {
		kubernetesSource = newClientGoConnector(client)
	} else {
		warnings = append(warnings, clientErr)
	}
	server, err := newServer(serverOptions{
		Observability: httpConnector,
		Releases:      jsonlReleaseConnector{filePath: config.ReleaseFile, maxLine: 1024 * 1024},
		Git:           gitSource,
		Kubernetes:    kubernetesSource,
		Limiter:       newActorLimiter(config.RequestsPerSec, config.Burst),
		Audit:         logAuditSink{logger: logger},
		Artifacts:     artifacts,
		InlineBytes:   config.InlineBytes,
		AuthToken:     config.AuthToken,
	})
	return server, warnings, err
}

func kubernetesClient(kubeconfig string) (kubernetes.Interface, error) {
	var (
		config *rest.Config
		err    error
	)
	if kubeconfig != "" {
		config, err = clientcmd.BuildConfigFromFlags("", kubeconfig)
	} else {
		config, err = rest.InClusterConfig()
	}
	if err != nil {
		return nil, errors.New("Kubernetes source is not configured")
	}
	config.Timeout = 15 * time.Second
	return kubernetes.NewForConfig(config)
}

type unavailableGitConnector struct{}

func (unavailableGitConnector) GetCommit(context.Context, string, uint32) (connectorResult, error) {
	return connectorResult{}, sourceUnavailable(errors.New("Git source is not configured"))
}

type unavailableKubernetesConnector struct{}

func (unavailableKubernetesConnector) GetWorkload(context.Context, string, string, string) (connectorResult, error) {
	return connectorResult{}, sourceUnavailable(errors.New("Kubernetes source is not configured"))
}

func (unavailableKubernetesConnector) ListEvents(context.Context, string, string, string, uint32) (connectorResult, error) {
	return connectorResult{}, sourceUnavailable(errors.New("Kubernetes source is not configured"))
}

// ConfigFromEnvironment resolves startup-only connector configuration.
func ConfigFromEnvironment() Config {
	return Config{
		PrometheusURL:  envOr("PROMETHEUS_URL", "http://127.0.0.1:19090"),
		LokiURL:        envOr("LOKI_URL", "http://127.0.0.1:13100"),
		TempoURL:       envOr("TEMPO_URL", "http://127.0.0.1:13200"),
		ReleaseFile:    envOr("RELEASE_EVENTS_FILE", "../../testbed/artifacts/fault-events/events.jsonl"),
		GitRepository:  envOr("GIT_REPOSITORY_PATH", "../.."),
		Kubeconfig:     os.Getenv("KUBECONFIG"),
		ArtifactDir:    envOr("ARTIFACT_DIRECTORY", "/tmp/ai-sre-tool-artifacts"),
		AuthToken:      os.Getenv("GATEWAY_AUTH_TOKEN"),
		InlineBytes:    64 * 1024,
		MaximumBytes:   4 * 1024 * 1024,
		RequestsPerSec: 20,
		Burst:          40,
	}
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
