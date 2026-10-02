# Assignment Brief: ClinicalTrials.gov Query-to-Visualization Agent (Backend)

> The take-home problem statement, transcribed from the original PDF (7 pages) and checked line by line against the page images.
> **§1–§8 and the Appendix reproduce the source faithfully.** Anything under *"Our notes"* is our own interpretation, not part of the brief.
> `README.md` is kept free for the final submission README (see §6).

**Time expectation:** ~24 hours
**Tools allowed:** Any programming language, libraries, AI tools, and internet access
**Primary goal:** Build a backend service that converts clinical-trial questions into **structured visualization outputs** backed by **ClinicalTrials.gov API** data.

---

## 1) Problem Overview

You will build an AI-enabled backend that answers questions about clinical trials using the **ClinicalTrials.gov API**. The user will provide a natural-language query, along with optional structured fields (which you may define). Your system must:

1. Interpret the user's question.
2. Retrieve relevant data from [ClinicalTrials.gov](https://clinicaltrials.gov).
3. Identify if a visualization is needed and what type of visualization is suitable for the given question.
4. Produce a **visualization specification** that answers the question.

A frontend is **not required**, but your output must be clear and structured so that a frontend can render the visualization reliably.

## 2) Data Source

Use the **ClinicalTrials.gov Data API** as the authoritative data source.

- API documentation: <https://clinicaltrials.gov/data-api/api>
- You may use any endpoints/fields needed.

## 3) Functional Requirements

### 3.1 Inputs

Your service must accept a request containing:

**Required**

- `query` (string): a natural language question about clinical trials.

**Optional structured fields (candidate-defined)**

You may define an input schema that includes additional parameters. Examples (not required):

- `drug_name`
- `condition/disease`
- `trial_phase`
- `sponsor`
- `country/location`
- `start_year`, `end_year`
- any other fields you find useful

**You must document your request schema** (field names, types, optional/required, validation).

An example request may look like:

```json
{
  "query": "How has the number of trials for this drug changed over time?",
  "drug_name": "Pembrolizumab"
}
```

### 3.2 Outputs

Your service must return a **structured response** describing a visualization.

**Required output components**

1. **Visualization specification**
   - `type`: the visualization type (e.g., `bar_chart`, `time_series`, `network_graph`, etc.)
   - `title`: a human-readable title
   - `encoding`: a clear mapping from fields to visual channels (e.g., x-axis, y-axis, series, nodes/edges)
   - `data`: the data points required to render the visualization
2. **Response metadata**
   - any additional fields needed for the frontend to render appropriately (units, sorting, time granularity, grouping choices, etc.)
   - optional notes about assumptions, filters applied, or query interpretation

**You must document your response schema** so that a frontend engineer can implement a renderer without guessing. If you build a simple frontend demo, that's a bonus, but **backend + structured outputs** are the focus.

Example response (illustrative only):

```json
{
  "visualization": {
    "type": "bar_chart",
    "title": "Trials by Phase for Pembrolizumab",
    "encoding": {
      "x": {"field": "phase"},
      "y": {"field": "trial_count"}
    },
    "data": [
      {"phase": "Phase 1", "trial_count": 32},
      {"phase": "Phase 2", "trial_count": 78}
    ]
  },
  "meta": {
    "filters": {"drug_name": "Pembrolizumab"},
    "source": "clinicaltrials.gov"
  }
}
```

## 4) Visualization Requirements

- The answer to the query must be a **visualization** (via structured specification).
- You may support any visualization types, but your system should aim to support **multiple** types, such as:
  - bar chart / grouped bar chart
  - timeline/time series
  - scatter plot
  - histogram
  - network graph (entities like drugs, sponsors, conditions, investigators, sites)

**Design goal:** Cover as many query types as possible with a **single coherent approach** and support **multiple visualization types**. Submissions that implement richer visualizations (e.g., meaningful network graphs) and broader query coverage will be scored higher than those that only support one chart type.

## 5) Bonus: Deep Citations (Source Traceability)

As a bonus, include **deep citations** from ClinicalTrials.gov to support the values shown in the visualization.

What "deep citations" mean here:

- Each visualized datum (e.g., a bar, time bucket, node/edge weight) includes references to the underlying trial records that contributed to it.
- Each reference includes:
  - `nct_id`
  - an **exact text excerpt** from the API response (or a specific field/value) that supports the datum

Example (illustrative only):

```json
{
  "phase": "Phase 3",
  "trial_count": 41,
  "citations": [
    {
      "nct_id": "NCT01234567",
      "excerpt": "Phase 3 randomized study evaluating pembrolizumab..."
    }
  ]
}
```

This is intentionally challenging, implement as much as is reasonable in the time box.

## 6) Submission Requirements

Submit a zip file containing:

1. **Code:** All source code required to run the service.
2. **README:** must include
   - how to run (install, configure, start)
   - request/response schema documentation (inputs/outputs)
   - key design decisions and tradeoffs
   - limitations and what you would improve with more time
3. **Example Runs:** Provide **3–5 example queries** with the **actual JSON outputs** produced by your system.
4. **(Optional) Demo**
   - a small UI
   - a deployed endpoint
   - a short demo video

## 7) Evaluation Criteria

We will evaluate submissions on:

1. **System Design (35%)**
   - clear, rational design decisions
   - maintainable structure and extensibility
   - sensible handling of real-world API data
2. **AI / Agent Design (20%)**
   - avoid hallucination-prone steps
   - include validation or constraints
   - Sensible planning and reasoning steps, along with appropriate tools
3. **Code Quality (20%)**
   - readability, organization, documentation
   - correctness and robustness
4. **Query and Visualization Coverage (15%)**
   - breadth of supported query types
   - ability to handle multiple question classes without one-off hacks
   - richer visualizations (e.g., meaningful network graphs) score higher than simple single-chart systems
5. **Input/Output Design (10%)**
   - well-structured, unambiguous schemas
   - frontend-friendly visualization specification

**Bonus consideration:** Deep citations/traceability to source records.

## 8) Integrity Note (Use AI Tools Freely)

You may use AI tools and online resources. We care about your **engineering judgment** and **design reasoning**.

In your README, briefly describe:

- which tools you used (if any)
- how you validated correctness
- what parts you designed/implemented deliberately vs generated and adapted

We reward submissions that show evidence of thoughtful construction, testing, and iteration.

---

## Appendix: Example Query Types (Non-Exhaustive)

You do not need to support all of these, but they illustrate the breadth we care about:

**Time trends**
- "How has the number of trials for *[drug]* changed per year since 2015?"
- "How many trials started each year for *[condition]*?"

**Distributions**
- "How are *[condition]* trials distributed across phases?"
- "What are the most common intervention types for *[drug/condition]* trials?"

**Comparisons**
- "Compare phases for trials involving *Drug A* vs *Drug B*."
- "Compare sponsor categories across two conditions."

**Geographic patterns**
- "Which countries have the most recruiting trials for *[condition]*?"

**Relationships/networks**
- "Show a network of sponsors ↔ drugs for *[condition]* trials."
- "Which drugs frequently co-occur in combination studies (drug ↔ drug network)?"

To repeat, these are just some example queries and visualization possible. You are not restricted to these in your assignment.

---
---

# Our notes (interpretation, not part of the brief)

## The end goal in one paragraph

A user asks a clinical-trial question in plain English, optionally with structured filters. The service works out what's being asked, pulls the matching trial records from the ClinicalTrials.gov Data API, aggregates them, picks a suitable chart type, and returns a **JSON visualization spec** (`type`, `title`, `encoding`, `data` plus metadata) that a frontend can render without guessing. The graded deliverable is the backend and its output schema; a UI is optional. The bonus is making every number traceable to the specific trials behind it, each with its NCT ID and an exact excerpt or field value from the API.

```
request ─► 1. interpret ─► 2. retrieve from CT.gov ─► 3. choose chart type ─► 4. build spec ─► response
(query +                                                                                     (visualization
 optional fields)                                                                              + meta
                                                                                              + [bonus] citations)
```

## What the example response tells us

- Top level is `{ "visualization": {...}, "meta": {...} }`.
- `data` is a flat array of records, and `encoding` refers to record keys by name (`{"field": "phase"}`), similar to Vega-Lite.
- `meta` echoes the filters that were applied and names the source.
- The example has no citations, so citations are an addition on top of the core schema (they're the §5 bonus).

## Requirement levels

| Level | Item |
|---|---|
| **Required** | Accept `query`; define and document optional fields with validation |
| **Required** | Interpret → retrieve from CT.gov → choose viz type → produce spec |
| **Required** | Spec has `type`, `title`, `encoding`, `data`, plus response metadata |
| **Required** | Documented request and response schemas |
| **Required** | Submission zip: code, README (run / schemas / design / limitations / AI-tool use / validation / deliberate vs. generated), 3–5 example runs with actual JSON |
| **Strongly rewarded** | Multiple viz types and broad query coverage from **one coherent approach**; meaningful network graphs |
| **Bonus** | Deep citations: per-datum `nct_id` + exact excerpt or field value |
| **Bonus / optional** | Frontend demo, deployed endpoint, demo video |

## What the grading emphasizes

1. **Grounding.** The data source is "authoritative", and 20% of the grade covers avoiding hallucination-prone steps and adding validation. Numbers must come from the API, not from the model. Citations (the bonus) are how you *prove* that.
2. **One coherent approach, not one-off hacks.** This is stated in both §4 and §7. New question types should come from combining general pieces, not from a hand-written handler per question.
3. **Network graphs are explicitly rewarded**, in both §4 and §7.
4. **Schemas are graded twice:** the 10% I/O criterion, and the documentation requirements in §3.1 and §3.2.
5. **Real-world data handling** (missing fields, inconsistent naming, pagination, volume) is part of the 35% system-design score.
6. **Show the work.** Testing, validation, and visible iteration are rewarded, and the README has to explain them.

## Open questions / things we must define

1. **§1 vs §4 tension.** §1 says to *"identify if a visualization is needed"*, but §4 says *"the answer to the query must be a visualization."* The likely reading is that every answer is a visualization and step 3 is mainly about choosing the type. We still need a defined response for questions we can't answer (off-topic, unsupported, or zero matching trials).
2. **"Exact text excerpt … (or a specific field/value)."** A verbatim field value appears to satisfy the citation definition.
3. **Semantics we'll have to pin down and document:** which date counts as a trial's year (start, first-posted, or completion), what "trials for [drug]" means (intervention field vs. any mention), how to count multi-phase trials (e.g. Phase 1/2), how to count multi-country trials, and what "sponsor categories" maps to.
4. **Due date.** "~24 hours" is the time expectation. The brief gives no submission deadline.
