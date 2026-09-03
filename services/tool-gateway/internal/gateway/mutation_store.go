package gateway

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	toolgatewayv1 "ai-sre-copilot.local/tool-gateway/gen/ai/sre/toolgateway/v1"
)

type mutationSpec struct {
	ToolName       string
	Target         string
	Parameters     map[string]any
	ParametersHash string
}

type mutationExecution struct {
	ExecutionID     string
	ApprovalID      string
	InvestigationID string
	ToolName        string
	Target          string
	ParametersHash  string
	IdempotencyKey  string
	Status          string
	Result          []byte
	SafeError       string
	StartedAt       time.Time
	FinishedAt      *time.Time
}

type mutationAuthorizer interface {
	Authorize(context.Context, string, string, string, string, mutationSpec) (mutationExecution, bool, error)
	Finalize(context.Context, string, string, []byte, string) (mutationExecution, error)
	Get(context.Context, string, string) (mutationExecution, error)
}

type unavailableMutationAuthorizer struct{}

func (unavailableMutationAuthorizer) Authorize(context.Context, string, string, string, string, mutationSpec) (mutationExecution, bool, error) {
	return mutationExecution{}, false, sourceUnavailable(errors.New("mutation approval store is not configured"))
}
func (unavailableMutationAuthorizer) Finalize(context.Context, string, string, []byte, string) (mutationExecution, error) {
	return mutationExecution{}, sourceUnavailable(errors.New("mutation approval store is not configured"))
}
func (unavailableMutationAuthorizer) Get(context.Context, string, string) (mutationExecution, error) {
	return mutationExecution{}, sourceUnavailable(errors.New("mutation approval store is not configured"))
}

type postgresMutationAuthorizer struct{ database *sql.DB }

func newPostgresMutationAuthorizer(database *sql.DB) *postgresMutationAuthorizer {
	return &postgresMutationAuthorizer{database: database}
}

func setupMutationSchema(ctx context.Context, database *sql.DB) error {
	setupCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := database.PingContext(setupCtx); err != nil {
		return fmt.Errorf("connect mutation approval database: %w", err)
	}
	if _, err := database.ExecContext(setupCtx, mutationSchema); err != nil {
		return fmt.Errorf("setup mutation approval schema: %w", err)
	}
	return nil
}

