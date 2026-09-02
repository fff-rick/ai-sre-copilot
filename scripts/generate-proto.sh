#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
proto_file="ai/sre/toolgateway/v1/tool_gateway.proto"
go_bin=$(go env GOPATH)/bin

PATH="${go_bin}:${PATH}" protoc \
  --proto_path="${repo_root}/proto" \
  --go_out="${repo_root}/services/tool-gateway" \
  --go_opt=module=ai-sre-copilot.local/tool-gateway \
  --go-grpc_out="${repo_root}/services/tool-gateway" \
  --go-grpc_opt=module=ai-sre-copilot.local/tool-gateway \
  "${proto_file}"

python_output="${repo_root}/services/investigation/src/ai_sre_investigation/generated"
mkdir -p "${python_output}"
uv run --project "${repo_root}/services/investigation" python -m grpc_tools.protoc \
  --proto_path="${repo_root}/proto/ai/sre/toolgateway/v1" \
  --python_out="${python_output}" \
  --pyi_out="${python_output}" \
  --grpc_python_out="${python_output}" \
  tool_gateway.proto

sed -i 's/^import tool_gateway_pb2 as/from . import tool_gateway_pb2 as/' \
  "${python_output}/tool_gateway_pb2_grpc.py"
touch "${python_output}/__init__.py"
