"""Run the planner eval set against a real model and score it deterministically.

    uv run python -m evals.run                         # default model, 1 repeat
    uv run python -m evals.run --model gpt-5.4-nano --repeats 3
    uv run python -m evals.run --only net_ --no-repair  # experiment E2: repair call off

Each case runs plan → validate → ground (live hit counts; no records are fetched). Results go
to evals/results/<timestamp>_<model>.jsonl plus a Markdown summary next to it.
"""

import argparse
import asyncio
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.contracts.request import VisualizationRequest
from app.factory import create_ctgov_client, create_http_client
from app.planner import Planner, PlannerError
from app.planner.gateway import OpenAIPlannerGateway
from app.planner.grounding import Grounder
from app.planner.prompt import PROMPT_VERSION
from evals.scoring import score_case

CASES = Path(__file__).parent / "cases.yaml"
RESULTS = Path(__file__).parent / "results"


async def run_case(planner: Planner, grounder: Grounder | None, case: dict[str, Any]) -> dict:
    request = VisualizationRequest.model_validate(case["request"])
    started = time.perf_counter()
    try:
        outcome = await planner.plan(request, grounder)
    except PlannerError as exc:
        return {"id": case["id"], "class": case["class"], "status": "error", "error": str(exc)}
    plan = outcome.plan.model_dump(mode="json") if outcome.plan else None
    score = score_case(
        case["expect"], outcome.status, plan if outcome.status == "accepted" else None
    )
    return {
        "id": case["id"],
        "class": case["class"],
        "status": outcome.status,
        "passed": score.passed,
        "fields_ok": score.fields_ok,
        "fields_total": score.fields_total,
        "mismatches": score.mismatches,
        "attempts": outcome.attempts,
        "repair_feedback": outcome.repair_feedback,
        "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens,
        "latency_s": round(time.perf_counter() - started, 2),
        "plan": plan,
    }


def summarize(rows: list[dict], model: str, repeats: int) -> str:
    by_class: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)
    total = len(rows)
    passed = sum(1 for r in rows if r.get("passed"))
    first_try = sum(1 for r in rows if r.get("attempts") == 1 and r.get("passed"))
    repaired = sum(1 for r in rows if r.get("attempts", 0) > 1)
    tokens = sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in rows)
    latency = sorted(r.get("latency_s", 0) for r in rows)
    # Stability: the same case yields the same plan on every repeat.
    plans: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        plans[r["id"]].add(json.dumps(r.get("plan"), sort_keys=True))
    stable = sum(1 for p in plans.values() if len(p) == 1)
    lines = [
        f"## Planner eval — {model}, prompt {PROMPT_VERSION}, {repeats} repeat(s)",
        "",
        f"- **Pass rate:** {passed}/{total} ({passed / total:.0%})",
        f"- **Passed on first try:** {first_try}/{total}; runs that used a repair: {repaired}",
        f"- **Stable across repeats:** {stable}/{len(plans)} cases",
        f"- **Tokens:** {tokens:,} total, {tokens // max(total, 1):,} per run",
        f"- **Latency:** median {latency[len(latency) // 2]:.1f}s, max {latency[-1]:.1f}s",
        "",
        "| Class | Passed |",
        "|---|---|",
    ]
    for name, items in sorted(by_class.items()):
        ok = sum(1 for r in items if r.get("passed"))
        lines.append(f"| {name} | {ok}/{len(items)} |")
    failures = [r for r in rows if not r.get("passed")]
    if failures:
        lines += ["", "### Failures", ""]
        for r in failures:
            detail = "; ".join(r.get("mismatches") or []) or r.get("error", "")
            lines.append(f"- `{r['id']}` ({r['status']}): {detail}")
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default=None, help="defaults to PLANNER_MODEL")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--only", default="", help="run case ids starting with this prefix")
    parser.add_argument("--no-ground", action="store_true", help="skip live grounding")
    parser.add_argument("--no-repair", action="store_true", help="E2: disable the repair call")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    settings = get_settings()
    if settings.openai_api_key is None:
        raise SystemExit("OPENAI_API_KEY is not set.")
    model = args.model or settings.planner_model
    gateway = OpenAIPlannerGateway(
        api_key=settings.openai_api_key.get_secret_value(),
        model=model,
        base_url=settings.openai_base_url,
        reasoning_effort=settings.planner_reasoning_effort,
    )
    planner = Planner(gateway, max_attempts=1 if args.no_repair else 2)
    cases = [c for c in yaml.safe_load(CASES.read_text()) if c["id"].startswith(args.only)]

    http = create_http_client(settings)
    try:
        grounder = None
        if not args.no_ground:
            ctgov = create_ctgov_client(settings, http)
            version = await ctgov.version()
            grounder = Grounder(ctgov, settings.max_trials_per_cohort, version.data_timestamp)
        limit = asyncio.Semaphore(args.concurrency)

        async def bounded(case: dict[str, Any]) -> dict:
            async with limit:
                return await run_case(planner, grounder, case)

        rows = await asyncio.gather(*(bounded(c) for c in cases for _ in range(args.repeats)))
    finally:
        await http.aclose()

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    suffix = "_norepair" if args.no_repair else ""
    out = RESULTS / f"{stamp}_{model}_{PROMPT_VERSION}{suffix}"
    out.with_suffix(".jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    summary = summarize(list(rows), model, args.repeats)
    out.with_suffix(".md").write_text(summary)
    print(summary)
    print(f"wrote {out}.jsonl / .md")


if __name__ == "__main__":
    asyncio.run(main())
