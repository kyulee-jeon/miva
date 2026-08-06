"""End-to-end orchestration: config -> five steps -> manifest + reports.

The pipeline is deliberately thin. Each step is an object-in/object-out module
that can be run alone (an agent frequently does exactly that), and this file
only wires them and records what happened. Anything clever belongs in a step,
not here.

Dry-run mode executes every step that does not need a database or a GPU: SQL is
rendered, the recipe is statically validated, model cards are checked for
portability, and the metric spec is resolved. That is enough to review a study
protocol before it touches PHI.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from ..evaluate.evaluator import Evaluator
from ..evaluate import report as eval_report
from ..models.card import check_input_compatibility, load_card, validate_card
from ..models.loader import ModelRegistry
from ..ontology.objects import (
    CohortSpec,
    EvaluationSpec,
    ModelCard,
    PreprocessRecipe,
    RetrievalSpec,
)
from ..preprocess import ops_ecg, ops_image  # noqa: F401  (registers built-in ops)
from ..preprocess.loaders import get_loader
from ..preprocess.recipe import PreprocessEngine, load_recipe, validate_recipe
from ..profile.profiler import DataSpecProfiler
from ..retrieve.resolver import CohortImageResolver
from . import manifest as manifest_mod


def load_config(path: Path | str) -> dict:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml  # type: ignore
        return yaml.safe_load(text)
    return json.loads(text)


def build_objects(cfg: dict) -> dict[str, Any]:
    cohort = CohortSpec(**cfg["cohort"])
    retrieval = RetrievalSpec(cohort=cohort, **cfg["retrieval"])
    recipe = None
    if cfg.get("preprocess"):
        p = cfg["preprocess"]
        if "recipe_file" in p:
            recipe = load_recipe(p["recipe_file"])
        else:
            # engine-level keys (loader, strict) live alongside recipe keys in the
            # config for readability, but must not leak into the hashed recipe —
            # otherwise changing the DICOM reader would change the recipe hash.
            fields = PreprocessRecipe.__dataclass_fields__
            recipe = PreprocessRecipe(**{k: v for k, v in p.items() if k in fields})
    models = [load_card(m) if isinstance(m, str) else ModelCard(**m)
              for m in cfg.get("models", [])]
    evaluation = EvaluationSpec(**cfg["evaluation"])
    return {"cohort": cohort, "retrieval": retrieval, "recipe": recipe,
            "models": models, "evaluation": evaluation}


class ValidationRun:
    def __init__(self, cfg: dict, connection: Any = None,
                 workspace: Path | str = "_workspace", site: Optional[str] = None):
        self.cfg = cfg
        self.conn = connection
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.site = site or cfg.get("site")
        self.obj = build_objects(cfg)
        self.artifacts: dict[str, str] = {}

    # -- individual steps --------------------------------------------------

    def step1_retrieve(self):
        resolver = CohortImageResolver(self.obj["retrieval"], self.conn, self.workspace)
        result = resolver.resolve()
        self.artifacts["01_retrieval"] = str(self.workspace / "01_retriever_result.json")
        return result

    def step2_profile(self, retrieval, characteristics=None):
        profiler = DataSpecProfiler(retrieval, characteristics, self.workspace)
        report = profiler.profile()
        html_path = profiler.to_html(report)
        self.artifacts["02_data_spec_html"] = str(html_path)
        return report

    def step3_preprocess(self, retrieval, labels: Optional[dict[int, int]] = None):
        recipe = self.obj["recipe"]
        if recipe is None:
            return []
        available = set()
        for o in retrieval.occurrences:
            if o.metadata:
                available.update(o.metadata.features)
        problems = validate_recipe(recipe, available or None)
        if problems:
            raise ValueError("recipe validation failed:\n  - " + "\n  - ".join(problems))
        loader = get_loader(self.cfg.get("preprocess", {}).get("loader", "dicom_waveform"))
        engine = PreprocessEngine(recipe, loader, self.workspace,
                                  strict=self.cfg.get("preprocess", {}).get("strict", True))
        engine.dump_spec()
        assets = {a.image_occurrence_id: a for a in retrieval.assets}
        samples, failures = [], []
        for occ in retrieval.occurrences:
            asset = assets.get(occ.image_occurrence_id)
            if asset is None or not asset.exists:
                failures.append({"image_occurrence_id": occ.image_occurrence_id,
                                 "reason": "asset missing"})
                continue
            try:
                samples.append(engine.apply_one(
                    occ, asset.path, (labels or {}).get(occ.image_occurrence_id)))
            except Exception as exc:
                failures.append({"image_occurrence_id": occ.image_occurrence_id,
                                 "reason": str(exc)})
        (self.workspace / "03_preprocess_failures.json").write_text(
            json.dumps(failures, indent=2), encoding="utf-8")
        self.artifacts["03_preprocess_failures"] = str(
            self.workspace / "03_preprocess_failures.json")
        return samples

    def step4_models(self, retrieval):
        checks = {}
        for card in self.obj["models"]:
            checks[card.object_id] = {
                "card_problems": validate_card(card),
                "input_compatibility": check_input_compatibility(card, retrieval),
            }
        (self.workspace / "04_model_checks.json").write_text(
            json.dumps(checks, indent=2, default=str), encoding="utf-8")
        self.artifacts["04_model_checks"] = str(self.workspace / "04_model_checks.json")
        return checks

    def step5_evaluate(self, predictions: dict[str, dict]):
        """``predictions`` maps model_id -> {y_true, y_score, groups?, ids?}."""
        evaluator = Evaluator(self.obj["evaluation"], self.workspace)
        results = [evaluator.evaluate(p["y_true"], p["y_score"], model_id,
                                      p.get("groups"), p.get("ids"))
                   for model_id, p in predictions.items()]
        path = eval_report.write_report(results, self.workspace / "05_results.html",
                                        manifest=None)
        self.artifacts["05_results_html"] = str(path)
        return results

    # -- whole run ---------------------------------------------------------

    def dry_run(self) -> dict:
        """Everything checkable without a DB, DICOM files, or a GPU."""
        out: dict[str, Any] = {"mode": "dry_run"}
        retrieval = self.step1_retrieve()
        out["retrieval_sql"] = str(self.workspace / "01_retrieve_occurrence.sql")

        recipe = self.obj["recipe"]
        out["recipe"] = ({"name": recipe.name, "hash": recipe.recipe_hash,
                          "problems": validate_recipe(recipe)} if recipe else None)
        out["models"] = {c.object_id: {"card_problems": validate_card(c)}
                         for c in self.obj["models"]}
        out["evaluation"] = {"spec_id": self.obj["evaluation"].object_id,
                             "recommendation": __import__(
                                 "mival.evaluate.metrics", fromlist=["recommend"]
                             ).recommend(self.obj["evaluation"].task)}
        man = manifest_mod.build_manifest(
            self.obj["cohort"], self.obj["retrieval"], recipe, self.obj["models"],
            self.obj["evaluation"], site=self.site, artifacts=self.artifacts)
        man_path = man.to_json(self.workspace / "run_manifest.json")
        out["manifest"] = str(man_path)
        out["portability"] = manifest_mod.verify(man)
        (self.workspace / "00_dry_run.json").write_text(
            json.dumps(out, indent=2, default=str), encoding="utf-8")
        return out

    def finalize_manifest(self) -> Path:
        man = manifest_mod.build_manifest(
            self.obj["cohort"], self.obj["retrieval"], self.obj["recipe"],
            self.obj["models"], self.obj["evaluation"], site=self.site,
            artifacts=self.artifacts)
        return man.to_json(self.workspace / "run_manifest.json")
