"""Smoke tests — run without a database, DICOM files, numpy, or a GPU.

The point of these is not coverage; it is that a new site can clone the repo,
run pytest, and know the framework's contracts hold before wiring up any PHI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mival.evaluate import metrics as M
from mival.evaluate.evaluator import Evaluator
from mival.ontology.objects import (
    CohortSpec,
    EvaluationSpec,
    ImageMetadata,
    ImageOccurrenceRef,
    ModelCard,
    PreprocessRecipe,
    RetrievalResult,
    RetrievalSpec,
)
from mival.ontology.registry import LINKS, OBJECT_TYPES, describe
from mival.models.card import check_input_compatibility, from_atlas, validate_card
from mival.preprocess.recipe import validate_recipe
from mival.profile.profiler import DataSpecProfiler
from mival.retrieve.resolver import CohortImageResolver
from mival.run import manifest as manifest_mod

REPO = Path(__file__).resolve().parents[1]


def _cohort() -> CohortSpec:
    return CohortSpec(cohort_definition_id=1042, name="AMI", results_schema="results")


def _occ(i: int, hz: float = 500.0, leads=None) -> ImageOccurrenceRef:
    from datetime import date
    meta = ImageMetadata(i, features={
        "Sampling Frequency": hz,
        "Waveform Channel Source": leads or ["I", "II", "III", "aVR", "aVL", "aVF",
                                             "V1", "V2", "V3", "V4", "V5", "V6"],
        "Manufacturer": "GE" if i % 2 else "Philips",
    })
    return ImageOccurrenceRef(image_occurrence_id=i, person_id=1000 + i,
                              image_occurrence_date=date(2020, 1, 1), modality="ECG",
                              local_path=f"ecg/{i}.dcm", days_from_index=0, metadata=meta)


# ---------------------------------------------------------------- ontology

def test_ontology_is_self_describing():
    assert "CohortSpec" in OBJECT_TYPES and "RunManifest" in OBJECT_TYPES
    text = describe()
    assert "image_feature" in text  # links are grounded in real tables
    assert len(LINKS) >= 6


# ---------------------------------------------------------------- step (1)

def test_retrieval_sql_renders_and_is_parameterized(tmp_path):
    spec = RetrievalSpec(cohort=_cohort(), selection="nearest_to_index")
    r = CohortImageResolver(spec, None, tmp_path)
    sql = r.render_occurrence_sql()
    assert "results.cohort" in sql and "cdm.image_occurrence" in sql
    assert ":cohort_definition_id" in sql and "ABS(image_occurrence_date - index_date)" in sql
    assert "{" not in sql  # every placeholder substituted
    assert "{" not in r.render_metadata_sql()


def test_selection_rule_changes_sql():
    spec_a = RetrievalSpec(cohort=_cohort(), selection="first")
    spec_b = RetrievalSpec(cohort=_cohort(), selection="last")
    a = CohortImageResolver(spec_a, None).render_occurrence_sql()
    b = CohortImageResolver(spec_b, None).render_occurrence_sql()
    assert a != b and spec_a.object_id != spec_b.object_id


def test_dry_run_writes_reviewable_sql(tmp_path):
    r = CohortImageResolver(RetrievalSpec(cohort=_cohort()), None, tmp_path)
    result = r.resolve()
    assert (tmp_path / "01_retrieve_occurrence.sql").exists()
    assert result.attrition[0]["step"] == "dry_run"


def test_metadata_folding_preserves_list_order():
    rows = [
        {"image_occurrence_id": 1, "feature_name": "Lead", "image_feature_value_order": 2,
         "value_as_number": None, "value_as_concept_name": "II", "value_source_value": "II",
         "measurement_concept_id": 9},
        {"image_occurrence_id": 1, "feature_name": "Lead", "image_feature_value_order": 1,
         "value_as_number": None, "value_as_concept_name": "I", "value_source_value": "I",
         "measurement_concept_id": 9},
    ]
    folded = CohortImageResolver._fold_metadata(rows)
    assert folded[1].features["Lead"] == ["I", "II"]


def test_local_path_root_is_site_local(tmp_path):
    spec = RetrievalSpec(cohort=_cohort(), local_path_root=str(tmp_path))
    asset = CohortImageResolver(spec, None, tmp_path).resolve_asset(_occ(1))
    assert asset.path == tmp_path / "ecg/1.dcm"


# ---------------------------------------------------------------- step (2)

def test_profiler_flags_acquisition_heterogeneity(tmp_path):
    occs = [_occ(i, hz=500.0 if i < 6 else 250.0) for i in range(1, 11)]
    rr = RetrievalResult(spec_id="s", occurrences=occs, assets=[],
                         n_subjects_in_cohort=20, n_subjects_with_image=10)
    report = DataSpecProfiler(rr, workspace=tmp_path).profile()
    assert report["coverage"]["image_coverage"] == 0.5
    flags = report["metadata_panel"]["heterogeneity_flags"]
    assert any(f["column"] == "Sampling Frequency" for f in flags)
    assert any(f["column"] == "Manufacturer" for f in flags)


def test_profiler_html_is_self_contained(tmp_path):
    rr = RetrievalResult(spec_id="s", occurrences=[_occ(1)], assets=[],
                         n_subjects_in_cohort=2, n_subjects_with_image=1)
    path = DataSpecProfiler(rr, workspace=tmp_path).to_html()
    html = path.read_text(encoding="utf-8")
    assert "<html" in html and "http" not in html.split("<style>")[1].split("</style>")[0]


# ---------------------------------------------------------------- step (3)

def test_recipe_hash_is_stable_and_order_sensitive():
    a = PreprocessRecipe(name="r", modality="ECG",
                         ops=[{"op": "resample", "target_hz": 500}, {"op": "normalize"}])
    b = PreprocessRecipe(name="r", modality="ECG",
                         ops=[{"op": "normalize"}, {"op": "resample", "target_hz": 500}])
    assert a.recipe_hash == PreprocessRecipe(**{**a.__dict__}).recipe_hash
    assert a.recipe_hash != b.recipe_hash


def test_recipe_validation_catches_missing_metadata():
    recipe = PreprocessRecipe(name="r", modality="ECG", ops=[{"op": "resample"}])
    ok = validate_recipe(recipe, {"Sampling Frequency"})
    assert ok == []
    bad = validate_recipe(recipe, {"Manufacturer"})
    assert bad and "Sampling Frequency" in bad[0]


def test_recipe_validation_catches_unknown_op():
    recipe = PreprocessRecipe(name="r", modality="ECG", ops=[{"op": "definitely_not_an_op"}])
    assert "unknown op" in validate_recipe(recipe)[0]


# ---------------------------------------------------------------- step (4)

def test_atlas_model_json_imports_without_loss():
    card = from_atlas({"name": "Some ATLAS Model", "version": "2.0",
                       "task": "binary_classification", "modality": "CR",
                       "intendedUse": "research", "weirdVendorField": 42})
    assert card.intended_use == "research"
    assert card.hyperparameters["_atlas_extra"]["weirdVendorField"] == 42


def test_card_validation_demands_portability():
    card = ModelCard(id="m", name="m", version="1", source_type="huggingface",
                     source_uri="org/repo", input_spec={"n_leads": 12})
    problems = validate_card(card)
    assert any("checksum" in p for p in problems)
    assert any("framework" in p for p in problems)


def test_input_contract_mismatch_is_caught_before_inference():
    occs = [_occ(i, hz=250.0) for i in range(1, 4)]
    rr = RetrievalResult(spec_id="s", occurrences=occs, assets=[],
                         n_subjects_in_cohort=3, n_subjects_with_image=3)
    card = ModelCard(id="m", name="m", version="1", modality="ECG",
                     input_spec={"sampling_hz": 500, "n_leads": 12})
    check = check_input_compatibility(card, rr)
    hz = next(c for c in check["checks"] if c["field"] == "sampling_hz")
    assert hz["ok"] is False and "resample" in hz["remedy"]
    assert check["ok"] is False


# ---------------------------------------------------------------- step (5)

def test_auroc_matches_known_value():
    y = [0, 0, 1, 1]
    s = [0.1, 0.4, 0.35, 0.8]
    assert M.auroc(y, s) == pytest.approx(0.75)


def test_auroc_handles_ties():
    assert M.auroc([0, 1], [0.5, 0.5]) == pytest.approx(0.5)


def test_auprc_penalizes_rare_positive_miss():
    y = [1] + [0] * 99
    good = [0.99] + [0.01] * 99
    bad = [0.01] + [0.99] * 99
    assert M.auprc(y, good) > M.auprc(y, bad)


def test_recommend_adds_auprc_under_imbalance():
    rec = M.recommend("binary_classification", prevalence=0.03)
    assert "auprc" in rec["primary"]
    assert any("prevalence" in r for r in rec["rationale"])


def test_bootstrap_is_seeded_and_reproducible():
    y = [0, 1] * 40
    s = [0.2, 0.8] * 40
    a = M.bootstrap_ci(M.auroc, y, s, n=100, seed=7)
    b = M.bootstrap_ci(M.auroc, y, s, n=100, seed=7)
    assert a == b and a["lo"] <= a["point"] <= a["hi"]


def test_evaluator_stratifies_and_reports_gap(tmp_path):
    # good at 500 Hz, coin-flip at 250 Hz — the overall number would hide this
    y, s, g = [], [], []
    for i in range(120):
        hz = 500 if i < 60 else 250
        label = i % 2
        y.append(label)
        s.append((0.9 if label else 0.1) if hz == 500 else 0.5)
        g.append(hz)
    spec = EvaluationSpec(task="binary_classification", subgroup_by=["Sampling Frequency"],
                          bootstrap_n=50)
    res = Evaluator(spec, tmp_path).evaluate(y, s, "model:test",
                                             groups={"Sampling Frequency": g})
    levels = res.subgroups["Sampling Frequency"]["levels"]
    assert levels["500"]["auroc"]["point"] > levels["250"]["auroc"]["point"]
    assert res.subgroups["Sampling Frequency"]["disparity"]["auroc"]["gap"] > 0.3


def test_evaluator_flags_small_subgroups(tmp_path):
    y = [0, 1] * 30 + [0, 1] * 3
    s = [0.2, 0.8] * 30 + [0.3, 0.7] * 3
    g = ["big"] * 60 + ["tiny"] * 6
    spec = EvaluationSpec(task="binary_classification", bootstrap_n=20)
    res = Evaluator(spec, tmp_path).evaluate(y, s, "m", groups={"site": g})
    assert res.subgroups["site"]["levels"]["tiny"]["small_n"] is True


def test_threshold_rule_is_recorded(tmp_path):
    spec = EvaluationSpec(task="binary_classification", operating_point="fixed_threshold",
                          threshold=0.42, bootstrap_n=10)
    res = Evaluator(spec, tmp_path).evaluate([0, 1, 0, 1], [0.1, 0.9, 0.3, 0.8], "m")
    assert res.overall["threshold"] == 0.42
    assert res.overall["threshold_rule"] == "fixed_threshold"


# ---------------------------------------------------------------- manifest

def test_manifest_verify_names_actionable_gaps():
    man = manifest_mod.build_manifest(
        _cohort(), RetrievalSpec(cohort=_cohort(), local_path_root="/data"),
        PreprocessRecipe(name="r", modality="ECG", ops=[{"op": "normalize"}]),
        [ModelCard(id="m", name="m", version="1", source_type="huggingface",
                   source_uri="org/repo")],
        EvaluationSpec(task="binary_classification"))
    res = manifest_mod.verify(man)
    assert res["portable"] is False
    joined = " ".join(res["issues"])
    assert "ATLAS cohort JSON" in joined
    assert "checksum" in joined
    assert "local_path_root" in joined


def test_manifest_roundtrips(tmp_path):
    man = manifest_mod.build_manifest(
        _cohort(), RetrievalSpec(cohort=_cohort()), None, [],
        EvaluationSpec(task="binary_classification"))
    p = man.to_json(tmp_path / "run_manifest.json")
    assert json.loads(p.read_text())["run_id"] == man.run_id


# ---------------------------------------------------------------- pipeline

def test_dry_run_end_to_end(tmp_path):
    from mival.run.pipeline import ValidationRun, load_config
    cfg = load_config(REPO / "configs" / "stemi_ecg.yaml")
    out = ValidationRun(cfg, None, tmp_path).dry_run()
    assert out["recipe"]["problems"] == []          # shipped recipe is valid
    assert (tmp_path / "run_manifest.json").exists()
    assert out["portability"]["portable"] is False  # atlas json intentionally null