func (a *postgresMutationAuthorizer) Authorize(
	ctx context.Context,
	token, idempotencyKey, investigationID, actorID string,
	spec mutationSpec,
) (mutationExecution, bool, error) {
	tx, err := a.database.BeginTx(ctx, nil)
	if err != nil {
		return mutationExecution{}, false, sourceUnavailable(err)
	}
	defer func() { _ = tx.Rollback() }()

	var existing mutationExecution
	row := tx.QueryRowContext(ctx, `
		SELECT execution_id, approval_id, investigation_id, tool_name, target,
		       parameters_hash, idempotency_key, status, result, safe_error,
		       started_at, finished_at
		FROM remediation_executions WHERE idempotency_key = $1
		FOR UPDATE`, idempotencyKey)
	scanErr := scanExecution(row, &existing)
	if scanErr == nil {
		if existing.InvestigationID != investigationID || existing.ToolName != spec.ToolName ||
			existing.Target != spec.Target || existing.ParametersHash != spec.ParametersHash {
			return mutationExecution{}, false, classifiedConflict("idempotency key is bound to a different mutation")
		}
		if err := tx.Commit(); err != nil {
			return mutationExecution{}, false, sourceUnavailable(err)
		}
		return existing, true, nil
	} else if !errors.Is(scanErr, sql.ErrNoRows) {
		return mutationExecution{}, false, sourceUnavailable(scanErr)
	}

	tokenDigest := sha256.Sum256([]byte(token))
	var approvalID, approvedInvestigation, toolName, target, parametersHash, status string
	var storedTokenHash []byte
	var expiresAt time.Time
	err = tx.QueryRowContext(ctx, `
		SELECT approval_id, investigation_id, tool_name, target, parameters_hash,
		       status, decode(token_hash, 'hex'), token_expires_at
		FROM remediation_approvals
		WHERE token_hash = $1
		FOR UPDATE`, hex.EncodeToString(tokenDigest[:])).Scan(
		&approvalID, &approvedInvestigation, &toolName, &target, &parametersHash,
		&status, &storedTokenHash, &expiresAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return mutationExecution{}, false, permissionDenied("approval token is invalid")
	}
	if err != nil {
		return mutationExecution{}, false, sourceUnavailable(err)
	}
	if subtle.ConstantTimeCompare(storedTokenHash, tokenDigest[:]) != 1 {
		return mutationExecution{}, false, permissionDenied("approval token is invalid")
	}
	if status != "APPROVED" {
		return mutationExecution{}, false, permissionDenied("approval token is not active")
	}
	if !expiresAt.After(time.Now()) {
		if _, updateErr := tx.ExecContext(ctx, `UPDATE remediation_approvals SET status='EXPIRED', updated_at=now() WHERE approval_id=$1`, approvalID); updateErr != nil {
			return mutationExecution{}, false, sourceUnavailable(updateErr)
		}
		if auditErr := insertMutationAudit(ctx, tx, approvedInvestigation, approvalID, "approval.expired", actorID, "rejected", spec); auditErr != nil {
			return mutationExecution{}, false, sourceUnavailable(auditErr)
		}
		if commitErr := tx.Commit(); commitErr != nil {
			return mutationExecution{}, false, sourceUnavailable(commitErr)
		}
		return mutationExecution{}, false, permissionDenied("approval token has expired")
	}
	if approvedInvestigation != investigationID || toolName != spec.ToolName || target != spec.Target || parametersHash != spec.ParametersHash {
		if auditErr := insertMutationAudit(ctx, tx, approvedInvestigation, approvalID, "mutation.binding_rejected", actorID, "rejected", spec); auditErr != nil {
			return mutationExecution{}, false, sourceUnavailable(auditErr)
		}
		if commitErr := tx.Commit(); commitErr != nil {
			return mutationExecution{}, false, sourceUnavailable(commitErr)
		}
		return mutationExecution{}, false, permissionDenied("approval token does not match mutation parameters")
	}

	executionID := "exec-" + randomIdentifier()
	err = tx.QueryRowContext(ctx, `
		INSERT INTO remediation_executions (
			execution_id, approval_id, investigation_id, tool_name, target,
			parameters_hash, idempotency_key, status
		) VALUES ($1,$2,$3,$4,$5,$6,$7,'EXECUTING')
		RETURNING execution_id, approval_id, investigation_id, tool_name, target,
		          parameters_hash, idempotency_key, status, result, safe_error,
		          started_at, finished_at`,
		executionID, approvalID, investigationID, spec.ToolName, spec.Target,
		spec.ParametersHash, idempotencyKey,
	).Scan(&existing.ExecutionID, &existing.ApprovalID, &existing.InvestigationID,
		&existing.ToolName, &existing.Target, &existing.ParametersHash,
		&existing.IdempotencyKey, &existing.Status, &existing.Result, &existing.SafeError,
		&existing.StartedAt, &existing.FinishedAt)
	if err != nil {
		return mutationExecution{}, false, sourceUnavailable(err)
	}
	if _, err = tx.ExecContext(ctx, `UPDATE remediation_approvals SET status='CONSUMED', consumed_at=now(), updated_at=now() WHERE approval_id=$1`, approvalID); err != nil {
		return mutationExecution{}, false, sourceUnavailable(err)
	}
	if err = insertMutationAudit(ctx, tx, investigationID, approvalID, "mutation.authorized", actorID, "success", spec); err != nil {
		return mutationExecution{}, false, sourceUnavailable(err)
	}
	if err = tx.Commit(); err != nil {
		return mutationExecution{}, false, sourceUnavailable(err)
	}
	return existing, false, nil
}

func (a *postgresMutationAuthorizer) Finalize(ctx context.Context, executionID, status string, result []byte, safeError string) (mutationExecution, error) {
	tx, err := a.database.BeginTx(ctx, nil)
	if err != nil {
		return mutationExecution{}, sourceUnavailable(err)
	}
	defer func() { _ = tx.Rollback() }()
	var record mutationExecution
	err = tx.QueryRowContext(ctx, `
		UPDATE remediation_executions
		SET status=$2, result=$3::jsonb, safe_error=$4, finished_at=now()
		WHERE execution_id=$1 AND status='EXECUTING'
		RETURNING execution_id, approval_id, investigation_id, tool_name, target,
		          parameters_hash, idempotency_key, status, result, safe_error,
		          started_at, finished_at`, executionID, status, nullableJSON(result), safeError,
	).Scan(&record.ExecutionID, &record.ApprovalID, &record.InvestigationID,
		&record.ToolName, &record.Target, &record.ParametersHash, &record.IdempotencyKey,
		&record.Status, &record.Result, &record.SafeError, &record.StartedAt, &record.FinishedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return a.Get(ctx, "", executionID)
	}
	if err != nil {
		return mutationExecution{}, sourceUnavailable(err)
	}
	spec := mutationSpec{ToolName: record.ToolName, Target: record.Target, ParametersHash: record.ParametersHash}
	if err = insertMutationAudit(ctx, tx, record.InvestigationID, record.ApprovalID, "mutation."+strings.ToLower(status), "tool-gateway", strings.ToLower(status), spec); err != nil {
		return mutationExecution{}, sourceUnavailable(err)
	}
	if err = tx.Commit(); err != nil {
		return mutationExecution{}, sourceUnavailable(err)
	}
	return record, nil
}

