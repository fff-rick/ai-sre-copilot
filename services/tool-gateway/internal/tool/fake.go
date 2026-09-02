package tool

import "context"

// Fake is a deterministic read tool for contract and workflow tests.
type Fake struct {
	ToolName string
	Result   Result
	Err      error
}

// Name returns the startup registration name.
func (f Fake) Name() string { return f.ToolName }

// Execute returns the configured fixture without external access.
func (f Fake) Execute(_ context.Context, _ Request) (Result, error) { return f.Result, f.Err }
