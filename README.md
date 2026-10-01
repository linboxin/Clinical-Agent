# ClinicalTrials.gov Query-to-Visualization Agent

A backend service that turns a clinical-trial question into a **typed visualization spec** computed from ClinicalTrials.gov API v2 data. Every bar, time bucket, histogram bin, scatter point, node and edge carries the NCT IDs and the **exact source field values** behind it.

```
question (+ optional fields) → LLM plan (validated, grounded on live hit counts) → ClinicalTrials.gov
  → deterministic analytics → chart spec + deep citations → verification gate → JSON
```

- **Brief:** [ASSIGNMENT.md](ASSIGNMENT.md)
- **Design (as built):** [DESIGN.md](DESIGN.md)
- **Renderer guide:** [docs/response-schema.md](docs/response-schema.md)
- **JSON Schemas:** [docs/schemas/](docs/schemas/)

## Quick start

Requires [uv](https://docs.astral.sh/uv/); Python 3.12 is installed automatically.

```bash
uv sync
cp .env.example .env                      # set OPENAI_API_KEY (and OPENAI_BASE_URL if you were given one)
uv run python -m scripts.check_openai     # verifies the key, the model, and one structured plan call
uv run uvicorn app.main:app --port 8000   # API docs: http://localhost:8000/docs · demo UI: /demo
```

```bash
curl -s localhost:8000/v1/visualizations -H 'content-type: application/json' -d '{
  "query": "How has the number of trials for this drug changed over time?",
  "drug_name": "Pembrolizumab"
}'
```

| Command | What it does |
|---|---|
| `uv run python -m scripts.ask "<question>" [--field drug_name=X] [--summary]` | One question through the full pipeline, from the CLI |
| `uv run python -m scripts.ask "<label>" --plan plan.json --summary` | Replays a hand-written `QueryPlan` instead of calling the model (still validated and grounded); needs no API key |
| `uv run python -m scripts.run_examples` | Regenerates `examples/*.json` from real runs |
| `uv run python -m scripts.review_run <run_id>` | Markdown review sheet for a stored run |
| `uv run python -m scripts.audit_citations <run_id>` | Re-fetches cited trials live and re-checks every cited excerpt |
| `uv run python -m evals.run [--model gpt-5.4-nano] [--repeats 3]` | Planner eval set (34 cases, deterministic scoring) |
| `uv run pytest` | 106 offline tests (no key, no network) |

`OPENAI_API_KEY` is only needed for the planner. Everything else (retrieval, analytics, citations, the gate, the demo, the tests) runs without it.

## Example runs

The files in [`examples/`](examples/) are produced by `scripts/run_examples.py`: real requests through the real pipeline, saved verbatim (request plus response) with 3 inline citations per datum.

| # | Question | Expected chart |
|---|---|---|
| 01 | *How has the number of trials for this drug changed over time?* + `drug_name: Pembrolizumab` (the brief's example) | `time_series` |
| 02 | *Compare phases for trials involving pembrolizumab vs nivolumab in melanoma.* | `grouped_bar_chart` |
| 03 | *Which countries have the most recruiting trials for breast cancer?* | `bar_chart` (horizontal) |
| 04 | *Show a network of sponsors and drugs for phase 3 melanoma trials.* | `network_graph` (bipartite) |
| 05 | *What is the enrollment size distribution of recruiting Alzheimer's trials?* | `histogram` |
| 06–12 | Drug co-occurrence network, scatter, phase mix over time, preferred pie, clarification, unsupported, and a follow-up of 03 | various |

> **Status:** the example JSON files still have to be generated. The OpenAI key provided during development was rejected by OpenAI (`401 invalid_api_key`), so no run has used the real model yet. Every stage after planning has been run live against ClinicalTrials.gov with hand-written plans (`--plan`); see "Validation" below.

## Request

`POST /v1/visualizations`. Only `query` is required. Field names follow the brief, so its example request works unchanged. Unknown fields are rejected (`422`). The full schema is in [docs/schemas/request.schema.json](docs/schemas/request.schema.json).

| Field | Type | Notes |
|---|---|---|
| `query` | string, 1–2,000 chars | The question |
| `drug_name` | string | Matches intervention name or other names; the registry expands synonyms (Keytruda = MK-3475 = pembrolizumab) |
| `condition` | string | Registry condition search (phrase, with synonyms) |
| `sponsor` | string | Lead sponsor (phrase) |
| `country` | string | Any listed trial location |
| `trial_phase` | phase or list | `"Phase 3"`, `"3"`, `"III"`, `"PHASE3"`, `"Phase 1/2"` (= Phase 1 or Phase 2), `"N/A"` |
| `overall_status` | status or list | e.g. `"RECRUITING"`, `"active, not recruiting"` |
| `study_type` | enum | `INTERVENTIONAL`, `OBSERVATIONAL`, `EXPANDED_ACCESS` |
| `start_year`, `end_year` | int 1900–2100 | Inclusive, with `start_year ≤ end_year`; applies to the plan's date basis (study start by default) |
| `preferred_visualization` | chart type | Honored only when compatible (e.g. no pie chart over overlapping groups) |
| `citations_per_datum` | int 0–100, default 5 | Inline citations; the full set is at `/v1/runs/{id}/evidence` |
| `parent_run_id` | UUID | Follow-up: "now only phase 3" refines that run's plan |

Explicit fields override the prose. If they contradict it ("pembrolizumab trials…" with `drug_name: nivolumab`), the response asks which one to use.

## Response

```jsonc
{
  "schema_version": "1.0",
  "run_id": "…",
  "status": "ok",                      // ok | empty | needs_clarification | unsupported | failed
  "visualization": {
    "type": "bar_chart",               // + grouped/stacked bar, pie, time_series, histogram, scatter_plot, network_graph
    "title": "Trials by phase — melanoma",
    "orientation": "vertical",
    "encoding": {
      "x": {"field": "phase", "type": "ordinal", "title": "Phase", "sort": ["Early Phase 1", "Phase 1", "…"]},
      "y": {"field": "trial_count", "type": "quantitative", "title": "Trials", "unit": "trials"}
    },
    "data": [{
      "datum_id": "d3", "phase": "Phase 3", "trial_count": 24, "citation_count": 24, "citations_truncated": true,
      "citations": [{
        "nct_id": "NCT…", "url": "https://clinicaltrials.gov/study/NCT…", "brief_title": "<exact briefTitle>",
        "evidence": [
          {"field_path": "protocolSection.armsInterventionsModule.interventions[0].name", "excerpt": "Pembrolizumab"},
          {"field_path": "protocolSection.conditionsModule.conditions[0]", "excerpt": "Melanoma"},
          {"field_path": "protocolSection.designModule.phases", "excerpt": ["PHASE3"]}
        ]
      }]
    }]
  },
  "meta": {
    "interpretation": {"summary": "…", "plan": {}, "planner_model": "gpt-5.4-mini", "planner_attempts": 1, "repair_feedback": []},
    "chart_selection": "counts per category → bar_chart",
    "cohorts": [{"label": "…", "api_params": {}, "total_matches": 351, "trials_analyzed": 351, "excluded": {}, "missing": {"phase": 13},
                 "synonym_matches": {"drug_name": 10}, "complete": true}],
    "policies": {}, "assumptions": [], "units": {}, "sort": "…", "truncation": null,
    "source": {"api_version": "2.0.5", "data_timestamp": "…", "retrieved_at": "…"}, "timings_ms": {}, "llm_usage": {}
  },
  "clarification": null,
  "error": null
}
```

The values above are illustrative. **[docs/response-schema.md](docs/response-schema.md)** documents every chart type for a frontend engineer: channels, row fields, sort rules, zero-fill, open-ended bins, network integrity, and the metadata to display. `GET /demo` is a working reference renderer (Vega-Lite + d3) that uses only those documented fields.

**Other endpoints:**
- `GET /v1/runs/{id}`, `/evidence?datum_id=…`, `/trace`;
- `GET /v1/capabilities`, generated from the field registry;
- `GET /health`;
- `GET /docs` (OpenAPI).

## How it works

1. **Plan.** The LLM fills a strict `QueryPlan` (OpenAI Structured Outputs). Its enums are generated from the field registry, so the model cannot name a field or operation the backend doesn't implement. The plan holds cohorts and filters, one of 5 operators (`count_by`, `time_trend`, `histogram`, `scatter`, `network`), dimensions, measures, a time scope and a phase policy. The model may instead return a clarification or "unsupported".
2. **Validate.** Semantic rules run (e.g. networks need entity dimensions, scatter colour must be single-valued, unused fields must be null). Explicit request fields are merged, and contradictions trigger a clarification.
3. **Ground.** One live `countTotal` call per cohort. A cohort with zero hits gets a per-entity probe, which separates an unknown term (`pembrolizumabb`) from a genuine zero. Unknown terms go back to the planner as feedback for its **one** repair call. A cohort over 30k trials is asked to narrow *before* anything is fetched.
4. **Retrieve.** Allowlisted, quoted API parameters (drug matching is scoped to the intervention fields). Pagination with field projection, a page cache keyed by the registry's `dataTimestamp`, a client-side rate limiter (the API returns 429 on bursts) and bounded retries.
5. **Prepare and analyze.** Exact filters are re-checked on every record. Each trial records *why it is in the cohort*. The operators work on sets of NCT IDs, so a count is always the size of its contributor set.
6. **Build and verify.** Code picks the chart type (§9 of DESIGN). The title, summary and policies are deterministic text. The gate re-resolves **every cited `field_path`** in the retrieved record and compares it with the `excerpt`, checks counts against citation counts, and checks network integrity. A failure returns `failed`; nothing is patched.

## Key design decisions and tradeoffs

| Decision | Why | Tradeoff |
|---|---|---|
| The LLM only writes a structured plan; code computes everything | Numbers, titles and citations can't be hallucinated; the plan is inspectable and testable | Questions outside the plan language get "unsupported" instead of a free-form answer |
| One operator set + a field registry (5 operators × 13 dimensions × 3 measures → 8 chart types) | New question classes are registry entries, not handlers; the prompt and `/capabilities` are generated from the registry | Some phrasing needs a clarification instead of a creative interpretation |
| Code chooses the chart; preferences must be compatible | A pie or stacked bar over overlapping groups would double-count, so the rules are also validation | Less stylistic freedom |
| A grounding tool in the agent loop (live hit counts → one repair) | Fixes misspellings and invented entities without user round-trips, and stays bounded (≤ 2 model calls) | One extra cheap API call per cohort; a typo that exists in the registry (one trial lists "pembrolizumb") passes grounding |
| Field-scoped drug search instead of `query.intr` | Measured: 11% of `query.intr` hits for pembrolizumab only mention it (e.g. prior therapy) | Relies on registry synonym expansion; those trials are counted in `synonym_matches` |
| Full retrieval up to 30k trials per cohort, then clarify | Exact counts with complete contributor sets | Unscoped questions (600k+ trials) must be narrowed; counting via `countTotal` facets is on the production path |
| Deterministic drug canonicalization (dose, salt, ®) instead of MeSH or an LLM | Measured: raw names split "erlotinib" / "erlotinib hydrochloride", and MeSH mixes in non-drugs ("Radiotherapy") | Brand and generic names stay separate nodes |
| File store, file cache, in-process traces; no DB or Docker | Runs with `uv` alone; persistence and infrastructure aren't graded | Single-instance; the Protocol seams are where Postgres, S3 and OTLP would go |
| Deterministic evals (field matching), no LLM judge | Reproducible, cheap, can't hallucinate | Only checks what each case specifies |

## Validation

- **106 offline tests** (`uv run pytest`):
  - hand-computed golden counts and contributor sets for every operator;
  - chart-selection rules and normalization;
  - a mocked-registry pipeline covering pagination, grounding, too-broad, upstream failure, typo → repair, follow-up diff, and the gate catching a tampered excerpt or count;
  - API routes, path traversal, and strict-schema validity of `QueryPlan` for OpenAI.
- **The gate on every response:** every cited excerpt is re-resolved against the record it came from.
- **Live runs against ClinicalTrials.gov** (hand-written plans via `--plan`): histogram (Alzheimer's, 601 trials), scatter (psoriasis, 516), phase mix over time (obesity, 10,160), investigator ↔ site network (glioblastoma), pembrolizumab vs nivolumab in melanoma. All passed the gate.
- **Spot checks against the live API.** The obesity 2026 bucket (782) matches an independent `countTotal` query exactly. `scripts/audit_citations.py` re-fetched 25 cited trials live, and **86/86 cited excerpts matched**.
- **Visual check of the demo renderer** (headless Chrome screenshots). This caught a UTC/local-time shift in the time axis, which was a renderer bug; the data was correct.
- **Planner evals:** 34 cases. Not run yet: they need a working OpenAI key.

## Limitations and what I'd improve

- **Planner quality is unmeasured** until the evals run with a working key. Next steps: run E1 (nano / mini / full) and E2 (repair on/off), then add each miss as a case.
- **Scale:** 30k trials per cohort, fetched synchronously (~25 s at the cap). Next: count-only facet queries for unscoped questions, and async jobs for large cohorts.
- **Entity resolution:** brand and generic names, sponsor subsidiaries ("Merck Sharp & Dohme LLC" vs "Merck KGaA") and site spellings aren't merged. Next: MeSH-backed drug identity plus a reviewed alias table.
- **Semantics:** co-occurrence means co-listed, not co-administered (it would need arm-level data). Status is current, not historical. Enrollment mixes actual and estimated counts (each citation states which).
- **Typos that exist in the registry** pass grounding (e.g. a single trial lists "pembrolizumb"). A low-hit-count heuristic or a spelling suggestion could flag them.
- **Single instance:** file storage, no auth. The production path (Postgres, S3, job queue, auth, OTLP export to Phoenix) is in DESIGN §16.

## How this was built (brief §8)

- **Tools:** Claude Code, two sessions.
  - The first built the v1 MVP (contracts, registry, client, three operators, gate, 71 tests).
  - The second reviewed the design against the brief and the live API, then built v3: histogram and scatter, chart rules, the grounding repair loop, membership evidence, traces and evidence endpoints, follow-ups, evals, the demo, and the review and audit scripts.
- **Design:** three written iterations (v1 → v2 → v3), each change justified in [DESIGN.md Appendix A](DESIGN.md#appendix-a-changes-from-v2-and-v1). The OpenAI-only constraint and the design-first workflow were set by the author.
- **Deliberate choices, made before coding and checked against the live API:**
  - the model-proposes / code-decides split, with chart selection in code;
  - contributor-set citations re-verified by the gate;
  - field-scoped drug search (measured 2,964 → 2,631);
  - phrase-quoted conditions;
  - the 30k cap (measured page cost);
  - the client-side rate limiter (the API returns 429 on bursts);
  - token matching for membership evidence (the literal-substring version mislabelled 490 of 601 Alzheimer's trials as synonym-only; token matching brought it to 130).
- **Validated rather than trusted:** see "Validation". Generated code is accepted only when it passes the golden tests, the gate and the live checks. The commit history shows each step.

## Repository

```
app/        service code (contracts, planner, ctgov client, analytics, viz, storage, telemetry, demo page)
evals/      planner eval cases, runner, deterministic scoring
scripts/    ask, run_examples, review_run, audit_citations, export_schemas, check_openai
tests/      offline test suite
docs/       response-schema.md (renderer guide), schemas/*.schema.json
examples/   saved real request/response pairs
```

Development: `uv run ruff check . && uv run ruff format --check . && uv run mypy app evals && uv run pytest`. CI runs the same in `.github/workflows/ci.yml`.
