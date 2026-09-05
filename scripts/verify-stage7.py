#!/usr/bin/env python3
"""Run deterministic release hardening probes and write the stage-7 report."""

import asyncio
import hashlib
import json
import platform
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from ai_sre_investigation.domain import Alert, InvestigationStatus, Severity, TimeWindow
from ai_sre_investigation.fakes import FailingFakeToolClient, FakeToolClient
from ai_sre_investigation.ports import ModelRequest, ModelResponse
from ai_sre_investigation.repository import InMemoryInvestigationRepository
from ai_sre_investigation.service import InvestigationService
from ai_sre_investigation.workflow import InvestigationWorkflow

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {
    InvestigationStatus.COMPLETED,
    InvestigationStatus.CANCELLED,
    InvestigationStatus.FAILED,
}


class ProbeModel:
    def __init__(self, delay_seconds: float = 0.03) -> None:
        self.delay_seconds = delay_seconds
        self.active = 0
        self.maximum_active = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(self.delay_seconds)
            payload = json.loads(request.input_text.split("\n", 1)[0])
            evidence_id = payload["evidence"][0]["evidence_id"]
            return ModelResponse(
                data={
                    "hypotheses": [
                        {
                            "statement": "The bounded probe is supported by collected evidence.",
                            "rank": 1,
                            "confidence": 0.9,
                            "supporting_evidence_ids": [evidence_id],
                            "contradicting_evidence_ids": [],
                            "next_checks": [],
                        }
                    ]
                },
                model_id="stage7-probe-v1",
                input_tokens=10,
                output_tokens=5,
            )
        finally:
            self.active -= 1


def alert(index: int) -> Alert:
    now = datetime.now(UTC)
    return Alert(
        alert_id=f"stage7-concurrency-{index}",
        service="payment",
        severity=Severity.CRITICAL,
        summary="Payment error rate increased.",
        time_window=TimeWindow(start=now - timedelta(minutes=10), end=now),
        source_ref="acceptance://stage7",
    )


def service(
    model: ProbeModel,
    repository: InMemoryInvestigationRepository,
    *,
    workers: int,
    fail_loki: bool = False,
) -> InvestigationService:
    responses = {
        "prometheus.query": {"error_rate": 0.2},
        "loki.query_range": {"errors": 20},
        "tempo.search_traces": {"failed_traces": 4},
        "releases.list": {"version": "1.1.0"},
    }
    tools: FakeToolClient
    if fail_loki:
        tools = FailingFakeToolClient(
            responses, {"loki.query_range": RuntimeError("source unavailable")}
        )
    else:
        tools = FakeToolClient(responses)
    workflow = InvestigationWorkflow(
        model=model,
        tools=tools,
        checkpointer=InMemorySaver(),
        cancel_check=repository.is_cancel_requested,
        event_sink=repository.append_event,
    )
    return InvestigationService(
        repository=repository,
        workflow=workflow,
        poll_seconds=0.005,
        worker_count=workers,
    )


