"""MI-VAL step (3) — declarative preprocessing recipes.

A recipe is data, not code: an ordered list of ops with parameters, hashable
and serializable. Two reasons this matters more than convenience.

1. Reproducibility. "We bandpass-filtered and z-scored" is not a method
   section. ``recipe.yaml`` + its hash is.
2. Metadata-awareness. Ops declare which MI-CDM attributes they need. If the
   recipe says "resample to 500 Hz" and MI-CDM does not record the source
   sampling frequency for a record, the engine refuses that record and says so,
   rather than assuming a default and silently producing garbage.

Ops are registered functions, so a site can add its own without forking:

    @register_op("my_filter", requires_metadata=["Sampling Frequency"])
    def my_filter(x, meta, **params): ...
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..ontology.objects import (
    ImageMetadata,
    ImageOccurrenceRef,
    PreprocessedSample,
    PreprocessRecipe,
)


@dataclass
class OpDef:
    name: str
    fn: Callable
    requires_metadata: list[str] = field(default_factory=list)
    modality: str = "*"
    doc: str = ""


OP_REGISTRY: dict[str, OpDef] = {}


def register_op(name: str, requires_metadata: Optional[list[str]] = None,
                modality: str = "*") -> Callable:
    def deco(fn: Callable) -> Callable:
        OP_REGISTRY[name] = OpDef(name=name, fn=fn,
                                  requires_metadata=requires_metadata or [],
                                  modality=modality, doc=(fn.__doc__ or "").strip())
        return fn
    return deco


class RecipeError(RuntimeError):
    pass


def load_recipe(path: Path | str) -> PreprocessRecipe:
    """Load from YAML (if PyYAML present) or JSON."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RecipeError("PyYAML required for .yaml recipes") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return PreprocessRecipe(**data)


def validate_recipe(recipe: PreprocessRecipe,
                    available_metadata: Optional[set[str]] = None) -> list[str]:
    """Static check before any data is touched.

    Returns a list of problems. Empty list means the recipe is executable
    against the metadata this dataset actually carries.
    """
    problems: list[str] = []
    for i, op in enumerate(recipe.ops):
        name = op.get("op")
        if name not in OP_REGISTRY:
            problems.append(f"[{i}] unknown op '{name}'. known: {sorted(OP_REGISTRY)}")
            continue
        d = OP_REGISTRY[name]
        if d.modality not in ("*", recipe.modality):
            problems.append(f"[{i}] op '{name}' is for {d.modality}, recipe is {recipe.modality}")
        if available_metadata is not None:
            missing = [k for k in d.requires_metadata if k not in available_metadata]
            if missing:
                problems.append(
                    f"[{i}] op '{name}' needs MI-CDM attribute(s) {missing} "
                    "which this dataset does not carry — supply an explicit override "
                    "in the op params or drop the op")
    return problems


class PreprocessEngine:
    """Apply a recipe to loaded arrays.

    The engine never loads DICOM itself — a loader callable is injected. That
    keeps this module testable without pydicom and lets a site swap in its own
    reader (wfdb, pydicom, a PACS client) without touching recipe logic.
    """

    def __init__(self, recipe: PreprocessRecipe, loader: Callable[[Path], Any],
                 workspace: Path | str = "_workspace", strict: bool = True):
        self.recipe = recipe
        self.loader = loader
        self.workspace = Path(workspace)
        self.array_dir = self.workspace / "03_arrays"
        self.array_dir.mkdir(parents=True, exist_ok=True)
        self.strict = strict

    def apply_one(self, occ: ImageOccurrenceRef, path: Path,
                  label: Optional[int] = None) -> PreprocessedSample:
        meta: ImageMetadata = occ.metadata or ImageMetadata(occ.image_occurrence_id)
        warnings: list[str] = []
        applied: list[str] = []
        x = self.loader(path)

        for op in self.recipe.ops:
            name = op.get("op")
            params = {k: v for k, v in op.items() if k != "op"}
            d = OP_REGISTRY.get(name)
            if d is None:
                raise RecipeError(f"unknown op '{name}'")
            missing = [k for k in d.requires_metadata
                       if meta.get(k) is None and k not in params]
            if missing:
                msg = (f"op '{name}' requires {missing}; not present on "
                       f"image_occurrence {occ.image_occurrence_id}")
                if self.strict:
                    raise RecipeError(msg)
                warnings.append(msg)
                continue
            x = d.fn(x, meta, **params)
            applied.append(name)

        out_path = self.array_dir / f"{occ.image_occurrence_id}.npy"
        shape, dtype = self._persist(x, out_path)
        return PreprocessedSample(
            image_occurrence_id=occ.image_occurrence_id,
            person_id=occ.person_id,
            array_path=out_path, shape=shape, dtype=dtype,
            label=label, applied_ops=applied, warnings=warnings,
        )

    @staticmethod
    def _persist(x: Any, path: Path) -> tuple[Optional[tuple], Optional[str]]:
        try:
            import numpy as np  # type: ignore
        except ImportError:  # pragma: no cover
            path.with_suffix(".json").write_text(json.dumps(x, default=str), encoding="utf-8")
            return None, None
        arr = np.asarray(x)
        np.save(path, arr)
        return tuple(arr.shape), str(arr.dtype)

    def dump_spec(self) -> Path:
        p = self.workspace / "03_recipe.json"
        p.write_text(json.dumps({**asdict(self.recipe),
                                 "recipe_hash": self.recipe.recipe_hash},
                                indent=2, default=str), encoding="utf-8")
        return p
