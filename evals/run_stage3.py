#!/usr/bin/env python3
"""Run the five-case stage-3 smoke evaluation with fake or real model adapters."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "investigation" / "src"))

from ai_sre_investigation.domain import (  # noqa: E402
    Alert,
    Investigation,
    Severity,
    TimeWindow,
)
from ai_sre_investigation.fakes import FakeModelClient, FakeToolClient  # noqa: E402
from ai_sre_investigation.model_client import OpenAICompatibleModelClient  # noqa: E402
from ai_sre_investigation.ports import ModelClient  # noqa: E402
from ai_sre_investigation.workflow import (  # noqa: E402
    PROMPT_VERSION,
    InvestigationWorkflow,
)


def evidence_id(tool_name: str, data: object) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    seed = f"{tool_name}:fake://{tool_name}:{digest}"
    return f"ev-{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def fake_payload(case: dict[str, Any]) -> dict[str, object]:
    first_evidence = evidence_id("prometheus.query", case["tools"]["prometheus.query"])
    statements = [
        case["root_cause"],
        f"A secondary capacity issue may affect {case['service']}.",
        f"An upstream dependency may contribute to {case['summary'].lower()}",
    ]
    return {
        "hypotheses": [
            {
                "statement": statement,
                "rank": rank,
                "confidence": 0.9 if rank == 1 else 0.4 / rank,
                "supporting_evidence_ids": [first_evidence],
                "contradicting_evidence_ids": [],
                "next_checks": [],
            }
            for rank, statement in enumerate(statements, start=1)
        ]
    }


def real_model() -> OpenAICompatibleModelClient:
    required = {
        name: os.environ.get(name, "")
        for name in ("AI_SRE_MODEL_BASE_URL", "AI_SRE_MODEL_API_KEY", "AI_SRE_MODEL_ID")
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit("online evaluation requires: " + ", ".join(missing))
    return OpenAICompatibleModelClient(
        base_url=required["AI_SRE_MODEL_BASE_URL"],
        api_key=required["AI_SRE_MODEL_API_KEY"],
        model=required["AI_SRE_MODEL_ID"],
    )


async def evaluate(mode: str) -> dict[str, Any]:
    dataset = json.loads((ROOT / "evals" / "stage3-cases.json").read_text())
    now = datetime.now(UTC)
    shared_model: ModelClient | None = real_model() if mode == "online" else None
    results: list[dict[str, Any]] = []
    try:
        for case in dataset["cases"]:
            model = shared_model or FakeModelClient(fake_payload(case))
            workflow = InvestigationWorkflow(
                model=model, tools=FakeToolClient(case["tools"]), now=lambda: now
            )
            item = Investigation(
                investigation_id=f"inv-eval-{case['id'].lower()}",
                trace_id=hashlib.sha256(case["id"].encode()).hexdigest()[:16],
                alert=Alert(
                    alert_id=case["id"],
                    service=case["service"],
                    severity=Severity.CRITICAL,
                    summary=case["summary"],
                    time_window=TimeWindow(start=now - timedelta(minutes=15), end=now),
                    source_ref=f"dataset://{dataset['dataset_id']}/{case['id']}",
                ),
                created_at=now,
                updated_at=now,
            )
            state = await workflow.run(item)
            hypotheses = state.get("hypotheses", [])
            text = " ".join(str(value["statement"]).lower() for value in hypotheses)
            match_groups = [
                [str(alias).lower() for alias in group] for group in case["match_groups"]
            ]
            matched_groups = [any(alias in text for alias in aliases) for aliases in match_groups]
            cited = {
                evidence
                for hypothesis in hypotheses
                for evidence in hypothesis["supporting_evidence_ids"]
            }
            available = {value["evidence_id"] for value in state.get("evidence", [])}
            results.append(
                {
                    "case_id": case["id"],
                    "completed": state.get("status") == "COMPLETED",
                    "candidate_count": len(hypotheses),
                    "top3_ground_truth_hit": all(matched_groups),
                    "matched_ground_truth_groups": matched_groups,
                    "top3_statements": [value["statement"] for value in hypotheses],
                    "evidence_valid": cited <= available,
                    "model_id": state.get("model_id"),
                    "repair_attempted": state.get("repair_attempted", False),
                    "usage": state.get("usage", {}),
                    "gaps": [
                        {
                            "source_type": gap["source_type"],
                            "error_code": gap["error_code"],
                        }
                        for gap in state.get("evidence_gaps", [])
                    ],
                }
            )
    finally:
        if isinstance(shared_model, OpenAICompatibleModelClient):
            await shared_model.close()
    passed = all(
        result["completed"]
        and result["candidate_count"] == 3
        and result["top3_ground_truth_hit"]
        and result["evidence_valid"]
        for result in results
    )
    git = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD", cwd=ROOT, stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await git.communicate()
    if git.returncode != 0:
        raise RuntimeError("could not resolve Git commit")
    return {
        "dataset": dataset["dataset_id"],
        "prompt": PROMPT_VERSION,
        "mode": mode,
        "commit": stdout.decode().strip(),
        "timestamp": datetime.now(UTC).isoformat(),
        "passed": passed,
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fake", "online"), default="fake")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.mode))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
