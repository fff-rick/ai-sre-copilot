package gateway

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	toolgatewayv1 "ai-sre-copilot.local/tool-gateway/gen/ai/sre/toolgateway/v1"
	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/util/retry"
)

type connectorResult struct {
	Data      any
	SourceRef string
}

type observabilityConnector interface {
	QueryPrometheus(context.Context, string, time.Time) (connectorResult, error)
	QueryLoki(context.Context, string, time.Time, time.Time, uint32, string) (connectorResult, error)
	GetTempoTrace(context.Context, string) (connectorResult, error)
	SearchTempoTraces(context.Context, string, time.Time, time.Time, uint32) (connectorResult, error)
}

type releaseConnector interface {
	ListReleases(context.Context, string, time.Time, time.Time, uint32) (connectorResult, error)
}

type gitConnector interface {
	GetCommit(context.Context, string, uint32) (connectorResult, error)
}

type kubernetesConnector interface {
	GetWorkload(context.Context, string, string, string) (connectorResult, error)
	ListEvents(context.Context, string, string, string, uint32) (connectorResult, error)
	RestartDeployment(context.Context, string, string, time.Time) (connectorResult, error)
	ScaleDeployment(context.Context, string, string, int32) (connectorResult, error)
	RollbackDeployment(context.Context, string, string, int64) (connectorResult, error)
}

type httpObservabilityConnector struct {
	client     *http.Client
	prometheus *url.URL
	loki       *url.URL
	tempo      *url.URL
	maxBytes   int64
}

func newHTTPObservabilityConnector(client *http.Client, prometheusURL, lokiURL, tempoURL string, maxBytes int64) (*httpObservabilityConnector, error) {
	parse := func(name, raw string) (*url.URL, error) {
		parsed, err := url.Parse(raw)
		if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
			return nil, fmt.Errorf("invalid %s URL", name)
		}
		return parsed, nil
	}
	prometheus, err := parse("Prometheus", prometheusURL)
	if err != nil {
		return nil, err
	}
	loki, err := parse("Loki", lokiURL)
	if err != nil {
		return nil, err
	}
	tempo, err := parse("Tempo", tempoURL)
	if err != nil {
		return nil, err
	}
	return &httpObservabilityConnector{client: client, prometheus: prometheus, loki: loki, tempo: tempo, maxBytes: maxBytes}, nil
}

func (c *httpObservabilityConnector) get(ctx context.Context, base *url.URL, endpoint string, query url.Values, source string) (connectorResult, error) {
	requestURL := *base
	requestURL.Path = path.Join(strings.TrimSuffix(base.Path, "/"), endpoint)
	requestURL.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL.String(), nil)
	if err != nil {
		return connectorResult{}, invalid("invalid data source request")
	}
	response, err := c.client.Do(request)
	if err != nil {
		return connectorResult{}, sourceUnavailable(err)
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusNotFound {
		return connectorResult{}, notFound("requested source object was not found")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return connectorResult{}, sourceUnavailable(fmt.Errorf("upstream status %d", response.StatusCode))
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, c.maxBytes+1))
	if err != nil {
		return connectorResult{}, sourceUnavailable(err)
	}
	if int64(len(data)) > c.maxBytes {
		return connectorResult{}, classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_RESPONSE_TOO_LARGE, "upstream response exceeds the maximum size", false, nil)
	}
	var decoded any
	if err := json.Unmarshal(data, &decoded); err != nil {
		return connectorResult{}, sourceUnavailable(fmt.Errorf("upstream returned invalid JSON: %w", err))
	}
	return connectorResult{Data: decoded, SourceRef: source}, nil
}

func (c *httpObservabilityConnector) QueryPrometheus(ctx context.Context, promQL string, at time.Time) (connectorResult, error) {
	query := url.Values{"query": {promQL}}
	if !at.IsZero() {
		query.Set("time", at.UTC().Format(time.RFC3339Nano))
	}
	return c.get(ctx, c.prometheus, "/api/v1/query", query, "prometheus://query")
}

func (c *httpObservabilityConnector) QueryLoki(ctx context.Context, logQL string, start, end time.Time, limit uint32, direction string) (connectorResult, error) {
	query := url.Values{
		"query":     {logQL},
		"start":     {strconv.FormatInt(start.UnixNano(), 10)},
		"end":       {strconv.FormatInt(end.UnixNano(), 10)},
		"limit":     {strconv.FormatUint(uint64(limit), 10)},
		"direction": {direction},
	}
	return c.get(ctx, c.loki, "/loki/api/v1/query_range", query, "loki://query_range")
}

