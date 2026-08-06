"""Object registry + link definitions.

The registry is what an agent queries when it needs to know "what objects exist
and how do they connect" without reading the whole codebase. Keeping links
declarative here (rather than implicit in function signatures) is what lets a
step be swapped without breaking the ones around it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .objects import (
    CohortSpec,
    CohortSubject,
    EvaluationResult,
    EvaluationSpec,
    ImageAsset,
    ImageMetadata,
    ImageOccurrenceRef,
    ModelCard,
    PreprocessedSample,
    PreprocessRecipe,
    RetrievalResult,
    RetrievalSpec,
    RunManifest,
)

OBJECT_TYPES: dict[str, type] = {
    "CohortSpec": CohortSpec,
    "CohortSubject": CohortSubject,
    "ImageOccurrenceRef": ImageOccurrenceRef,
    "ImageMetadata": ImageMetadata,
    "ImageAsset": ImageAsset,
    "RetrievalSpec": RetrievalSpec,
    "RetrievalResult": RetrievalResult,
    "PreprocessRecipe": PreprocessRecipe,
    "PreprocessedSample": PreprocessedSample,
    "ModelCard": ModelCard,
    "EvaluationSpec": EvaluationSpec,
    "EvaluationResult": EvaluationResult,
    "RunManifest": RunManifest,
}


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    cardinality: str
    via: str
    note: str = ""


#: Declarative ontology graph. Mirrors the MI-CDM physical model so that a
#: reader can go from an object to the table it came from without guessing.
LINKS: tuple[Link, ...] = (
    Link("CohortSpec", "CohortSubject", "1:N", "results.cohort.subject_id = person.person_id"),
    Link("CohortSubject", "ImageOccurrenceRef", "1:N",
         "image_occurrence.person_id, windowed on cohort_start_date"),
    Link("ImageOccurrenceRef", "ImageMetadata", "1:1",
         "image_feature.image_occurrence_id -> measurement.measurement_id",
         "image_feature is a LINK table, not a metadata store"),
    Link("ImageOccurrenceRef", "ImageAsset", "1:1", "image_occurrence.local_path + site root"),
    Link("ImageAsset", "PreprocessedSample", "1:1", "PreprocessRecipe application"),
    Link("PreprocessedSample", "EvaluationResult", "N:1", "ModelCard inference"),
    Link("RunManifest", "*", "1:N", "artifact registry", "the portability contract"),
)


#: Which MI-CDM / OMOP tables each object type is grounded in. Agents use this
#: to sanity-check a query before running it.
TABLE_GROUNDING: dict[str, list[str]] = {
    "CohortSpec": ["<results>.cohort", "<results>.cohort_definition"],
    "CohortSubject": ["person", "observation_period", "condition_occurrence", "measurement"],
    "ImageOccurrenceRef": ["image_occurrence"],
    "ImageMetadata": ["image_feature", "measurement", "concept"],
    "ImageAsset": ["image_occurrence.local_path"],
}


def describe() -> str:
    """Human/agent readable ontology summary."""
    lines = ["MI-VAL ontology", "=" * 15, "", "Objects:"]
    for name in OBJECT_TYPES:
        tables = TABLE_GROUNDING.get(name)
        suffix = f"  <- {', '.join(tables)}" if tables else ""
        lines.append(f"  - {name}{suffix}")
    lines += ["", "Links:"]
    for link in LINKS:
        note = f"  # {link.note}" if link.note else ""
        lines.append(f"  {link.source} --{link.cardinality}--> {link.target}  via {link.via}{note}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(describe())