func (a *postgresMutationAuthorizer) Get(ctx context.Context, investigationID, key string) (mutationExecution, error) {
	var record mutationExecution
	query := `SELECT execution_id, approval_id, investigation_id, tool_name, target,
		parameters_hash, idempotency_key, status, result, safe_error, started_at, finished_at
		FROM remediation_executions WHERE `
	var row *sql.Row
	if investigationID == "" {
		row = a.database.QueryRowContext(ctx, query+`execution_id=$1`, key)
	} else {
		row = a.database.QueryRowContext(ctx, query+`investigation_id=$1 AND idempotency_key=$2`, investigationID, key)
	}
	if err := scanExecution(row, &record); errors.Is(err, sql.ErrNoRows) {
		return mutationExecution{}, notFound("mutation execution was not found")
	} else if err != nil {
		return mutationExecution{}, sourceUnavailable(err)
	}
	return record, nil
}

type rowScanner interface{ Scan(...any) error }

func scanExecution(row rowScanner, record *mutationExecution) error {
	return row.Scan(&record.ExecutionID, &record.ApprovalID, &record.InvestigationID,
		&record.ToolName, &record.Target, &record.ParametersHash, &record.IdempotencyKey,
		&record.Status, &record.Result, &record.SafeError, &record.StartedAt, &record.FinishedAt)
}

func insertMutationAudit(ctx context.Context, tx *sql.Tx, investigationID, approvalID, eventType, actorID, outcome string, spec mutationSpec) error {
	_, err := tx.ExecContext(ctx, `
		INSERT INTO remediation_audit_events (
			investigation_id, approval_id, event_type, actor_id, outcome,
			tool_name, target, parameters_hash
		) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`, investigationID, approvalID,
		eventType, actorID, outcome, spec.ToolName, spec.Target, spec.ParametersHash)
	return err
}

func canonicalHash(parameters map[string]any) (string, error) {
	encoded, err := json.Marshal(parameters)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}

func nullableJSON(value []byte) any {
	if len(value) == 0 {
		return nil
	}
	return string(value)
}

func permissionDenied(message string) error {
	return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED, message, false, nil)
}

func classifiedConflict(message string) error {
	return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_CONFLICT, message, false, nil)
}

func randomIdentifier() string {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		panic("crypto/rand unavailable")
	}
	return hex.EncodeToString(value)
}

const mutationSchema = `
CREATE TABLE IF NOT EXISTS remediation_approvals (
    approval_id text PRIMARY KEY,
    investigation_id text NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    action_id text NOT NULL,
    tool_name text NOT NULL,
    target text NOT NULL,
    parameters jsonb NOT NULL,
    parameters_hash char(64) NOT NULL,
    risk_level text NOT NULL,
    status text NOT NULL,
    proposed_by text NOT NULL,
    approved_by text,
    rejected_by text,
    token_hash char(64) UNIQUE,
    token_expires_at timestamptz,
    consumed_at timestamptz,
    action jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS remediation_approvals_investigation_idx
ON remediation_approvals (investigation_id, created_at);

CREATE TABLE IF NOT EXISTS remediation_executions (
    execution_id text PRIMARY KEY,
    approval_id text NOT NULL REFERENCES remediation_approvals(approval_id),
    investigation_id text NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    tool_name text NOT NULL,
    target text NOT NULL,
    parameters_hash char(64) NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    status text NOT NULL,
    result jsonb,
    safe_error text NOT NULL DEFAULT '',
    recovery_status text,
    pre_evidence jsonb,
    post_evidence jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS remediation_audit_events (
    event_id bigserial PRIMARY KEY,
    investigation_id text NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    approval_id text REFERENCES remediation_approvals(approval_id),
    event_type text NOT NULL,
    actor_id text NOT NULL,
    outcome text NOT NULL,
    tool_name text NOT NULL,
    target text NOT NULL,
    parameters_hash char(64) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
`