func (c *httpObservabilityConnector) GetTempoTrace(ctx context.Context, traceID string) (connectorResult, error) {
	return c.get(ctx, c.tempo, "/api/traces/"+traceID, nil, "tempo://trace/"+traceID)
}

func (c *httpObservabilityConnector) SearchTempoTraces(ctx context.Context, traceQL string, start, end time.Time, limit uint32) (connectorResult, error) {
	query := url.Values{
		"q":     {traceQL},
		"start": {strconv.FormatInt(start.Unix(), 10)},
		"end":   {strconv.FormatInt(end.Unix(), 10)},
		"limit": {strconv.FormatUint(uint64(limit), 10)},
	}
	return c.get(ctx, c.tempo, "/api/search", query, "tempo://search")
}

type jsonlReleaseConnector struct {
	filePath string
	maxLine  int
}

func (c jsonlReleaseConnector) ListReleases(ctx context.Context, service string, start, end time.Time, limit uint32) (connectorResult, error) {
	file, err := os.Open(c.filePath)
	if err != nil {
		return connectorResult{}, sourceUnavailable(err)
	}
	defer file.Close()
	result := make([]map[string]any, 0, limit)
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), c.maxLine)
	for scanner.Scan() {
		if err := ctx.Err(); err != nil {
			return connectorResult{}, err
		}
		var event map[string]any
		if json.Unmarshal(scanner.Bytes(), &event) != nil {
			continue
		}
		occurredAt, err := time.Parse(time.RFC3339, fmt.Sprint(event["occurred_at"]))
		if err != nil || occurredAt.Before(start) || occurredAt.After(end) || fmt.Sprint(event["target"]) != service {
			continue
		}
		response, ok := event["response"].(map[string]any)
		if !ok || (fmt.Sprint(response["type"]) != "release_regression" && fmt.Sprint(event["scenario"]) != "release-payment") {
			continue
		}
		result = append(result, event)
	}
	if err := scanner.Err(); err != nil {
		return connectorResult{}, sourceUnavailable(err)
	}
	if len(result) > int(limit) {
		result = result[len(result)-int(limit):]
	}
	return connectorResult{Data: map[string]any{"releases": result}, SourceRef: "releases://events"}, nil
}

type goGitConnector struct{ repository *git.Repository }

func newGoGitConnector(repositoryPath string) (*goGitConnector, error) {
	repository, err := git.PlainOpen(repositoryPath)
	if err != nil {
		return nil, fmt.Errorf("open Git repository: %w", err)
	}
	return &goGitConnector{repository: repository}, nil
}

func (c *goGitConnector) GetCommit(ctx context.Context, revision string, maxFiles uint32) (connectorResult, error) {
	if err := ctx.Err(); err != nil {
		return connectorResult{}, err
	}
	hash, err := c.repository.ResolveRevision(plumbing.Revision(revision))
	if err != nil {
		return connectorResult{}, notFound("Git revision was not found")
	}
	commit, err := c.repository.CommitObject(*hash)
	if err != nil {
		return connectorResult{}, notFound("Git commit was not found")
	}
	stats, err := commit.Stats()
	if err != nil {
		return connectorResult{}, sourceUnavailable(err)
	}
	if len(stats) > int(maxFiles) {
		stats = stats[:maxFiles]
	}
	files := make([]map[string]any, 0, len(stats))
	for _, stat := range stats {
		files = append(files, map[string]any{"name": stat.Name, "additions": stat.Addition, "deletions": stat.Deletion})
	}
	parents := make([]string, 0, commit.NumParents())
	for index := 0; index < commit.NumParents(); index++ {
		parent, parentErr := commit.Parent(index)
		if parentErr != nil {
			return connectorResult{}, sourceUnavailable(parentErr)
		}
		parents = append(parents, parent.Hash.String())
	}
	return connectorResult{Data: map[string]any{
		"hash": commit.Hash.String(), "author": commit.Author.Name,
		"authored_at": commit.Author.When.UTC(), "message": strings.TrimSpace(commit.Message),
		"parents": parents, "changed_files": files,
	}, SourceRef: "git://commit/" + commit.Hash.String()}, nil
}

