package gateway

import (
	"context"
	"testing"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

func TestClientGoConnectorUsesTypedFakeClient(t *testing.T) {
	now := metav1.NewTime(time.Now())
	client := fake.NewClientset(
		&appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: "payment", Namespace: "default"}, Status: appsv1.DeploymentStatus{Replicas: 3, ReadyReplicas: 2}},
		&corev1.Event{ObjectMeta: metav1.ObjectMeta{Name: "payment-warning", Namespace: "default"}, InvolvedObject: corev1.ObjectReference{Kind: "Deployment", Name: "payment", Namespace: "default"}, Type: "Warning", Reason: "Unhealthy", Message: "readiness failed", LastTimestamp: now},
	)
	connector := newClientGoConnector(client)
	workload, err := connector.GetWorkload(context.Background(), "default", "Deployment", "payment")
	if err != nil {
		t.Fatal(err)
	}
	data := workload.Data.(map[string]any)
	if data["ready_replicas"] != int32(2) {
		t.Fatalf("ready replicas = %v", data["ready_replicas"])
	}
	events, err := connector.ListEvents(context.Background(), "default", "Deployment", "payment", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(events.Data.(map[string]any)["events"].([]map[string]any)) != 1 {
		t.Fatalf("unexpected events: %+v", events.Data)
	}
}
