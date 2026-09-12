package gateway

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestJSONLReleaseConnectorReadsDeploymentsAndLegacyEvents(t *testing.T) {
	path := filepath.Join(t.TempDir(), "releases.jsonl")
	lines := []map[string]any{
		{"type": "deployment", "occurred_at": "2026-09-12T10:00:00Z", "target": "live-api", "version": "v2", "revision": "abc123"},
		{"type": "deployment", "occurred_at": "2026-09-12T10:05:00Z", "target": "live-worker", "version": "v2", "revision": "abc123"},
		{"occurred_at": "2026-09-12T10:10:00Z", "target": "live-api", "response": map[string]any{"type": "release_regression"}},
		{"type": "note", "occurred_at": "2026-09-12T10:15:00Z", "target": "live-api"},
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range lines {
		if err := json.NewEncoder(file).Encode(line); err != nil {
			t.Fatal(err)
		}
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}

	start, _ := time.Parse(time.RFC3339, "2026-09-12T09:00:00Z")
	end, _ := time.Parse(time.RFC3339, "2026-09-12T11:00:00Z")
	result, err := (jsonlReleaseConnector{filePath: path, maxLine: 1024 * 1024}).ListReleases(context.Background(), "live-api", start, end, 10)
	if err != nil {
		t.Fatal(err)
	}
	releases := result.Data.(map[string]any)["releases"].([]map[string]any)
	if len(releases) != 2 {
		t.Fatalf("got %d releases, want deployment and legacy release", len(releases))
	}
}
