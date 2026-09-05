#!/usr/bin/env python3
"""Build and validate the immutable provenance manifest attached to a release tag."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return raw


def canonical_dataset_sha256(path: Path) -> str:
    payload = read_json(path)
    payload.pop("content_sha256", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in (ROOT / ".tool-versions").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, version = line.split(maxsplit=1)
        versions[name] = version
    return versions


def report_binding(path: Path) -> dict[str, Any]:
    report = read_json(path)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256(path),
        "mode": report.get("mode"),
        "commit": report.get("commit"),
        "dataset": report.get("dataset"),
        "dataset_sha256": report.get("dataset_sha256"),
        "gate_profile": report.get("gate_profile"),
        "passed": report.get("passed"),
        "models": sorted(
            {
                str(profile.get("model_id"))
                for profile in report.get("profiles", [])
                if isinstance(profile, dict)
            }
        ),
        "prompts": [
            {
                "version": profile.get("prompt_version"),
                "sha256": profile.get("prompt_sha256"),
            }
            for profile in report.get("profiles", [])
            if isinstance(profile, dict)
        ],
    }


def build_manifest(
    release: str,
    offline_report: Path,
    online_report: Path | None,
    require_tag: bool,
) -> dict[str, Any]:
    commit = git("rev-parse", "HEAD")
    tags = set(git("tag", "--points-at", "HEAD").splitlines())
    if require_tag and release not in tags:
        raise ValueError(f"release tag {release!r} does not point at HEAD")
    if git("status", "--porcelain") and require_tag:
        raise ValueError("release manifest requires a clean tagged worktree")

    stage6_dataset = ROOT / "evals/stage6-cases.json"
    stage4_dataset = ROOT / "evals/stage4-retrieval-cases.json"
    scenarios = sorted((ROOT / "testbed/scenarios").glob("*.yaml"))
    offline = report_binding(offline_report)
    stage6_content_sha256 = canonical_dataset_sha256(stage6_dataset)
    if offline["dataset_sha256"] != stage6_content_sha256:
        raise ValueError("offline report dataset checksum does not match the frozen dataset")
    if not offline["passed"]:
        raise ValueError("offline release quality gate did not pass")

    reports = {"offline": offline}
    if online_report is not None:
        online = report_binding(online_report)
        if online["dataset_sha256"] != stage6_content_sha256:
            raise ValueError("online report dataset checksum does not match the frozen dataset")
        if not online["passed"]:
            raise ValueError("online release quality gate did not pass")
        reports["online"] = online
    elif require_tag:
        raise ValueError("a tagged release requires the 32-case online evaluation report")

    source_epoch = os.getenv("SOURCE_DATE_EPOCH") or git("show", "-s", "--format=%ct", "HEAD")
    generated_at = datetime.fromtimestamp(int(source_epoch), UTC).isoformat()
    return {
        "schema_version": 1,
        "release": release,
        "generated_at": generated_at,
        "source": {"commit": commit, "tag": release if release in tags else None},
        "runtime": runtime_versions(),
        "datasets": {
            "stage6": {
                "id": offline["dataset"],
                "path": stage6_dataset.relative_to(ROOT).as_posix(),
                "content_sha256": stage6_content_sha256,
                "file_sha256": sha256(stage6_dataset),
            },
            "retrieval": {
                "path": stage4_dataset.relative_to(ROOT).as_posix(),
                "sha256": sha256(stage4_dataset),
            },
            "fault_scenarios": [
                {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)}
                for path in scenarios
            ],
        },
        "reports": reports,
        "deployment": {
            "compose": {
                "path": "compose.yaml",
                "sha256": sha256(ROOT / "compose.yaml"),
            },
            "database_schema": {
                "path": "deploy/postgres/init.sql",
                "sha256": sha256(ROOT / "deploy/postgres/init.sql"),
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument(
        "--offline-report", type=Path, default=ROOT / "artifacts/stage6-report.json"
    )
    parser.add_argument("--online-report", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/release-manifest.json")
    parser.add_argument("--require-tag", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_manifest(
        args.release,
        args.offline_report.resolve(),
        args.online_report.resolve() if args.online_report else None,
        args.require_tag,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"release manifest: {args.output}")


if __name__ == "__main__":
    main()
