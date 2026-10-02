"""Audit a stored run's citations against the live API: re-fetch a sample of cited trials and
check that every cited field path still holds the cited excerpt. Independent of the in-process
gate (which checks against the records fetched during the run), so it also detects drift
since retrieval.

    uv run python -m scripts.audit_citations <run_id> [--sample 25]
"""

import argparse
import asyncio
import json
import random

from app.config import get_settings
from app.factory import create_ctgov_client, create_http_client
from app.registry import API_FIELDS, get_path
from app.storage import FileRunStore


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_id")
    parser.add_argument("--sample", type=int, default=25, help="trials to re-fetch")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    settings = get_settings()
    record = FileRunStore(settings.data_dir / "runs").get(args.run_id)
    spec = record.response.visualization if record else None
    if spec is None:
        raise SystemExit(f"run {args.run_id} not found or has no visualization")
    data = (
        [*spec.data.nodes, *spec.data.edges]  # type: ignore[union-attr]
        if spec.type == "network_graph"
        else spec.data
    )
    checks: dict[str, list[tuple[str, object]]] = {}
    for datum in data:  # type: ignore[union-attr]
        for c in datum.citations:
            checks.setdefault(c.nct_id, []).extend((e.field_path, e.excerpt) for e in c.evidence)
    ids = sorted(checks)
    random.Random(args.seed).shuffle(ids)
    ids = ids[: args.sample]

    http = create_http_client(settings)
    ok = drift = 0
    try:
        client = create_ctgov_client(settings, http)
        for nct in ids:
            live = await client.get_study(nct, API_FIELDS)
            unique = {json.dumps(pair, sort_keys=True): pair for pair in checks[nct]}
            for path, excerpt in unique.values():
                actual = get_path(live, path)
                if actual == excerpt:
                    ok += 1
                else:
                    drift += 1
                    print(f"DRIFT {nct} {path}: cited {excerpt!r}, live {actual!r}")
    finally:
        await http.aclose()
    print(f"audited {len(ids)} trials: {ok} excerpts match live data, {drift} differ")


if __name__ == "__main__":
    asyncio.run(main())
