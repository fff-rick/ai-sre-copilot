// Package gateway implements the fixed gRPC trust boundary.
package gateway

import (
	"errors"

	toolgatewayv1 "ai-sre-copilot.local/tool-gateway/gen/ai/sre/toolgateway/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Error is a classified domain failure whose message is safe to expose.
type Error struct {
	Code      toolgatewayv1.ToolErrorCode
	Message   string
	Retryable bool
	Cause     error
}

func (e *Error) Error() string { return e.Message }
func (e *Error) Unwrap() error { return e.Cause }

func classified(code toolgatewayv1.ToolErrorCode, message string, retryable bool, cause error) error {
	return &Error{Code: code, Message: message, Retryable: retryable, Cause: cause}
}

func invalid(message string) error {
	return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_INVALID_ARGUMENT, message, false, nil)
}

func sourceUnavailable(cause error) error {
	return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_SOURCE_UNAVAILABLE, "data source is unavailable", true, cause)
}

func notFound(message string) error {
	return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_NOT_FOUND, message, false, nil)
}

func toStatus(err error) error {
	var domain *Error
	if !errors.As(err, &domain) {
		domain = &Error{
			Code:    toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_INTERNAL,
			Message: "internal tool gateway error",
			Cause:   err,
		}
	}

	grpcCode := map[toolgatewayv1.ToolErrorCode]codes.Code{
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_INVALID_ARGUMENT:   codes.InvalidArgument,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_UNAUTHENTICATED:    codes.Unauthenticated,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED:  codes.PermissionDenied,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_RATE_LIMITED:       codes.ResourceExhausted,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_DEADLINE_EXCEEDED:  codes.DeadlineExceeded,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_SOURCE_UNAVAILABLE: codes.Unavailable,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_NOT_FOUND:          codes.NotFound,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_RESPONSE_TOO_LARGE: codes.ResourceExhausted,
		toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_INTERNAL:           codes.Internal,
	}[domain.Code]
	if grpcCode == codes.OK {
		grpcCode = codes.Internal
	}

	detail := &toolgatewayv1.ToolError{Code: domain.Code, SafeMessage: domain.Message, Retryable: domain.Retryable}
	withDetail, detailErr := status.New(grpcCode, domain.Message).WithDetails(detail)
	if detailErr != nil {
		return status.Error(grpcCode, domain.Message)
	}
	return withDetail.Err()
}
