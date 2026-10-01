# Response schema: a renderer's guide

Everything a frontend needs to draw any response without guessing. The machine-readable versions are [`schemas/response.schema.json`](schemas/response.schema.json) (JSON Schema), `GET /openapi.json`, and `GET /v1/capabilities`. `app/static/demo.html` is a working reference renderer (~250 lines) that uses only the fields described here.

## 1. Envelope

Every key is always present; unused keys are `null`. Branch on `status`:

| `status` | Draw | Non-null fields | HTTP |
|---|---|---|---|
| `ok` | `visualization` | `visualization`, `meta` | 200 |
| `empty` | `visualization` (no data, or all counts 0) plus the reason in `meta.assumptions` | `visualization`, `meta` | 200 |
| `needs_clarification` | `clarification.question` and its `options` (e.g. as buttons; re-ask with the chosen wording or field) | `clarification` | 200 |
| `unsupported` | `error.message` and `error.details` (the questions the service does support) | `error` | 200 |
| `failed` | `error.code` / `error.message` | `error` | 404 `parent_run_not_found` · 503 `planner_*` / `upstream_unavailable` · 504 `deadline_exceeded` · 500 `output_verification_failed` |

`schema_version` is `"1.1"`. Stored 1.0 records are upgraded on read. `run_id` addresses the stored run (`GET /v1/runs/{run_id}`, `/evidence`, `/trace`) and is used as `parent_run_id` for follow-up questions.

## 2. Common to every chart

```jsonc
"visualization": {
  "type": "bar_chart",            // discriminator: one of the 8 types below
  "title": "Trials by phase — melanoma",
  "encoding": { … },              // channel → field mapping (Vega-Lite style)
  "data": [ … ]                   // rows; for network_graph: {"nodes": [...], "edges": [...]}
}
```

