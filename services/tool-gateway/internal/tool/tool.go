// Package tool defines the deterministic boundary for registered read tools.
package tool

import (
	"context"
	"errors"
	"fmt"
)

// ErrNotRegistered prevents callers from constructing arbitrary operations.
var ErrNotRegistered = errors.New("tool is not registered")

// Request contains identity-independent data; transport middleware adds identity and deadlines.
type Request struct {
	InvestigationID string
	Arguments       map[string]any
}

// Result is bounded structured data with a reference to its source.
type Result struct {
	Data      map[string]any
	SourceRef string
}

// ReadTool is the only executable tool type introduced in stage 0.
type ReadTool interface {
	Name() string
	Execute(context.Context, Request) (Result, error)
}

// Registry exposes only tools explicitly registered at process startup.
type Registry struct {
	tools map[string]ReadTool
}

// NewRegistry rejects duplicate names instead of silently replacing a security boundary.
func NewRegistry(tools ...ReadTool) (*Registry, error) {
	registry := &Registry{tools: make(map[string]ReadTool, len(tools))}
	for _, candidate := range tools {
		name := candidate.Name()
		if name == "" {
			return nil, errors.New("tool name must not be empty")
		}
		if _, exists := registry.tools[name]; exists {
			return nil, fmt.Errorf("duplicate tool name: %s", name)
		}
		registry.tools[name] = candidate
	}
	return registry, nil
}

// ExecuteRead dispatches an exact registered name and never evaluates dynamic code.
func (r *Registry) ExecuteRead(ctx context.Context, name string, request Request) (Result, error) {
	candidate, exists := r.tools[name]
	if !exists {
		return Result{}, fmt.Errorf("%w: %s", ErrNotRegistered, name)
	}
	return candidate.Execute(ctx, request)
}