type clientGoConnector struct{ client kubernetes.Interface }

func newClientGoConnector(client kubernetes.Interface) *clientGoConnector {
	return &clientGoConnector{client: client}
}

func (c *clientGoConnector) GetWorkload(ctx context.Context, namespace, kind, name string) (connectorResult, error) {
	var data map[string]any
	switch strings.ToLower(kind) {
	case "deployment":
		item, err := c.client.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return connectorResult{}, mapKubernetesError(err)
		}
		data = map[string]any{"kind": "Deployment", "namespace": namespace, "name": name, "generation": item.Generation, "replicas": item.Status.Replicas, "ready_replicas": item.Status.ReadyReplicas, "available_replicas": item.Status.AvailableReplicas, "conditions": item.Status.Conditions}
	case "statefulset":
		item, err := c.client.AppsV1().StatefulSets(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return connectorResult{}, mapKubernetesError(err)
		}
		data = map[string]any{"kind": "StatefulSet", "namespace": namespace, "name": name, "generation": item.Generation, "replicas": item.Status.Replicas, "ready_replicas": item.Status.ReadyReplicas, "current_replicas": item.Status.CurrentReplicas, "conditions": item.Status.Conditions}
	case "daemonset":
		item, err := c.client.AppsV1().DaemonSets(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return connectorResult{}, mapKubernetesError(err)
		}
		data = map[string]any{"kind": "DaemonSet", "namespace": namespace, "name": name, "generation": item.Generation, "desired_scheduled": item.Status.DesiredNumberScheduled, "ready": item.Status.NumberReady, "available": item.Status.NumberAvailable, "conditions": item.Status.Conditions}
	case "pod":
		item, err := c.client.CoreV1().Pods(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return connectorResult{}, mapKubernetesError(err)
		}
		containers := make([]map[string]any, 0, len(item.Status.ContainerStatuses))
		for _, status := range item.Status.ContainerStatuses {
			containers = append(containers, map[string]any{"name": status.Name, "ready": status.Ready, "restart_count": status.RestartCount, "state": status.State})
		}
		data = map[string]any{"kind": "Pod", "namespace": namespace, "name": name, "phase": item.Status.Phase, "reason": item.Status.Reason, "conditions": item.Status.Conditions, "containers": containers}
	default:
		return connectorResult{}, invalid("kind must be Deployment, StatefulSet, DaemonSet, or Pod")
	}
	return connectorResult{Data: data, SourceRef: fmt.Sprintf("kubernetes://%s/%s/%s", namespace, strings.ToLower(kind), name)}, nil
}

func (c *clientGoConnector) ListEvents(ctx context.Context, namespace, kind, name string, limit uint32) (connectorResult, error) {
	selector := ""
	if kind != "" {
		selector = "involvedObject.kind=" + kind
	}
	if name != "" {
		if selector != "" {
			selector += ","
		}
		selector += "involvedObject.name=" + name
	}
	events, err := c.client.CoreV1().Events(namespace).List(ctx, metav1.ListOptions{FieldSelector: selector})
	if err != nil {
		return connectorResult{}, mapKubernetesError(err)
	}
	sort.Slice(events.Items, func(i, j int) bool { return events.Items[i].LastTimestamp.Before(&events.Items[j].LastTimestamp) })
	if len(events.Items) > int(limit) {
		events.Items = events.Items[len(events.Items)-int(limit):]
	}
	data := make([]map[string]any, 0, len(events.Items))
	for _, event := range events.Items {
		data = append(data, map[string]any{"type": event.Type, "reason": event.Reason, "message": event.Message, "count": event.Count, "first_timestamp": event.FirstTimestamp, "last_timestamp": event.LastTimestamp, "involved_object": map[string]any{"kind": event.InvolvedObject.Kind, "namespace": event.InvolvedObject.Namespace, "name": event.InvolvedObject.Name}})
	}
	return connectorResult{Data: map[string]any{"events": data}, SourceRef: "kubernetes://" + namespace + "/events"}, nil
}

