"""MI-VAL Ontology — object types.

Palantir-style ontology: every step of the validation framework consumes and
emits *typed objects*, never loose dicts. This is what makes each step
independently swappable, auditable, and agent-drivable.

Design rule
-----------
An object carries (a) identity, (b) provenance, and (c) payload. Identity and
provenance are what make a run reproducible; payload is what makes it useful.
Anything an agent needs to decide the next step must be on the object, not in
the agent's head.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Optional

__all__ = [
    "Provenance",
    "CohortSpec",
    "CohortSubject",
    "ImageOccurrenceRef",
    "ImageMetadata",
    "ImageAsset",
    "RetrievalSpec",
    "RetrievalResult",
    "PreprocessRecipe",
    "PreprocessedSample",
    "ModelCard",
    "EvaluationSpec",
    "EvaluationResult",
    "RunManifest",
    "stable_hash",
]

Modality = Literal["ECG", "CR", "DX", "CT", "MR", "US", "OTHER"]


def stable_hash(payload: Any) -> str:
    """Content hash used for provenance. Order-insensitive for dicts."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class Provenance:
    """Where an object came from. Never optional in a real run."""

    produced_by: str  # module or agent name, e.g. "mival.retrieve.CohortImageResolver"
    produced_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    source_ref: Optional[str] = None  # SQL file, model URI, recipe id...
    inputs_hash: Optional[str] = None
    notes: Optional[str] = None


# --------------------------------------------------------------------------
# 1. Cohort layer — the boundary with ATLAS. MI-VAL never defines cohorts.
# --------------------------------------------------------------------------


@dataclass
class CohortSpec:
    """A cohort MI-VAL consumes. Built in ATLAS, referenced here by id.

    MI-VAL deliberately does not re-implement cohort logic. The contract is:
    `results_schema.cohort` holds (cohort_definition_id, subject_id,
    cohort_start_date, cohort_end_date) — the OHDSI standard shape.
    """

    cohort_definition_id: int
    name: str
    results_schema: str
    cohort_table: str = "cohort"
    cdm_schema: str = "cdm"
    atlas_url: Optional[str] = None
    atlas_definition_json: Optional[dict] = None  # export from ATLAS for archival
    description: Optional[str] = None

    @property
    def object_id(self) -> str:
        return f"cohort:{self.cohort_definition_id}"


@dataclass
class CohortSubject:
    person_id: int
    index_date: date
    cohort_start_date: date
    cohort_end_date: Optional[date] = None
    outcome: Optional[int] = None  # label, when the task is supervised
    covariates: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 2. Imaging layer — MI-CDM image_occurrence / image_feature / measurement
# --------------------------------------------------------------------------


@dataclass
class ImageMetadata:
    """DICOM metadata resolved through image_feature -> measurement.

    `image_feature` is a *linking* table: it points an image_occurrence at
    measurement rows carrying DICOM-vocabulary concepts. Values arrive as
    (concept_id, concept_name, value) triples plus an order index for
    list-valued attributes (e.g. per-lead attributes on an ECG).
    """

    image_occurrence_id: int
    features: dict[str, Any] = field(default_factory=dict)  # concept_name -> value
    concept_ids: dict[str, int] = field(default_factory=dict)  # concept_name -> id
    raw_rows: list[dict] = field(default_factory=list)

    def get(self, concept_name: str, default: Any = None) -> Any:
        return self.features.get(concept_name, default)

    # Convenience accessors for the attributes that gate ECG preprocessing.
    @property
    def sampling_frequency(self) -> Optional[float]:
        v = self.get("Sampling Frequency") or self.get("SamplingFrequency")
        return float(v) if v is not None else None

    @property
    def lead_names(self) -> list[str]:
        v = self.get("Waveform Channel Source") or self.get("LeadNames")
        if v is None:
            return []
        return v if isinstance(v, list) else [v]


@dataclass
class ImageOccurrenceRef:
    """One row of MI-CDM `image_occurrence`, plus the person context."""

    image_occurrence_id: int
    person_id: int
    image_occurrence_date: date
    modality: Modality
    local_path: str
    image_study_UID: Optional[str] = None
    image_series_UID: Optional[str] = None
    anatomic_site_concept_id: Optional[int] = None
    procedure_occurrence_id: Optional[int] = None
    days_from_index: Optional[int] = None
    metadata: Optional[ImageMetadata] = None

    @property
    def object_id(self) -> str:
        return f"image_occurrence:{self.image_occurrence_id}"


@dataclass
class ImageAsset:
    """A physically resolved DICOM object on the analysis machine.

    `local_path` in MI-CDM is institution-relative by design. `root` is the
    site-local mount point; keeping them separate is what lets the same
    RunManifest execute at another institution.
    """

    image_occurrence_id: int
    path: Path
    exists: bool
    n_bytes: Optional[int] = None
    sop_class_uid: Optional[str] = None
    checksum: Optional[str] = None


# --------------------------------------------------------------------------
# 3. Retrieval layer — step (1)
# --------------------------------------------------------------------------


@dataclass
class RetrievalSpec:
    """Declarative "which images do I want" — the core standardized query.

    Everything a reviewer would ask ("how did you pick the ECG?") is an
    explicit field here rather than buried in analyst SQL.
    """

    cohort: CohortSpec
    modality: Modality = "ECG"
    # index window, in days relative to cohort_start_date (index date)
    window_start_days: int = -1
    window_end_days: int = 0
    # when several studies fall in the window
    selection: Literal["first", "last", "nearest_to_index", "all"] = "nearest_to_index"
    max_per_subject: Optional[int] = 1
    require_local_file: bool = True
    # optional metadata-level filters, e.g. {"Number of Leads": 12}
    metadata_filters: dict[str, Any] = field(default_factory=dict)
    anatomic_site_concept_ids: list[int] = field(default_factory=list)
    local_path_root: Optional[str] = None

    @property
    def object_id(self) -> str:
        return f"retrieval:{stable_hash(asdict(self))}"


