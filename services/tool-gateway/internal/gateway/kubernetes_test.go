package gateway

import (
	"context"
	"testing"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	autoscalingv1 "k8s.io/api/autoscaling/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"
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

func TestClientGoConnectorRunsOnlyTypedDeploymentMutations(t *testing.T) {
	replicas := int32(2)
	client := fake.NewClientset(
		&appsv1.Deployment{
			ObjectMeta: metav1.ObjectMeta{Name: "payment", Namespace: "ai-sre-test", Annotations: map[string]string{"deployment.kubernetes.io/revision": "2"}},
			Spec: appsv1.DeploymentSpec{
				Replicas: &replicas,
				Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"app": "payment"}},
				Template: corev1.PodTemplateSpec{ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{"app": "payment"}}, Spec: corev1.PodSpec{Containers: []corev1.Container{{Name: "app", Image: "payment:v2"}}}},
			},
		},
		&appsv1.ReplicaSet{
			ObjectMeta: metav1.ObjectMeta{Name: "payment-v1", Namespace: "ai-sre-test", Labels: map[string]string{"app": "payment"}, Annotations: map[string]string{"deployment.kubernetes.io/revision": "1"}},
			Spec:       appsv1.ReplicaSetSpec{Template: corev1.PodTemplateSpec{ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{"app": "payment"}}, Spec: corev1.PodSpec{Containers: []corev1.Container{{Name: "app", Image: "payment:v1"}}}}},
		},
	)
	connector := newClientGoConnector(client)
	client.PrependReactor("get", "deployments", func(action k8stesting.Action) (bool, runtime.Object, error) {
		if action.GetSubresource() == "scale" {
			return true, &autoscalingv1.Scale{ObjectMeta: metav1.ObjectMeta{Name: "payment", Namespace: "ai-sre-test"}, Spec: autoscalingv1.ScaleSpec{Replicas: 2}}, nil
		}
		return false, nil, nil
	})
	client.PrependReactor("update", "deployments", func(action k8stesting.Action) (bool, runtime.Object, error) {
		if action.GetSubresource() == "scale" {
			return true, action.(k8stesting.UpdateAction).GetObject(), nil
		}
		return false, nil, nil
	})
	if _, err := connector.RestartDeployment(context.Background(), "ai-sre-test", "payment", time.Now()); err != nil {
		t.Fatal(err)
	}
	deployment, err := client.AppsV1().Deployments("ai-sre-test").Get(context.Background(), "payment", metav1.GetOptions{})
	if err != nil || deployment.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] == "" {
		t.Fatalf("restart annotation missing: deployment=%+v error=%v", deployment, err)
	}
	if _, err := connector.ScaleDeployment(context.Background(), "ai-sre-test", "payment", 3); err != nil {
		t.Fatal(err)
	}
	if _, err := connector.RollbackDeployment(context.Background(), "ai-sre-test", "payment", 1); err != nil {
		t.Fatal(err)
	}
	deployment, err = client.AppsV1().Deployments("ai-sre-test").Get(context.Background(), "payment", metav1.GetOptions{})
	if err != nil || deployment.Spec.Template.Spec.Containers[0].Image != "payment:v1" {
		t.Fatalf("rollback image=%q error=%v", deployment.Spec.Template.Spec.Containers[0].Image, err)
	}
}