func (c *clientGoConnector) RestartDeployment(ctx context.Context, namespace, name string, at time.Time) (connectorResult, error) {
	deployments := c.client.AppsV1().Deployments(namespace)
	var generation int64
	err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		item, getErr := deployments.Get(ctx, name, metav1.GetOptions{})
		if getErr != nil {
			return getErr
		}
		updated := item.DeepCopy()
		if updated.Spec.Template.Annotations == nil {
			updated.Spec.Template.Annotations = make(map[string]string)
		}
		updated.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = at.UTC().Format(time.RFC3339Nano)
		updated, updateErr := deployments.Update(ctx, updated, metav1.UpdateOptions{})
		if updateErr == nil {
			generation = updated.Generation
		}
		return updateErr
	})
	if err != nil {
		return connectorResult{}, mapKubernetesError(err)
	}
	return connectorResult{Data: map[string]any{
		"operation": "restart", "namespace": namespace, "name": name,
		"generation": generation,
	}, SourceRef: fmt.Sprintf("kubernetes://%s/deployment/%s", namespace, name)}, nil
}

func (c *clientGoConnector) ScaleDeployment(ctx context.Context, namespace, name string, replicas int32) (connectorResult, error) {
	deployments := c.client.AppsV1().Deployments(namespace)
	err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		scale, getErr := deployments.GetScale(ctx, name, metav1.GetOptions{})
		if getErr != nil {
			return getErr
		}
		scale.Spec.Replicas = replicas
		_, updateErr := deployments.UpdateScale(ctx, name, scale, metav1.UpdateOptions{})
		return updateErr
	})
	if err != nil {
		return connectorResult{}, mapKubernetesError(err)
	}
	return connectorResult{Data: map[string]any{
		"operation": "scale", "namespace": namespace, "name": name,
		"replicas": replicas,
	}, SourceRef: fmt.Sprintf("kubernetes://%s/deployment/%s/scale", namespace, name)}, nil
}

func (c *clientGoConnector) RollbackDeployment(ctx context.Context, namespace, name string, revision int64) (connectorResult, error) {
	deployments := c.client.AppsV1().Deployments(namespace)
	targetRevision := revision
	var generation int64
	err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		item, getErr := deployments.Get(ctx, name, metav1.GetOptions{})
		if getErr != nil {
			return getErr
		}
		replicaSets, listErr := c.client.AppsV1().ReplicaSets(namespace).List(ctx, metav1.ListOptions{LabelSelector: metav1.FormatLabelSelector(item.Spec.Selector)})
		if listErr != nil {
			return listErr
		}
		selectedRevision := revision
		if selectedRevision == 0 {
			current := deploymentRevision(item.Annotations)
			for index := range replicaSets.Items {
				candidate := deploymentRevision(replicaSets.Items[index].Annotations)
				if candidate < current && candidate > selectedRevision {
					selectedRevision = candidate
				}
			}
		}
		for index := range replicaSets.Items {
			replicaSet := &replicaSets.Items[index]
			if deploymentRevision(replicaSet.Annotations) != selectedRevision {
				continue
			}
			updated := item.DeepCopy()
			updated.Spec.Template = *replicaSet.Spec.Template.DeepCopy()
			updated, updateErr := deployments.Update(ctx, updated, metav1.UpdateOptions{})
			if updateErr == nil {
				targetRevision, generation = selectedRevision, updated.Generation
			}
			return updateErr
		}
		return errDeploymentRevisionNotFound
	})
	if errors.Is(err, errDeploymentRevisionNotFound) {
		return connectorResult{}, notFound("requested Deployment revision was not found")
	}
	if err != nil {
		return connectorResult{}, mapKubernetesError(err)
	}
	return connectorResult{Data: map[string]any{
		"operation": "rollback", "namespace": namespace, "name": name,
		"revision": targetRevision, "generation": generation,
	}, SourceRef: fmt.Sprintf("kubernetes://%s/deployment/%s/revisions/%d", namespace, name, targetRevision)}, nil
}

func deploymentRevision(annotations map[string]string) int64 {
	if annotations == nil {
		return 0
	}
	revision, _ := strconv.ParseInt(annotations["deployment.kubernetes.io/revision"], 10, 64)
	return revision
}

var errDeploymentRevisionNotFound = errors.New("deployment revision not found")

var kubernetesNamePattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$`)

func mapKubernetesError(err error) error {
	if strings.Contains(strings.ToLower(err.Error()), "not found") {
		return notFound("Kubernetes object was not found")
	}
	return sourceUnavailable(err)
}
