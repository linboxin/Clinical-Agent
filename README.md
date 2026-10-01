# ClinicalTrials.gov Query-to-Visualization Agent

A backend service that turns a clinical-trial question into a **typed visualization spec** computed from ClinicalTrials.gov API v2 data. Every bar, time bucket, node and edge carries the NCT IDs and the **exact source field values** behind it.

```
question (+ optional fields) → LLM plan (validated) → ClinicalTrials.gov → deterministic analytics → chart spec + citations → verification gate
```

- **Brief:** [ASSIGNMENT.md](ASSIGNMENT.md)
- **Design:** [DESIGN.md](DESIGN.md)
- **Status:** v1 MVP (DESIGN §16)

## Quick start

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed automatically.

```bash
uv sync
cp .env.example .env          # then set OPENAI_API_KEY (and OPENAI_BASE_URL if you were given one)
uv run python -m scripts.check_openai   # verifies key, model and one structured plan call
uv run uvicorn app.main:app --reload    # http://localhost:8000/docs
```

```bash
curl -s localhost:8000/v1/visualizations -H 'content-type: application/json' -d '{
  "query": "How has the number of trials for this drug changed over time?",
  "drug_name": "Pembrolizumab"
}'
```

**CLI equivalents:**
- `uv run python -m scripts.ask "How are lung cancer trials distributed across phases?"` runs one question.
- `uv run python -m scripts.run_examples` regenerates `examples/*.json` from real runs.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/visualizations` | Question → response envelope (below) |
| `GET /v1/runs/{run_id}` | A stored response |
| `GET /v1/capabilities` | Supported dimensions, operations, chart types, limits (generated from the field registry) |
| `GET /health` | Liveness and whether the planner is configured |
| `GET /docs`, `GET /openapi.json` | Full request/response JSON Schema |

## Request

Only `query` is required. Field names follow the brief, so its example request works unchanged. Unknown fields are rejected (`422`).

| Field | Type | Notes |
|---|---|---|
| `query` | string, 1–2000 chars | The question |
| `drug_name` | string | Matches intervention name / other names (the registry expands synonyms such as Keytruda / MK-3475) |
| `condition` | string | Registry condition search (phrase, with synonyms) |
| `sponsor` | string | Lead sponsor (phrase) |
| `country` | string | Any listed trial location |
| `trial_phase` | phase or list of phases | `"Phase 3"`, `"3"`, `"PHASE3"`, `"Phase 1/2"` (= Phase 1 or Phase 2) |
| `overall_status` | status or list of statuses | e.g. `"RECRUITING"`, `"active, not recruiting"` |
| `study_type` | enum | `INTERVENTIONAL`, `OBSERVATIONAL`, `EXPANDED_ACCESS` |
| `start_year`, `end_year` | int 1900–2100 | Inclusive; `start_year ≤ end_year`. Applies to the plan's date basis (study start by default) |
| `preferred_visualization` | chart type | Honored only when compatible |
| `citations_per_datum` | int 0–1000, default 10 | Inline citations per datum; `citation_count` always gives the total |

Explicit fields override the prose. If they contradict the question, the response asks which one to use.

## Response

```json
{
  "schema_version": "1.0",
  "run_id": "…",
  "status": "ok | empty | needs_clarification | unsupported | failed",
  "visualization": { "type": "…", "title": "…", "encoding": { }, "data": [ ] },
  "meta": { "interpretation": { }, "cohorts": [ ], "policies": { }, "assumptions": [ ], "source": { } },
  "clarification": { "question": "…", "options": [ ] },
  "error": { "code": "…", "message": "…", "details": [ ] }
}
```

All keys are always present; unused ones are `null`.

| `status` | Set fields | HTTP |
|---|---|---|
| `ok` | `visualization` | 200 |
| `empty` | `visualization` with no data, or all-zero counts | 200 |
| `needs_clarification` | `clarification` (ambiguous, contradictory, zero hits for a named entity, too broad) | 200 |
| `unsupported` | `error` with supported alternatives | 200 |
| `failed` | `error` (`planner_unavailable`, `upstream_unavailable`, `deadline_exceeded`, `output_verification_failed`) | 503 / 504 / 500 |

### Visualization types

| `type` | `encoding` | `data` |
|---|---|---|
| `bar_chart` | `x` (category), `y` (`trial_count`) | rows |
| `grouped_bar_chart` | `x`, `y`, `series` (`cohort` or a second dimension) | rows; a complete grid, zeros included |
| `time_series` | `x` (`year`, temporal), `y`, optional `series` | rows; one per year, zero-filled, plus `estimated_date_count` |
| `network_graph` | `nodes` {id, label, group, size}, `edges` {source, target, weight} | `{nodes: [], edges: []}` |

Each channel has `field`, `type` (nominal / ordinal / quantitative / temporal), `title`, and optionally `unit` and `sort` (explicit category order). `encoding.<channel>.field` names the key to read from each row, as in Vega-Lite.

### Datum and citations

Every row, node and edge has these fields:

```json
{
  "datum_id": "d3", "phase": "Phase 3", "trial_count": 125,
  "citation_count": 125, "citations_truncated": true,
  "citations": [{
    "nct_id": "NCT07809256",
    "url": "https://clinicaltrials.gov/study/NCT07809256",
    "brief_title": "…exact briefTitle…",
    "evidence": [{"field_path": "protocolSection.designModule.phases", "value": ["PHASE3"]}]
  }]
}
```

- `evidence.value` is the exact value at `field_path` in the API record.
- Network edges cite both endpoints.
- Before a response is returned, the verification gate re-resolves every citation against the retrieved records, and checks that counts equal citation counts and that network edges reference existing nodes.

### `meta`

- **`interpretation`:** the accepted plan and a deterministic summary of it.
- **`cohorts[]`:** per cohort:
  - effective filters and the exact API parameters;
  - API total and trials analyzed;
  - exclusions and missing values by reason;
  - whether retrieval was complete.
- **`policies`:** counting rules in plain words (e.g. how multi-phase and multi-country trials count).
- **`assumptions`:** defaults the run relied on.
- **Display hints:** `sort`, `time_granularity`, `truncation`.
- **`cohort_overlap`.**
- **`source`:** API version and registry `dataTimestamp`.
- **`timings_ms`.**

## How it works

1. **Plan.** `gpt-5.4-mini` (configurable) fills a strict `QueryPlan` schema via OpenAI Structured Outputs. The schema's enums come from the field registry, so the model cannot name a field the backend doesn't support. The model never sees trial records and never produces numbers.
2. **Validate.** Semantic checks run, explicit request fields are applied, and the planner gets at most one repair call with the errors listed.
3. **Retrieve.** The plan is compiled to allowlisted API parameters (drug matching is scoped to the intervention fields). The client paginates, with a file cache, a client-side rate limiter and retries. A cohort over 30,000 trials, or zero hits for a named entity, gets a clarification instead of a misleading chart.
4. **Analyze.** `count_by`, `time_trend` and `network` (bipartite or co-occurrence) work on sets of NCT IDs, so every count is exactly its contributor set.
5. **Build + verify.** The typed spec, deterministic title and policies are assembled; then the verification gate runs.

## Development

```bash
uv run pytest            # 71 offline tests (unit, golden counts, pipeline with mocked API, API)
uv run ruff check . && uv run ruff format --check . && uv run mypy app scripts
```

## Status and limitations (v1)

**Built:**
- Count/trend/comparison/geography/network questions.
- Citations and the verification gate.
- File-based run store and cache.

**Planned (DESIGN §16):**
- **v2:** histogram/scatter, an eval harness and model comparison.
- **v3:** persistence, tracing and CI.
- **v4:** full README sections (design decisions, limitations, AI-tool usage).

**Known limitations:**
- Drug and sponsor names are grouped case-insensitively only; brand/generic variants appear as separate nodes.
- Drug co-occurrence means co-listed in a record, not given together.
- Counts reflect the live registry at `meta.source.data_timestamp`.
