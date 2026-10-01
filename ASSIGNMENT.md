# Assignment Brief: ClinicalTrials.gov Query-to-Visualization Agent (Backend)

> This is the problem statement, transcribed and organized from the take-home PDF.
> It covers **what** we have to build. **How** we build it goes in a separate design doc.
> `README.md` is kept free for the final submission README (see §6).

| | |
|---|---|
| **Time expectation** | ~24 hours |
| **Tools allowed** | Any language, libraries, AI tools, and internet access |
| **Primary goal** | A backend service that turns clinical-trial questions into **structured visualization outputs** backed by **ClinicalTrials.gov API** data |

> ⚠️ **Source gap:** The screenshots skip from the end of page 2 to the citation bullets on page 3. Pages 3 and 4 were the same image. That means we're missing the **illustrative example response for §3.2**, all of **§4**, and the **heading and opening of §5** (the citation bullets below look like its end). See [Open questions](#open-questions--gaps).

---

## The end goal in one paragraph

A user sends a natural-language question about clinical trials, such as *"How has the number of Pembrolizumab trials changed since 2015?"*, optionally with structured filters. The service works out what's being asked, pulls the relevant trial records from the ClinicalTrials.gov Data API, decides whether a chart helps and which kind, and returns a **JSON visualization spec**. The spec has to be clear enough that a frontend can render it without guessing. Every number in it (every bar, time bucket, node, and edge) must **trace back to the specific trials behind it**, each with its NCT ID and an exact excerpt from the API response. A frontend is optional. The graded deliverable is the backend and its structured output.

```
 ┌──────────────────────┐
 │ Request              │  query (required) + optional structured fields
 └──────────┬───────────┘
            ▼
 1. Interpret the question
            ▼
 2. Retrieve relevant data from ClinicalTrials.gov (authoritative source)
            ▼
 3. Decide IF a visualization is needed, and WHICH type fits
            ▼
 4. Produce a visualization specification that answers the question
            ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Response                                                     │
 │  • visualization: type, title, encoding, data                │
 │  • metadata: render hints + assumptions/filters/interpretation│
 │  • per-datum citations: nct_id + exact excerpt               │
 └──────────────────────────────────────────────────────────────┘
```

---

## 1) Problem overview

Build an AI-enabled backend that answers clinical-trial questions using the **ClinicalTrials.gov API**. Each request carries a natural-language query plus optional structured fields, which we get to define. The system must:

