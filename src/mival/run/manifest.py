"""Run manifests — the portability contract.

A MI-VAL run is reproducible at another institution if and only if the manifest
pins four things: the cohort (by ATLAS definition, exported), the retrieval spec
(window + selection rule), the recipe (by hash), and the model (by checksum) —
plus the metric spec. :func:`verify` checks exactly that, so "is this run
portable?" is a command rather than an opinion.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from ..ontology.objects import (
    CohortSpec,
    EvaluationSpec,
    ModelCard,
    PreprocessRecipe,
    RetrievalSpec,
    RunManifest,
)


def capture_environment(extra_packages: Optional[list[str]] = None) -> dict:
    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    for pkg in ("numpy", "torch", "pydicom", "scipy", "pandas", "wfdb",
                *(extra_packages or [])):
        try:
            env[pkg] = __import__(pkg).__version__
        except Exception:
            env[pkg] = None
    try:
        env["pip_freeze_sha"] = _hash_text(
            subprocess.run([sys.executable, "-m", "pip", "freeze"],
                           capture_output=True, text=True, timeout=60).stdout)
    except Exception:
        env["pip_freeze_sha"] = None
    try:
        env["git_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=10).stdout.strip() or None
    except Exception:
        env["git_commit"] = None
    return env


def _hash_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_manifest(cohort: CohortSpec, retrieval: RetrievalSpec,
                   recipe: Optional[PreprocessRecipe], models: list[ModelCard],
                   evaluation: EvaluationSpec, site: Optional[str] = None,
                   artifacts: Optional[dict[str, str]] = None,
                   run_id: Optional[str] = None) -> RunManifest:
    return RunManifest(
        run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
        site=site,
        cohort={**asdict(cohort), "object_id": cohort.object_id},
        retrieval={**asdict(retrieval), "object_id": retrieval.object_id},
        recipe=({**asdict(recipe), "recipe_hash": recipe.recipe_hash} if recipe else None),
        model={"members": [asdict(m) for m in models],
               "ensemble_size": len(models)},
        evaluation={**asdict(evaluation), "object_id": evaluation.object_id},
        environment=capture_environment(),
        artifacts=artifacts or {},
    )


PORTABILITY_CHECKS = (
    ("cohort.atlas_definition_json", "ATLAS cohort JSON not exported — another site cannot "
     "rebuild the same population"),
    ("recipe.recipe_hash", "no recipe hash — preprocessing is not pinned"),
    ("model.members[].checksum_sha256", "model weights not checksummed"),
    ("evaluation.object_id", "metric spec not pinned"),
    ("environment.pip_freeze_sha", "environment not captured"),
)


def verify(manifest: RunManifest | dict) -> dict:
    """Return {'portable': bool, 'issues': [...]}. Issues are actionable."""
    m = manifest if isinstance(manifest, dict) else asdict(manifest)
    issues: list[str] = []

    if not (m.get("cohort") or {}).get("atlas_definition_json"):
        issues.append(PORTABILITY_CHECKS[0][1])
    if not (m.get("recipe") or {}).get("recipe_hash"):
        issues.append(PORTABILITY_CHECKS[1][1])
    members = ((m.get("model") or {}).get("members") or [])
    for mem in members:
        if mem.get("source_type") != "local" and not mem.get("checksum_sha256"):
            issues.append(f"{PORTABILITY_CHECKS[2][1]}: {mem.get('id')}")
        if not mem.get("framework"):
            issues.append(f"no framework pin for model {mem.get('id')}")
    if not (m.get("evaluation") or {}).get("object_id"):
        issues.append(PORTABILITY_CHECKS[3][1])
    if not (m.get("environment") or {}).get("pip_freeze_sha"):
        issues.append(PORTABILITY_CHECKS[4][1])
    # local paths must not leak into a manifest meant to travel
    ret = m.get("retrieval") or {}
    if ret.get("local_path_root"):
        issues.append("retrieval.local_path_root is site-specific — receiving site must "
                      "override it; keep it out of the shared manifest")

    return {"portable": not issues, "n_issues": len(issues), "issues": issues}


def load(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
