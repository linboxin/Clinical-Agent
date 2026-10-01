"""Name normalization for drug and condition values (brand/code → generic, spelling variants
merged, combinations split, non-entities dropped), done by a small model under guardrails.

Why a model: registry names are free text ("Keytruda", "MK-3475", "pembrolizumab 200 mg",
"Nivolumab & Ipilimumab"); deterministic rules (registry.drug_key) handle dose and salt
suffixes but cannot know that a brand and a code are the same drug.

Guardrails (the model never touches counts or citations):
- it only maps names it was given, in batches; every input line must be answered exactly once
  or the batch is retried once and otherwise left unmapped (raw names kept);
- answers are cached on disk per name, so the same name maps the same way on every run;
- the operators still cite the raw registry value, so every merge is auditable;
- the response discloses the model and the merges it made (meta.normalization), and
  NAME_NORMALIZER=off turns it off.
"""

import asyncio
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import openai
from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import Dimension
from app.registry import norm_name
from app.telemetry import span

log = logging.getLogger(__name__)

BATCH_SIZE = 60
MAX_NAMES = 600  # most frequent names per dimension; rarer ones keep their raw grouping
NORMALIZED_DIMENSIONS = (Dimension.DRUG, Dimension.CONDITION)

INSTRUCTIONS = {
    Dimension.DRUG: (
        "Each numbered line is an intervention name from a clinical-trial registry. Return the "
        "canonical generic name of each active drug or biologic it contains, lowercase (e.g. "
        "'Keytruda' and 'MK-3475' -> ['pembrolizumab']; 'Temodar (temozolomide)' -> "
        "['temozolomide']). Split combinations into each drug ('Nivolumab & Ipilimumab' -> "
        "['nivolumab', 'ipilimumab']). Keep cell and gene therapies (e.g. 'Anti-BCMA CAR-T "
        "cells' -> ['anti-bcma car-t cells']), vaccines and investigational codes you do not "
        "recognize (return them unchanged in lowercase). Return [] only for placebo, standard "
        "of care, procedures, radiation, devices, imaging agents or tracers, and generic class "
        "words with no specific agent ('chemotherapy', 'antibody-drug conjugate'). Answer every "
        "line exactly once."
    ),
    Dimension.CONDITION: (
        "Each numbered line is a condition name from a clinical-trial registry. Return the "
        "canonical disease or condition name, lowercase, merging spelling variants and "
        "abbreviations (e.g. 'GBM' and 'Glioblastoma Multiforme' -> ['glioblastoma']; 'NSCLC' "
        "-> ['non-small cell lung cancer']). Keep clinically distinct conditions distinct (do "
        "not merge a subtype into its parent). Return [] for entries that are not a condition "
        "(e.g. 'Healthy'). If unsure, return the name unchanged in lowercase. Answer every line "
        "exactly once."
    ),
}


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(description="Line number of the input name.")
    canonical: list[str] = Field(description="Canonical names; [] if not an entity of this kind.")


class Labels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[Label]


class NameNormalizer(Protocol):
    model: str

    async def __call__(self, dimension: Dimension, names: list[str]) -> dict[str, list[str]]:
        """Map each given name to canonical names (missing key = leave unmapped)."""
        ...


@dataclass
class Merge:
    canonical: str
    variants: list[str]


@dataclass
class NormalizationReport:
    dimension: Dimension
    model: str
    names_in: int
    names_sent: int
    names_unmapped: int
    names_mapped: int
    dropped: list[str] = field(default_factory=list)
    merges: list[Merge] = field(default_factory=list)


def check_labels(labels: Labels, size: int) -> tuple[list[Label], str | None]:
    """Split the model's answer into usable labels and a problem description (None = clean).
    Out-of-range, duplicate or malformed answers are never used; missing lines stay raw."""
    seen: set[int] = set()
    usable: list[Label] = []
    problems: list[str] = []
    for label in labels.labels:
        if not 0 <= label.index < size or label.index in seen:
            problems.append(f"line {label.index} is out of range or answered twice")
            continue
        seen.add(label.index)
        if len(label.canonical) > 6 or any(not c.strip() or len(c) > 80 for c in label.canonical):
            problems.append(
                f"line {label.index}: canonical names must be 1-80 characters, at most 6"
            )
            continue
        usable.append(label)
    missing = size - len(seen)
    if missing:
        problems.append(f"{missing} line(s) not answered; answer every line 0-{size - 1} once")
    return usable, ("; ".join(problems) or None)