@dataclass
class RetrievalResult:
    spec_id: str
    occurrences: list[ImageOccurrenceRef]
    assets: list[ImageAsset]
    n_subjects_in_cohort: int
    n_subjects_with_image: int
    attrition: list[dict] = field(default_factory=list)  # CONSORT-style trace
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------
# 4. Preprocessing layer — step (3)
# --------------------------------------------------------------------------


@dataclass
class PreprocessRecipe:
    """An ordered, declarative list of ops. Serializable, hashable, portable.

    Recipes are metadata-aware: an op may declare `requires_metadata` so the
    engine fails loudly when MI-CDM does not carry the attribute the op needs
    (e.g. resampling without a recorded sampling frequency).
    """

    name: str
    modality: Modality
    ops: list[dict] = field(default_factory=list)
    version: str = "0.1.0"
    description: Optional[str] = None

    @property
    def recipe_hash(self) -> str:
        return stable_hash({"name": self.name, "ops": self.ops, "version": self.version})

    @property
    def object_id(self) -> str:
        return f"recipe:{self.name}@{self.recipe_hash}"


@dataclass
class PreprocessedSample:
    image_occurrence_id: int
    person_id: int
    array_path: Optional[Path] = None  # .npy on disk; arrays are not kept in the object
    shape: Optional[tuple] = None
    dtype: Optional[str] = None
    label: Optional[int] = None
    applied_ops: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 5. Model layer — step (4). Superset of RSNA ATLAS model.json.
# --------------------------------------------------------------------------


@dataclass
class ModelCard:
    """Model card. Field names track RSNA/ATLAS `model.json` where they exist,
    with MI-VAL additions for *loadability* and *environment* — the two things
    that actually break when a model moves between institutions.
    """

    id: str
    name: str
    version: str
    task: Literal["binary_classification", "multiclass_classification",
                  "multilabel_classification", "regression", "segmentation",
                  "representation"] = "binary_classification"
    modality: Modality = "ECG"
    # --- loading ---
    source_type: Literal["local", "github", "huggingface", "zenodo", "url"] = "local"
    source_uri: str = ""
    weights_format: Literal["pth", "pt", "safetensors", "h5", "onnx", "ckpt"] = "pth"
    entrypoint: Optional[str] = None  # "pkg.module:build_model"
    checksum_sha256: Optional[str] = None
    # --- expected input contract; validated against MI-CDM metadata ---
    input_spec: dict[str, Any] = field(default_factory=dict)
    output_spec: dict[str, Any] = field(default_factory=dict)
    # --- environment ---
    framework: Optional[str] = None  # "torch==2.3.1"
    python_version: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    # --- inference ---
    ensemble: int = 1
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    # --- documentation (ATLAS-aligned) ---
    intended_use: Optional[str] = None
    training_data: Optional[str] = None
    known_limitations: list[str] = field(default_factory=list)
    license: Optional[str] = None
    citation: Optional[str] = None

    @property
    def object_id(self) -> str:
        return f"model:{self.id}@{self.version}"


# --------------------------------------------------------------------------
# 6. Evaluation layer — step (5)
# --------------------------------------------------------------------------


@dataclass
class EvaluationSpec:
    """Which metrics, at which operating point, sliced how.

    Metric choice follows the Metrics Reloaded problem-fingerprint logic:
    the task type plus the class prevalence determine which metrics are
    defensible, so the spec records *why* as well as *what*.
    """

    task: str
    primary_metrics: list[str] = field(default_factory=lambda: ["auroc", "auprc"])
    secondary_metrics: list[str] = field(default_factory=list)
    operating_point: Literal["youden", "fixed_threshold", "fixed_sensitivity",
                             "fixed_specificity"] = "youden"
    threshold: Optional[float] = None
    bootstrap_n: int = 1000
    ci_level: float = 0.95
    # subgroups: MI-CDM metadata columns and/or demographic columns
    subgroup_by: list[str] = field(default_factory=list)
    calibration: bool = True
    seed: int = 42
    rationale: Optional[str] = None

    @property
    def object_id(self) -> str:
        return f"eval:{stable_hash(asdict(self))}"


@dataclass
class EvaluationResult:
    spec_id: str
    model_id: str
    n: int
    n_positive: Optional[int] = None
    overall: dict[str, Any] = field(default_factory=dict)
    subgroups: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    predictions_path: Optional[str] = None
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------
# 7. Run layer — the reproducibility contract
# --------------------------------------------------------------------------


@dataclass
class RunManifest:
    """The single artifact that makes a run re-executable elsewhere.

    A manifest that another site can run unchanged is the whole point of the
    framework: cohort by id, recipe by hash, model by checksum, metrics by
    spec. If any of those four is missing, the run is not portable.
    """

    run_id: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    site: Optional[str] = None
    cohort: Optional[dict] = None
    retrieval: Optional[dict] = None
    recipe: Optional[dict] = None
    model: Optional[dict] = None
    evaluation: Optional[dict] = None
    environment: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    mival_version: str = "0.1.0"

    def to_json(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")
        return path
