# Planner eval results

Each run is listed as `<timestamp>_<model>_<prompt version>[_norepair]`: a `.jsonl` file (one row per case run, with the produced plan) and a `.md` summary. The cases are in [`../cases.yaml`](../cases.yaml): 34 cases, each run 3 times (102 runs). Scoring is deterministic.

| Prompt | Model | Pass | First try | Repair used | Stable | Median latency | Notes |
|---|---|---|---|---|---|---|---|
| v3 | gpt-5.4-nano | 90/102 | 88 | 3 | — | 2.3 s | unnecessary clarifications; misses `completion_date` |
| v3 | gpt-5.4-mini | 101/102 | 98 | 3 | — | 2.2 s | once accepted the structured field over a contradicting question |
| v3 | gpt-5.4 | 102/102 | 99 | 3 | — | 2.8 s | |
| v4 | gpt-5.4-nano | 99/102 | 97 | 2 | 29/34 | 2.4 s | prompt v4: keep the question's value on conflicts; concrete clarification options |
| v4 | gpt-5.4-mini | 101/102 | 98 | 3 | 30/34 | 1.9 s | once used start_date for "completed each year" |
| v4 | gpt-5.4 | 102/102 | 100 | 2 | 33/34 | 2.7 s | |
| v5 | gpt-5.4-nano | 97/102 | 96 | 1 | 30/34 | 2.2 s | prompt v5: wording cues for the date basis |
| v5 | gpt-5.4-mini | 101/102 | 98 | 3 | 32/34 | 2.0 s | once accepted "recruiting *in March 2019*" (historical status) |
| **v5** | **gpt-5.4** | **102/102** | **99** | **3** | **34/34** | **2.5 s** | **chosen default** |
| v5 | gpt-5.4-mini, `--no-repair` (E2) | 99/102 | 99 | 0 | 34/34 | 2.1 s | the misspelling case fails 3/3 without the repair call |

**How to read the columns:**
- **Stable** = the same status and the same semantic plan (free text such as labels and clarification wording ignored) in all 3 repeats. The v3 runs used an earlier, stricter definition, so they aren't comparable.
- **Repair used** counts runs where grounding or validation feedback triggered the second planner call. With repair on, the misspelling case `pembrolizumabb` is fixed every time.
- **Tokens:** about 3.8k per run for all models (the prompt dominates).

**Decision.** The default is `gpt-5.4`: the only model with 100% pass and 100% stability, at about 0.5 s more median latency than mini. That latency is negligible next to retrieval. Mini's single miss would have produced a misleading chart (current status presented as historical).
