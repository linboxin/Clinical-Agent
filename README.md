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
uv run python -m scripts.check_openai     # verifies the key, the model (default gpt-5.4), and one structured plan call
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
| `uv run python -m evals.run [--model gpt-5.4-nano] [--repeats 3]` | Planner eval set (41 cases, deterministic scoring) |
| `uv run pytest` | 124 offline tests (no key, no network) |

`OPENAI_API_KEY` is only needed for the planner. Everything else (retrieval, analytics, citations, the gate, the demo, the tests) runs without it.

## Example runs

The files in [`examples/`](examples/) are actual outputs of `scripts/run_examples.py`, using the real planner (`gpt-5.4`) and live ClinicalTrials.gov data from 2026-10-01. Each is saved verbatim (request plus response, schema 1.1) with 3 inline citations per datum. **01–05 are the five headline examples** (brief §6). 06–14 show the remaining chart types and statuses, drug-class expansion with exclusion, and server-side counting.

| # | Question | Result |
|---|---|---|
| [01](examples/01_time_trend_brief_example.json) | *How has the number of trials for this drug changed over time?* + `drug_name: Pembrolizumab` (the brief's example) | `time_series`: 2,631 trials, 2008–2027 (later years are anticipated starts) |
| [02](examples/02_comparison_two_drugs.json) | *Compare phases for trials involving pembrolizumab vs nivolumab in melanoma.* | `grouped_bar_chart`: 351 vs 329 trials, 8 phase groups each |
| [03](examples/03_geographic_recruiting.json) | *Which countries have the most recruiting trials for breast cancer?* | `bar_chart`, horizontal: top 25 of 81 countries; United States 915, China 647, Italy 195 |
| [04](examples/04_network_sponsor_drug.json) | *Show a network of sponsors and drugs for phase 3 melanoma trials.* | `network_graph`, bipartite: 221 trials; 40 nodes and 55 of 324 edges; drug names normalized (135 changed) |
| [05](examples/05_histogram_enrollment.json) | *What is the enrollment size distribution of recruiting Alzheimer's trials?* | `histogram`: 601 trials in 11 declared bins |
| [06](examples/06_network_drug_cooccurrence.json) | *Which drugs frequently co-occur in combination studies for multiple myeloma?* | `network_graph`, co-occurrence: 4,049 trials; dexamethasone is the hub (782); BTZ / Velcade → bortezomib |
| [07](examples/07_scatter_enrollment_vs_start.json) | *Plot enrollment vs start date for phase 3 psoriasis trials, by sponsor type* | `scatter_plot`: 510 points, coloured by sponsor class |
| [08](examples/08_trend_split_by_phase.json) | *How has the phase mix of interventional obesity trials changed since 2010?* | `time_series`, one line per phase: 10,160 trials |
| [09](examples/09_pie_preferred.json) | *What share of COVID-19 vaccine trials are randomized?* + `preferred_visualization: pie_chart` | `pie_chart` (honored: allocation is exclusive), 627 trials |
| [10](examples/10_needs_clarification.json) | *How many trials has this drug had per year?* | `needs_clarification`: "Which drug do you want to analyze per year?" |
| [11](examples/11_unsupported.json) | *Which melanoma drug has the best overall survival?* | `unsupported`, with the supported alternatives |
| [12](examples/12_follow_up_of_03.json) | *Same, but only phase 3 trials.* + `parent_run_id` of 03 | `bar_chart`; `plan_diff` = `trial_phase: null → [PHASE3]`; 206 trials |
| [13](examples/13_drug_class_with_exclusion.json) | *Excluding Keytruda, which PD-1 inhibitors have the most Phase 3 trials?* | `bar_chart`: the class is expanded into 5 grounded members and Keytruda is excluded; nivolumab 152, tislelizumab 80, toripalimab 63, cemiplimab 11, dostarlimab 10 |
| [14](examples/14_whole_registry_server_counts.json) | *How are all registered clinical trials distributed across phases?* | `bar_chart`, **server counts** over all 605,357 trials; each bar links a `source_query` that reproduces it |

Every example passed the verification gate on all of its citations. Separate live re-fetches of cited trials (§ Validation) matched every excerpt.

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
  "schema_version": "1.1",
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
3. **Ground.** One live `countTotal` call per cohort, plus one per member of an expanded class (an invented member goes back to the planner). A cohort with zero hits gets a per-entity probe, which separates an unknown term (`pembrolizumabb`) from a genuine zero. Unknown terms go back to the planner as feedback for its **one** repair call. A cohort over 30k trials is detected here, *before* anything is fetched, and takes the server-count path below.
4. **Retrieve.** Allowlisted, quoted API parameters (drug matching is scoped to the intervention fields; value lists become `OR`, exclusions become `NOT`). Pagination with field projection, a page cache keyed by the registry's `dataTimestamp`, a client-side rate limiter (the API returns 429 on bursts) and bounded retries.
   - **Above the cap:** if the analysis groups by something the API can count, each bucket is counted **on the server**: the exact `totalCount` plus 3 sample citations and a `source_query` URL. Otherwise the user is asked to narrow.
5. **Prepare, normalize and analyze.** Exact filters and exclusions are re-checked on every record. For drug and condition groupings, a small model normalizes names under guardrails (see below). Each trial records *why it is in the cohort*. The operators work on sets of NCT IDs, so a count is always the size of its contributor set.
6. **Build and verify.** Code picks the chart type (§9 of DESIGN). The title, summary and policies are deterministic text. The gate re-resolves **every cited `field_path`** in the retrieved record and compares it with the `excerpt`, checks counts against citation counts, and checks network integrity. A failure returns `failed`; nothing is patched.

## Key design decisions and tradeoffs

| Decision | Why | Tradeoff |
|---|---|---|
| The LLM only writes a structured plan; code computes everything | Numbers, titles and citations can't be hallucinated; the plan is inspectable and testable | Questions outside the plan language get "unsupported" instead of a free-form answer |
| One operator set + a field registry (5 operators × 13 dimensions × 3 measures → 8 chart types) | New question classes are registry entries, not handlers; the prompt and `/capabilities` are generated from the registry | Some phrasing needs a clarification instead of a creative interpretation |
| A composable plan language (any-of lists, exclusions, class expansion with grounded members, "count only listed values", declared unhandled constraints) | A PD-1-style question composes from general blocks, with no per-question code; nothing is silently dropped, and titles are built from filters, never model text | Class membership comes from model knowledge. It's shown in `meta.assumptions`, and every member is checked against the registry |
| Server-side counting above the cap (idea adopted after comparing with another implementation) | Whole-registry questions get exact answers in seconds, each bar reproducible from its `source_query` | Citations are 3 samples per datum; local re-checks and normalization don't apply (stated in `meta`) |
| Model-based name normalization with guardrails (also adopted from that comparison) | Keytruda / MK-3475 / "pembrolizumab 200 mg" group as one drug; combination strings split | A model judges identity. It only maps the names it's given; answers are validated and cached; raw values stay in the citations; merges are listed in `meta.normalization`; `NAME_NORMALIZER=off` disables it |
| Code chooses the chart; preferences must be compatible | A pie or stacked bar over overlapping groups would double-count, so the rules are also validation | Less stylistic freedom |
| A grounding tool in the agent loop (live hit counts → one repair) | Fixes misspellings and invented entities without user round-trips, and stays bounded (≤ 2 model calls) | One extra cheap API call per cohort; a typo that exists in the registry (one trial lists "pembrolizumb") passes grounding |
| Field-scoped drug search instead of `query.intr` | Measured: 11% of `query.intr` hits for pembrolizumab only mention it (e.g. prior therapy) | Relies on registry synonym expansion; those trials are counted in `synonym_matches` |
| Full retrieval up to 30k trials per cohort; server counts above that | Exact counts with complete contributor sets wherever the cohort fits | Above the cap, citations are samples, and shapes the API can't count (drug rankings, networks) must be narrowed |
| A deterministic drug key (dose, salt, ®) underneath the model normalizer, not MeSH | Measured: raw names split "erlotinib" / "erlotinib hydrochloride", and MeSH mixes in non-drugs ("Radiotherapy"). The key works with no model call; the model layer adds brand/code → generic | Names outside the 600 most frequent keep the deterministic key only |
| File store, file cache, in-process traces; no DB or Docker | Runs with `uv` alone; persistence and infrastructure aren't graded | Single-instance; the Protocol seams are where Postgres, S3 and OTLP would go |
| Deterministic evals (field matching), no LLM judge | Reproducible, cheap, can't hallucinate | Only checks what each case specifies |

## Validation

- **124 offline tests** (`uv run pytest`):
  - hand-computed golden counts and contributor sets for every operator;
  - chart-selection rules and normalization;
  - a mocked-registry pipeline covering pagination, grounding, too-broad, upstream failure, typo → repair, follow-up diff, and the gate catching a tampered excerpt or count;
  - API routes, path traversal, and strict-schema validity of `QueryPlan` for OpenAI.
- **The gate on every response:** every cited excerpt is re-resolved against the record it came from.
- **Live runs against ClinicalTrials.gov** (hand-written plans via `--plan`): histogram (Alzheimer's, 601 trials), scatter (psoriasis, 516), phase mix over time (obesity, 10,160), investigator ↔ site network (glioblastoma), pembrolizumab vs nivolumab in melanoma. All passed the gate.
- **Spot checks against the live API.** The obesity 2026 bucket (782) matches an independent `countTotal` query exactly. `scripts/audit_citations.py` re-fetched 124 cited trials live (smoke runs plus examples 01, 04, 06, 07, 12 and 13), and **485/485 cited excerpts matched**.
- **Visual check of the demo renderer** (headless Chrome screenshots). This caught a UTC/local-time shift in the time axis, which was a renderer bug; the data was correct.
- **Planner evals** ([evals/results](evals/results/README.md)): 34 cases × 3 repeats on prompt v5 (below), then 41 cases on v7; deterministic scoring.

  | Model (prompt v5) | Pass | Stable across repeats | Median latency |
  |---|---|---|---|
  | gpt-5.4-nano | 97/102 | 30/34 | 2.2 s |
  | gpt-5.4-mini | 101/102 | 32/34 | 2.0 s |
  | **gpt-5.4 (default)** | **102/102** | **34/34** | 2.5 s |
  | gpt-5.4-mini without the repair call (E2) | 99/102 | 34/34 | 2.1 s |

  With the plan-language cases added (41 cases, prompt v7): **gpt-5.4 passes 123/123**, and mini 117/123.

  E2 shows the grounding repair loop at work: without it, the misspelling case (`pembrolizumabb`) fails 3/3; with it, it is corrected 3/3. The eval failures also drove the prompt from v3 to v5: keeping the question's value when it conflicts with a field, wording cues for the date basis, and concrete clarification options.

## Limitations and what I'd improve

- **The eval set is small (41 cases) and was written by the builder,** so 100% means "no known regressions", not general accuracy. Next: grow it from real user questions and review traces, and add adversarial paraphrases.
- **No free-text keyword filter.** "COVID-19 vaccine trials" becomes `conditions=[COVID-19]` plus `drug_names=[vaccine]` (an intervention-name phrase), which works but is indirect. Constraints the language can't express (e.g. "placebo-controlled") get a clarification rather than being dropped.
- **Server counts cover common shapes only:** one countable dimension (or a year trend), ≤ 48 queries. Drug rankings and networks over 30k+ trials still ask the user to narrow.
- **Name normalization is model judgement.** It's guarded and disclosed, but a wrong merge would change a grouping. Citations still show the raw names, so it's auditable.
- **Registry search semantics leak through.** "Alzheimer's" and "Alzheimer's disease" expand to different trial sets (434 vs 601 recruiting). The exact `api_params` are in `meta.cohorts`, but the user isn't warned.
- **Scale:** 30k trials per cohort, fetched synchronously (~25 s at the cap). Next: async jobs for large cohorts, so drug rankings and networks over 30k+ trials can be fully retrieved instead of narrowed.
- **Entity resolution:** sponsor subsidiaries ("Merck Sharp & Dohme LLC" vs "Merck KGaA") and site spellings aren't merged; only drug and condition names are normalized.
- **Semantics:** co-occurrence means co-listed, not co-administered (it would need arm-level data). Status is current, not historical. Enrollment mixes actual and estimated counts (each citation states which).
- **Typos that exist in the registry** pass grounding (e.g. a single trial lists "pembrolizumb"). A low-hit-count heuristic or a spelling suggestion could flag them.
- **Single instance:** file storage, no auth. The production path (Postgres, S3, job queue, auth, OTLP export to Phoenix) is in DESIGN §16.

## How this was built (brief §8)

- **Tools:** Claude Code, two sessions.
  - The first built the v1 MVP (contracts, registry, client, three operators, gate, 71 tests).
  - The second reviewed the design against the brief and the live API, then built v3: histogram and scatter, chart rules, the grounding repair loop, membership evidence, traces and evidence endpoints, follow-ups, evals, the demo, and the review and audit scripts.
- **Design:** three written iterations (v1 → v2 → v3), each change justified in [DESIGN.md Appendix A](DESIGN.md#appendix-a-changes-from-v2-and-v1). The OpenAI-only constraint and the design-first workflow were set by the author.
- **Comparison and testing drove iteration:**
  - a user test of "Excluding Keytruda, which PD-1 inhibitors…" exposed a confident wrong answer, which led to the general plan-language blocks;
  - comparing with another implementation of this brief led to adopting server-side counting and model-based name normalization, with stricter guardrails;
  - a parallel Claude Code agent redesigned the `/demo` page while the backend changed; its screenshots also caught a schema-migration bug.
- **Iteration driven by measurement:**
  - planner prompt v3 → v7 (each change answers a specific eval failure);
  - model choice by experiment E1;
  - repair-loop value measured by E2;
  - the time-axis fix found by looking at rendered output.
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