1. **Interpret** the user's question.
2. **Retrieve** relevant data from [ClinicalTrials.gov](https://clinicaltrials.gov).
3. **Identify** whether a visualization is needed, and which type suits the question.
4. **Produce** a visualization specification that answers the question.

A frontend is **not required**. The output must still be clear and structured enough for a frontend to render the visualization reliably.

## 2) Data source

- The **ClinicalTrials.gov Data API** is the authoritative data source.
- API documentation: <https://clinicaltrials.gov/data-api/api>
- Any endpoints and fields may be used.

## 3) Functional requirements

### 3.1 Inputs

**Required**

| Field | Type | Description |
|---|---|---|
| `query` | string | A natural-language question about clinical trials |

**Optional structured fields (we define these)**

The brief lists these as examples, none of them required:

- `drug_name`
- `condition` / `disease`
- `trial_phase`
- `sponsor`
- `country` / `location`
- `start_year`, `end_year`
- any other fields we find useful

**Must document the request schema:** field names, types, optional/required, and validation rules.

Example request (from the brief):

```json
{
  "query": "How has the number of trials for this drug changed over time?",
  "drug_name": "Pembrolizumab"
}
```

Note that the query says *"this drug"* and only the structured field names the drug. The system has to **combine the free text with the structured fields** to work out what's being asked.

### 3.2 Outputs

The service must return a **structured response** describing a visualization.

**Required output components**

1. **Visualization specification**
   - `type`: the visualization type, e.g. `bar_chart`, `time_series`, `network_graph`
   - `title`: a human-readable title
   - `encoding`: a clear mapping from fields to visual channels (x-axis, y-axis, series, nodes/edges)
   - `data`: the data points needed to render the visualization
2. **Response metadata**
   - any extra fields the frontend needs to render properly (units, sorting, time granularity, grouping choices, etc.)
   - optional notes on assumptions, filters applied, or how the query was interpreted

**Must document the response schema** well enough that a frontend engineer can build a renderer without guessing. A simple frontend demo earns a bonus, but **backend + structured outputs** are the focus.

> *The brief's "Example response (illustrative only)" for this section was on the missing page.*

### §5 (heading not visible): Citations / traceability

> These bullets come right before §6 in the source. Their section heading wasn't in the screenshots.

- Each visualized datum (a bar, a time bucket, a node or edge weight) includes **references to the underlying trial records** that contributed to it.
- Each reference includes:
  - `nct_id`
  - an **exact text excerpt** from the API response (or a specific field/value) that supports the datum

Example from the brief (illustrative only):

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

> *"This is intentionally challenging, implement as much as is reasonable in the time box."*

## 4) and 5): not provided

The screenshots don't include these sections. The citation requirement above appears to be the end of §5.

## 6) Submission requirements

Submit a **zip file** containing:

1. **Code**: all source code needed to run the service.
2. **README** that must include:
   - how to run it (install, configure, start)
   - request/response schema documentation (inputs/outputs)
   - key design decisions and tradeoffs
   - limitations and what we'd improve with more time
   - *(from §8)* which AI tools were used, how correctness was validated, and which parts were designed/implemented deliberately vs. generated and adapted
3. **Example runs**: **3–5 example queries** with the **actual JSON outputs** the system produced.
4. **(Optional) Demo**, any of:
   - a small UI
   - a deployed endpoint
   - a short demo video

## 7) Evaluation criteria

| Weight | Area | What they look at |
|---:|---|---|
| **35%** | **System design** | Clear, rational design decisions. Maintainable structure and extensibility. Sensible handling of real-world API data. |
| **20%** | **AI / agent design** | Avoid hallucination-prone steps. Include validation or constraints. Sensible planning and reasoning steps, with appropriate tools. |
| **20%** | **Code quality** | Readability, organization, documentation. Correctness and robustness. |
| **15%** | **Query & visualization coverage** | Breadth of supported query types. Handling multiple question classes **without one-off hacks**. Richer visualizations (e.g. meaningful network graphs) score higher than simple single-chart systems. |
| **10%** | **Input/output design** | Well-structured, unambiguous schemas. Frontend-friendly visualization spec. |
| Bonus | **Traceability** | Deep citations/traceability to source records. |

## 8) Integrity note (AI tools are allowed)

AI tools and online resources are fine. They care about **engineering judgment** and **design reasoning**. The README must briefly describe:

- which tools were used (if any)
- how correctness was validated
- which parts were designed/implemented deliberately vs. generated and adapted

> *"We reward submissions that show evidence of thoughtful construction, testing, and iteration."*

---

## Appendix: Example query types (non-exhaustive)

We don't have to support all of these. They show the range of questions the graders care about, and we aren't limited to them.

| Category | Example queries | Shape of the answer |
|---|---|---|
| **Time trends** | "How has the number of trials for *[drug]* changed per year since 2015?"<br>"How many trials started each year for *[condition]*?" | count per time bucket |
| **Distributions** | "How are *[condition]* trials distributed across phases?"<br>"What are the most common intervention types for *[drug/condition]* trials?" | count per category |
| **Comparisons** | "Compare phases for trials involving *Drug A* vs *Drug B*."<br>"Compare sponsor categories across two conditions." | count per category, per group |
| **Geographic patterns** | "Which countries have the most recruiting trials for *[condition]*?" | count per location (ranked) |
| **Relationships / networks** | "Show a network of sponsors ↔ drugs for *[condition]* trials."<br>"Which drugs frequently co-occur in combination studies (drug ↔ drug network)?" | nodes + weighted edges |

---

## What the grading emphasizes

These are read off the criteria above. They aren't design decisions yet.

1. **Grounding is the central theme.** The data source is called "authoritative", hallucination avoidance gets 20%, citations must quote the API *exactly*, and deep traceability is the bonus. Every number in the output has to come from real API data and be verifiable against it.
2. **Generality over special cases.** "Without one-off hacks" means new question classes should come from composing general pieces, not from a hand-written handler per example query.
3. **Network graphs score extra.** The brief explicitly calls out graphs as "richer" and worth more than single-chart systems.
4. **Schemas are graded twice.** Input/output design is worth 10%, and both schemas must be documented well enough that a frontend engineer never has to guess.
5. **Real-world data is messy.** The API data has missing fields, inconsistent naming, pagination, and rate limits. Handling it sensibly is explicitly part of the 35% system-design score.
6. **Show the work.** Tests, validation, and visible iteration are rewarded, and the README has to explain how correctness was checked.

---

## Deliverables checklist

**Service behavior**
- [ ] Accepts `query` (required, string)
- [ ] Accepts optional structured fields, with defined validation
- [ ] Interprets the question (free text + structured fields together)
- [ ] Retrieves data from the ClinicalTrials.gov Data API
- [ ] Decides whether a visualization is needed, and which type
- [ ] Returns `type`, `title`, `encoding`, `data`
- [ ] Returns render metadata (units, sorting, time granularity, grouping…)
- [ ] Returns notes (assumptions, filters applied, interpretation)
- [ ] Every datum carries citations: `nct_id` + exact excerpt / field value
- [ ] Covers several question classes: trends, distributions, comparisons, geographic, networks

**Submission**
- [ ] Zip file with all source code
- [ ] README: run instructions (install / configure / start)
- [ ] README: request schema (names, types, required/optional, validation)
- [ ] README: response schema (renderer-ready)
- [ ] README: design decisions & tradeoffs
- [ ] README: limitations & future improvements
- [ ] README: AI tools used, how correctness was validated, deliberate vs. generated parts
- [ ] 3–5 example queries with **actual** JSON outputs
- [ ] *(Optional)* small UI / deployed endpoint / demo video

---

## Open questions / gaps

1. **Missing content.** We don't have §4, the start of §5, or the illustrative example response for §3.2. If the full PDF is available, those pages may add hard requirements (non-functional requirements such as latency or error handling, or a stricter citation format).
2. **"Identify if a visualization is needed."** This implies some questions may not need a chart (e.g. "How many Phase 3 trials does X have?" is a single number). The response schema needs a well-defined shape for that case.
3. **"Exact text excerpt … (or a specific field/value)."** A verbatim field value seems to satisfy the requirement. Free-text snippets aren't required, but they are an option.
4. **Ambiguous semantics we'll have to pin down and document:** which date counts as "the trial's year" (start, first-posted, or completion), what "trials for [drug]" means (intervention field vs. any mention), and how multi-phase trials (e.g. Phase 1/2) and multi-country trials get counted.