class OpenAINameNormalizer:
    def __init__(
        self,
        client: openai.AsyncOpenAI,
        model: str,
        cache_path: Path | None,
        concurrency: int = 8,
    ) -> None:
        self.client = client
        self.model = model
        self.cache_path = cache_path
        self.limit = asyncio.Semaphore(concurrency)
        self._cache: dict[str, list[str]] = {}
        if cache_path is not None and cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except (OSError, ValueError):
                self._cache = {}

    def _key(self, dimension: Dimension, name: str) -> str:
        return f"{self.model}|{dimension.value}|{name}"

    async def __call__(self, dimension: Dimension, names: list[str]) -> dict[str, list[str]]:
        todo = [n for n in names if self._key(dimension, n) not in self._cache]
        batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
        with span("normalize.names", dimension=dimension.value, names=len(names), new=len(todo)):
            results = await asyncio.gather(*(self._batch(dimension, b) for b in batches))
        for batch, labels in zip(batches, results, strict=True):
            for label in labels.labels if labels else []:
                canonical = [norm_name(c) for c in label.canonical if c.strip()]
                self._cache[self._key(dimension, batch[label.index])] = canonical
        if todo and self.cache_path is not None:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache))
            tmp.replace(self.cache_path)
        return {
            n: self._cache[self._key(dimension, n)]
            for n in names
            if self._key(dimension, n) in self._cache
        }

    async def _batch(self, dimension: Dimension, names: list[str]) -> Labels | None:
        prompt = "\n".join(f"{i}: {name}" for i, name in enumerate(names))
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        usable: list[Label] = []
        async with self.limit:
            for _attempt in range(2):
                try:
                    response = await self.client.responses.parse(
                        model=self.model,
                        instructions=INSTRUCTIONS[dimension],
                        input=messages,  # type: ignore[arg-type]
                        text_format=Labels,
                    )
                except openai.APIError as exc:
                    log.warning("name normalization failed: %s", exc)
                    break
                labels = response.output_parsed
                if labels is None:
                    break
                usable, problem = check_labels(labels, len(names))
                if problem is None:
                    return labels
                messages += [
                    {"role": "assistant", "content": labels.model_dump_json()},
                    {"role": "user", "content": f"Invalid answer: {problem}. Answer again."},
                ]
        if usable:
            log.warning("name normalization: %d/%d names usable", len(usable), len(names))
            return Labels(labels=usable)  # only validated answers; the rest stay raw
        log.warning("name normalization batch rejected; raw names kept")
        return None


def report(
    dimension: Dimension,
    model: str,
    labels: Counter[str],
    mapping: dict[str, list[str]],
    raw_labels: dict[str, str],
) -> NormalizationReport:
    """`raw_labels` holds the names sent to the model; `mapping` the ones it answered."""
    """Summarize what the model changed: merges (several raw names → one canonical) and drops."""
    variants: dict[str, set[str]] = defaultdict(set)
    dropped = []
    for key, canonical in mapping.items():
        if not canonical:
            dropped.append(raw_labels.get(key, key))
        for name in canonical:
            variants[name].add(raw_labels.get(key, key))
    merges = [
        Merge(name, sorted(v))
        for name, v in sorted(variants.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if len(v) > 1
    ]
    changed = sum(1 for key, canonical in mapping.items() if canonical != [key])
    return NormalizationReport(
        dimension=dimension,
        model=model,
        names_in=len(labels),
        names_sent=len(raw_labels),
        names_unmapped=len(raw_labels) - len(mapping),
        names_mapped=changed,
        dropped=sorted(dropped)[:30],
        merges=merges[:25],
    )
