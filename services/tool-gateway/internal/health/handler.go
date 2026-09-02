// Package health exposes dependency-free process health endpoints.
package health

import (
	"encoding/json"
	"net/http"
)

type response struct {
	Service string `json:"service"`
	Status  string `json:"status"`
}

// Handler writes a stable JSON response for liveness and readiness probes.
func Handler(status string) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(response{
			Service: "tool-gateway",
			Status:  status,
		})
	}
}