**Channel:** `{field, type, title, unit?, sort?}`.
- `field` is the key to read from each row.
- `type` is `nominal`, `ordinal`, `quantitative` or `temporal`.
- `sort` is either an explicit list of category values in display order (use it as-is, and don't re-sort by value) or the string `"ascending"` / `"descending"`.
- `unit` is for axis titles and tooltips.

**Datum.** Every row, point, node and edge carries:

| Field | Meaning |
|---|---|
| `datum_id` | Stable id (`d1…` rows, `p1…` points, `n1…` nodes, `e1…` edges). Pass it to `/evidence` |
| `trial_count` | Distinct trials behind the datum; this is the plotted value for counts |
| `citation_count` | Always equal to `trial_count` |
| `citations[]` | The first `citations_per_datum` supporting trials (newest NCT IDs first) |
| `citations_truncated` | `true` when `citations` lists fewer than `citation_count` |

**Server counts.** When a cohort is too large to fetch (more than 30,000 trials) and the analysis groups by something the API can count, `meta.count_method` is `"server_count"`:
- every datum's `trial_count` is the registry's exact `totalCount` for the datum's **`source_query`**, a URL you can open to reproduce the number;
- `citations` are 3 sample trials, `citations_truncated` is `true`, and `/evidence` returns only those samples.

Show a "Verify this count" link, and don't offer to load every citation.

**Citation:** `{nct_id, url, brief_title, evidence: [{field_path, excerpt}]}`.
- `excerpt` is the **exact** value at `field_path` in the ClinicalTrials.gov v2 record, verbatim. It is a string for text fields, and a list or number where the API returns one (e.g. `["PHASE1","PHASE2"]`, `250`).
- `evidence` lists everything that puts that trial in this datum: why it's in the cohort (e.g. the matching intervention name, the phase that matched a phase filter) and why it lands in this bar, bucket, bin, point, node or edge.
- The full set for any datum is at `GET /v1/runs/{run_id}/evidence?datum_id=d3&offset=0&limit=100`.

## 3. Chart types

### `bar_chart`
- `encoding.x`: the category (`nominal`, or `ordinal` with an explicit `sort`).
- `encoding.y`: `trial_count` (quantitative, unit `trials`).
- `orientation`: `"horizontal"` means draw categories on the vertical axis (used for long entity names such as sponsors, drugs and countries). `x`/`y` still name the category and value fields.
- Rows: `{datum_id, <x.field>: "Phase 3", trial_count, …citations}`.

### `grouped_bar_chart` / `stacked_bar_chart`
- Same as `bar_chart`, plus `encoding.series` (`field` is `cohort` or a second dimension such as `phase`), whose `sort` gives the series order.
- Rows form a complete grid: one row per (category, series), with explicit zeros.
- `stacked_bar_chart` is only produced when series are mutually exclusive, so a stack's height is a true trial total. Never stack a `grouped_bar_chart`: its series can overlap (a trial in two cohorts, or in several phases under the split policy).

### `pie_chart`
- `encoding.theta`: `trial_count`.
- `encoding.color`: the category, with `sort` as the slice order.
- Only produced for mutually exclusive categories, so the slices sum to the trials that have a value (trials without one are counted in `meta.cohorts[].missing`).

### `time_series`
- `time_granularity`: `"year"`.
- `encoding.x.field` is `year`, with **integer** values (e.g. `2019`). Treat them as calendar years; if your library needs dates, build `YYYY-01-01` in **UTC**.
- `encoding.y`: `trial_count`. The optional `encoding.series` draws one line per series.
- Rows are zero-filled inside the year range. Each row also has `estimated_date_count`: how many of its trials have an anticipated (ESTIMATED) date. A trailing future year is anticipated-only; `meta.assumptions` says so.

### `histogram`
- `encoding.x.field` is `bin`: an ordinal label such as `"100–249"` or `"≥10,000"`, with `sort` giving the bin order.
- `encoding.y`: `trial_count`.
- Each row also has numeric `bin_start` (inclusive) and `bin_end` (exclusive; `null` for the open last bin). `bin_edges` lists the declared edges.
- Bins have **unequal widths** on purpose (enrollment and duration are heavy-tailed), so draw equal-width bars labelled by `bin`, not a linear numeric axis.
- With several cohorts, `encoding.series` is `cohort`; draw them side by side within each bin.

### `scatter_plot`
- One point per trial (`trial_count` 1).
- `encoding.x` / `encoding.y` name per-trial measures, e.g. `start_date` (temporal; ISO strings of varying precision: `"2016"`, `"2016-07"`, `"2016-07-15"`) and `enrollment` (quantitative, unit `participants`). A log or symlog y scale suits enrollment.
- `encoding.series` (optional) is the colour field.
- Rows also carry `nct_id`.
- At most 3,000 points; `meta.truncation` says when more existed.

### `network_graph`
```jsonc
"bipartite": true,                       // two different entity types
"encoding": {
  "nodes": {"id": "id", "label": "label", "group": "entity_type", "size": "trial_count"},
  "edges": {"source": "source", "target": "target", "weight": "trial_count"},
  "directed": false
},
"data": {"nodes": [{"datum_id": "n1", "id": "drug:pembrolizumab", "label": "Pembrolizumab",
                    "entity_type": "drug", "trial_count": 41, …citations}],
         "edges": [{"datum_id": "e1", "source": "lead_sponsor:merck sharp & dohme llc",
                    "target": "drug:pembrolizumab", "relation": "lead_sponsor–drug in the same trial",
                    "trial_count": 12, …citations}]}
```
- Every edge endpoint exists in `nodes`. There are no self-loops, and no duplicate pair in either direction.
- An edge's citations show both endpoints' field values.
- The display is capped at 40 nodes and 150 edges, strongest first; `meta.truncation` gives shown versus total.

## 4. `meta`: context to display

| Field | Use |
|---|---|
| `interpretation.summary` | One deterministic sentence: what was computed, over which cohorts and filters. Show it under the title |
| `interpretation.plan` | The accepted `QueryPlan` (see [`schemas/query_plan.schema.json`](schemas/query_plan.schema.json)) |
| `interpretation.repair_feedback` | Validation or grounding errors the planner corrected (e.g. a misspelt drug) |
| `interpretation.parent_run_id`, `plan_diff` | Follow-ups only: `{"cohorts[0].filters.trial_phase": {"before": null, "after": ["PHASE3"]}}` |
| `chart_selection` | Why this chart type was chosen, including why a preferred type was declined |
| `cohorts[]` | Per cohort: `filters`, exact `api_params`, `total_matches`, `records_fetched`, `trials_analyzed`, `excluded{reason: n}`, `missing{field: n}`, `synonym_matches{filter: n}`, `complete`. In `filters`, the drug, condition, sponsor and country fields are **lists** (a trial matches any value), and `exclude_drug_names`, `exclude_conditions`, `exclude_sponsors` and `exclude_countries` list removed values |
| `interpretation.plan.expansions` | Classes the planner expanded, e.g. `{term: "PD-1 inhibitors", field: "drug_names", members: [...]}`. Also stated in `assumptions` |
| `count_method` | `fetched` (every trial downloaded; full citation sets) or `server_count` (see above) |
| `normalization[]` | Empty unless optional name normalization is enabled (`NAME_NORMALIZER=model`; off by default). Then one entry per normalized dimension: `{dimension, model, names_in, names_sent, names_mapped, names_unmapped, dropped[], merges[{canonical, variants[]}]}`. Raw spellings stay in the citations |
| `cohort_overlap` | `"A ∩ B": n`: trials counted in both cohorts |
| `policies` | Counting rules in plain words (multi-phase, multi-country, drug grouping, bins, …) |
| `assumptions` | Defaults applied ("over time = start year"), partial-year notes, empty-result reasons |
| `units`, `sort`, `time_granularity`, `truncation` | Axis units, sort rule, time bucket, display caps |
| `source` | `api_version`, registry `data_timestamp`, `retrieved_at` |
| `timings_ms`, `llm_usage` | Per-stage latency (`plan`, then `retrieve`, `prepare`, `normalize`, `analyze`, or `count` for server counts, then `build`, `verify`); planner calls and tokens |
