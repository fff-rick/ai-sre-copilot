#!/usr/bin/env python3
"""Run the stage-6 replay or online prompt comparison and enforce the candidate gate."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "investigation" / "src"))

from ai_sre_investigation.model_client import (  # noqa: E402
    OpenAICompatibleModelClient,
)
from ai_sre_investigation.stage6 import (  # noqa: E402
    evaluate_stage6,
    load_dataset,
    render_markdown,
)


def online_model() -> OpenAICompatibleModelClient:
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


async def evaluate(
    args: argparse.Namespace, input_cost: float, output_cost: float
) -> dict[str, object]:
    model = online_model() if args.mode == "online" else None
    try:
        return await evaluate_stage6(
            dataset=load_dataset(args.dataset),
            output_root=args.output.parent / "stage6",
            mode=args.mode,
            online_model=model,
            input_cost_usd_per_million=input_cost,
            output_cost_usd_per_million=output_cost,
        )
    finally:
        if model is not None:
            await model.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("replay", "online"), default="replay")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals" / "stage6-cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "stage6-report.json")
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=ROOT / "artifacts" / "stage6-report.md",
    )
    parser.add_argument(
        "--input-cost-usd-per-million",
        type=float,
        default=(
            float(os.environ["AI_SRE_MODEL_INPUT_USD_PER_MILLION"])
            if "AI_SRE_MODEL_INPUT_USD_PER_MILLION" in os.environ
            else None
        ),
    )
    parser.add_argument(
        "--output-cost-usd-per-million",
        type=float,
        default=(
            float(os.environ["AI_SRE_MODEL_OUTPUT_USD_PER_MILLION"])
            if "AI_SRE_MODEL_OUTPUT_USD_PER_MILLION" in os.environ
            else None
        ),
    )
    args = parser.parse_args()
    if args.mode == "online" and (
        args.input_cost_usd_per_million is None or args.output_cost_usd_per_million is None
    ):
        raise SystemExit(
            "online evaluation requires AI_SRE_MODEL_INPUT_USD_PER_MILLION and "
            "AI_SRE_MODEL_OUTPUT_USD_PER_MILLION"
        )
    input_cost = args.input_cost_usd_per_million
    output_cost = args.output_cost_usd_per_million
    if input_cost is None:
        input_cost = 1.0
    if output_cost is None:
        output_cost = 4.0
    report = asyncio.run(evaluate(args, input_cost, output_cost))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(render_markdown(report))
    print(render_markdown(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