async def wait_for_terminal(
    active: InvestigationService, identifiers: list[str], timeout_seconds: float = 5
) -> list[Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        records = await asyncio.gather(*(active.get(identifier) for identifier in identifiers))
        if all(record is not None and record.status in TERMINAL for record in records):
            return records
        await asyncio.sleep(0.01)
    raise TimeoutError("stage-7 investigation probe did not reach a terminal state")


async def concurrency_probe() -> dict[str, Any]:
    repository = InMemoryInvestigationRepository()
    model = ProbeModel()
    active = service(model, repository, workers=5)
    await active.start()
    started = time.monotonic()
    try:
        created = await asyncio.gather(*(active.create(alert(index)) for index in range(5)))
        records = await wait_for_terminal(
            active, [item.investigation.investigation_id for item in created]
        )
    finally:
        await active.stop()
    completed = sum(record.status == InvestigationStatus.COMPLETED for record in records)
    return {
        "requested": 5,
        "completed": completed,
        "completion_rate": completed / 5,
        "maximum_parallel_model_calls": model.maximum_active,
        "wall_seconds": round(time.monotonic() - started, 6),
        "unique_trace_count": len({record.investigation.trace_id for record in records}),
        "worker_tasks_after_stop": len(active._workers),
        "passed": completed == 5 and model.maximum_active == 5 and not active._workers,
    }


async def degradation_probe() -> dict[str, Any]:
    repository = InMemoryInvestigationRepository()
    active = service(ProbeModel(delay_seconds=0), repository, workers=1, fail_loki=True)
    await active.start()
    try:
        created = await active.create(alert(99))
        records = await wait_for_terminal(active, [created.investigation.investigation_id])
    finally:
        await active.stop()
    record = records[0]
    gaps = record.report.get("evidence_gaps", []) if record.report else []
    loki_gap = any(item.get("source_type") == "loki.query_range" for item in gaps)
    return {
        "injected_failure": "loki.query_range source unavailable",
        "terminal_status": record.status.value,
        "evidence_gap_recorded": loki_gap,
        "passed": record.status == InvestigationStatus.COMPLETED and loki_gap,
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compose_probe() -> dict[str, Any]:
    raw = subprocess.check_output(
        ["docker", "compose", "config", "--format", "json"], cwd=ROOT, text=True
    )
    services = json.loads(raw)["services"]
    required = {"postgres", "investigation", "tool-gateway", "web"}
    details: dict[str, Any] = {}
    passed = True
    for name in sorted(required):
        item = services[name]
        bounded = (
            bool(item.get("cpus")) and bool(item.get("mem_limit")) and bool(item.get("pids_limit"))
        )
        no_privilege_escalation = "no-new-privileges:true" in item.get("security_opt", [])
        details[name] = {
            "cpus": item.get("cpus"),
            "memory_bytes": int(item.get("mem_limit", 0)),
            "pids": item.get("pids_limit"),
            "no_new_privileges": no_privilege_escalation,
        }
        passed = passed and bounded and no_privilege_escalation
    dockerfiles = list(ROOT.glob("**/Dockerfile"))
    floating = []
    for path in dockerfiles:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("FROM ") and re.search(r":(?:latest|alpine)(?:\s|$)", line):
                floating.append(f"{path.relative_to(ROOT)}: {line}")
    return {
        "services": details,
        "floating_base_images": floating,
        "passed": passed and not floating,
    }


def quality_probe() -> dict[str, Any]:
    report_path = ROOT / "artifacts/stage6-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    candidate = next(
        profile
        for profile in report["profiles"]
        if profile["prompt_version"] == report["gate_profile"]
    )
    metrics = candidate["metrics"]
    return {
        "report": str(report_path.relative_to(ROOT)),
        "report_sha256": file_sha256(report_path),
        "dataset": report["dataset"],
        "dataset_sha256": report["dataset_sha256"],
        "prompt": candidate["prompt_version"],
        "prompt_sha256": candidate["prompt_sha256"],
        "model": candidate["model_id"],
        "metrics": metrics,
        "gate_failures": report["gate_failures"],
        "passed": report["passed"],
    }


def secret_probe() -> dict[str, Any]:
    pattern = (
        "-----BEGIN "
        + r"(?:RSA |OPENSSH |EC )?PRIVATE KEY-----|"
        + "s"
        + "k-"
        + r"[A-Za-z0-9]{20,}"
    )
    result = subprocess.run(
        ["git", "grep", "-I", "-n", "-P", "-e", pattern],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    known_test_values = {"sk-abcdefghijklmnop1234"}
    findings = [
        line
        for line in result.stdout.splitlines()
        if line and not any(value in line for value in known_test_values)
    ]
    return {
        "tracked_secret_findings": findings,
        "passed": result.returncode in {0, 1} and not findings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    quality = report["probes"]["quality"]
    metrics = quality["metrics"]
    concurrency = report["probes"]["concurrency"]
    degradation = report["probes"]["degradation"]
    compose_status = "PASS" if report["probes"]["compose"]["passed"] else "FAIL"
    secret_status = "PASS" if report["probes"]["secrets"]["passed"] else "FAIL"
    lines = [
        "# Stage 7 Acceptance Report",
        "",
        f"- Decision: **{'PASS' if report['passed'] else 'FAIL'}**",
        f"- Commit: `{report['commit']}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Dataset: `{quality['dataset']}` / `{quality['dataset_sha256']}`",
        f"- Prompt: `{quality['prompt']}` / `{quality['prompt_sha256']}`",
        f"- Model: `{quality['model']}` ({report['evaluation_scope']})",
        "",
        "## Quality, performance, and cost",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Completion | {metrics['completion_rate']:.1%} |",
        f"| Top-1 / Top-3 | {metrics['top1_accuracy']:.1%} / {metrics['top3_accuracy']:.1%} |",
        f"| Evidence validity | {metrics['evidence_validity']:.1%} |",
        f"| Unsupported claims | {metrics['unsupported_claim_rate']:.1%} |",
        (
            f"| Security / Trace | {metrics['security_pass_rate']:.1%} / "
            f"{metrics['trace_completeness']:.1%} |"
        ),
        (
            f"| P50 / P95 duration | {metrics['p50_duration_seconds']:.3f}s / "
            f"{metrics['p95_duration_seconds']:.3f}s |"
        ),
        (
            f"| P50 / P95 estimated cost | ${metrics['p50_cost_usd']:.6f} / "
            f"${metrics['p95_cost_usd']:.6f} |"
        ),
        "",
        "## Hardening probes",
        "",
        (
            f"- Five concurrent investigations: `{concurrency['completed']}/5`, "
            f"maximum model concurrency `{concurrency['maximum_parallel_model_calls']}`."
        ),
        (
            f"- Injected Loki outage: terminal `{degradation['terminal_status']}`, "
            f"evidence gap recorded `{degradation['evidence_gap_recorded']}`."
        ),
        f"- Resource/security limits: `{compose_status}`.",
        f"- Tracked secret pattern scan: `{secret_status}`.",
        "",
        (
            "The injected Loki outage is an expected uncertainty case: the investigation "
            "completes using other evidence and explicitly records the missing source."
        ),
    ]
    return "\n".join(lines) + "\n"


async def main() -> None:
    probes = {
        "concurrency": await concurrency_probe(),
        "degradation": await degradation_probe(),
        "compose": compose_probe(),
        "quality": quality_probe(),
        "secrets": secret_probe(),
    }
    commit = await asyncio.to_thread(
        subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    )
    commit = commit.strip()
    report = {
        "schema_version": 1,
        "stage": 7,
        "commit": commit,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "evaluation_scope": "deterministic frozen replay; online release gate is separate",
        "probes": probes,
        "passed": all(probe["passed"] for probe in probes.values()),
    }
    output = ROOT / "artifacts/stage7-acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = ROOT / "artifacts/stage7-acceptance.md"
    markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
