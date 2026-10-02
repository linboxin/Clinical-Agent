"""Render a stored run as a Markdown review sheet: question, plan, chart choice, assumptions,
coverage, the data table and a few citations per datum. Every example run is reviewed with
this before it is committed (DESIGN §12).

    uv run python -m scripts.review_run <run_id> [--citations 3] > review.md
"""

import argparse
import json
from typing import Any

from app.config import get_settings
from app.storage import FileRunStore


def _cell(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("|", "\\|")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_id")
    parser.add_argument("--citations", type=int, default=3)
    parser.add_argument("--rows", type=int, default=40)
    args = parser.parse_args()

    record = FileRunStore(get_settings().data_dir / "runs").get(args.run_id)
    if record is None:
        raise SystemExit(f"run {args.run_id} not found")
    resp, meta = record.response, record.response.meta
    out = [f"# Review: {record.request.query}", ""]
    fields = record.request.model_dump(exclude_none=True, exclude={"query"})
    out += [f"- **Run:** `{resp.run_id}` ({record.created_at}) — **status:** `{resp.status.value}`"]
    if fields:
        out.append(f"- **Request fields:** `{json.dumps(fields)}`")
    if meta.interpretation:
        i = meta.interpretation
        out += [
            f"- **Interpretation:** {i.summary}",
            f"- **Planner:** {i.planner_model}, {i.planner_attempts} call(s), "
            f"tokens {meta.llm_usage}",
        ]
        if i.repair_feedback:
            out.append(f"- **Repair feedback:** {' '.join(i.repair_feedback)}")
        if i.plan_diff:
            out.append(f"- **Follow-up diff:** `{json.dumps(i.plan_diff)}`")
    if meta.chart_selection:
        out.append(f"- **Chart:** {meta.chart_selection}")
    if resp.clarification:
        out.append(
            f"- **Clarification:** {resp.clarification.question} {resp.clarification.options}"
        )
    if resp.error:
        out.append(f"- **Error:** `{resp.error.code}` {resp.error.message} {resp.error.details}")
    for note in meta.assumptions:
        out.append(f"- **Assumption:** {note}")
    if meta.cohorts:
        out += [
            "",
            "| Cohort | API params | Matches | Analyzed | Excluded | Missing | Synonym-only |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in meta.cohorts:
            out.append(
                f"| {c.label} | `{_cell(c.api_params)}` | {c.total_matches} | {c.trials_analyzed} "
                f"| {_cell(c.excluded)} | {_cell(c.missing)} | {_cell(c.synonym_matches)} |"
            )
    spec = resp.visualization
    if spec is not None:
        out += ["", f"## {spec.type}: {spec.title}", ""]
        data = (
            [*spec.data.nodes, *spec.data.edges]  # type: ignore[union-attr]
            if spec.type == "network_graph"
            else spec.data
        )
        out += ["| datum | fields | trials | sample citations |", "|---|---|---|---|"]
        for d in list(data)[: args.rows]:  # type: ignore[arg-type]
            extra = {k: v for k, v in (d.model_extra or {}).items()}
            for name in ("id", "label", "source", "target"):
                if hasattr(d, name):
                    extra[name] = getattr(d, name)
            cites = "<br>".join(
                f"[{c.nct_id}]({c.url}): "
                + "; ".join(
                    f"`{e.field_path.split('.')[-1]}`={_cell(e.excerpt)}" for e in c.evidence
                )
                for c in d.citations[: args.citations]
            )
            out.append(f"| {d.datum_id} | {_cell(extra)} | {d.trial_count} | {cites} |")
        if meta.truncation:
            out.append(
                f"\n_Shown {meta.truncation.shown} of {meta.truncation.total}: "
                f"{meta.truncation.rule}_"
            )
    print("\n".join(out))


if __name__ == "__main__":
    main()
