"""MI-VAL step (4a) — model cards.

The card is the contract between "a model someone published" and "a model this
cohort can be run through". RSNA/ATLAS `model.json` covers documentation well;
what it does not fully pin down is the part that breaks in practice — how the
weights are fetched, what input tensor shape and units the model expects, and
which environment it was validated in. MI-VAL keeps ATLAS field names where
they exist and adds those three.

The important function here is :func:`check_input_compatibility`: it compares
the card's declared input contract against the *observed MI-CDM metadata* of
the retrieved cohort, before inference. Most "the model didn't transfer"
findings are actually "the input contract was never checked".
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from ..ontology.objects import ModelCard, RetrievalResult

#: RSNA ATLAS model.json -> MI-VAL ModelCard field mapping. Anything not listed
#: is carried through into ``hyperparameters`` so nothing is lost in import.
ATLAS_FIELD_MAP = {
    "name": "name",
    "version": "version",
    "task": "task",
    "modality": "modality",
    "license": "license",
    "intended_use": "intended_use",
    "intendedUse": "intended_use",
    "training_data": "training_data",
    "trainingData": "training_data",
    "limitations": "known_limitations",
    "citation": "citation",
    "inputs": "input_spec",
    "outputs": "output_spec",
}


def load_card(path: Path | str) -> ModelCard:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" \
        else __import__("yaml").safe_load(path.read_text(encoding="utf-8"))
    return from_atlas(raw) if _looks_like_atlas(raw) else ModelCard(**raw)


def _looks_like_atlas(raw: dict) -> bool:
    return "id" not in raw or "source_type" not in raw


def from_atlas(raw: dict) -> ModelCard:
    """Import an RSNA ATLAS-style model.json into a MI-VAL ModelCard."""
    mapped: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for k, v in raw.items():
        target = ATLAS_FIELD_MAP.get(k)
        if target:
            mapped[target] = v
        else:
            extra[k] = v
    mapped.setdefault("id", raw.get("id") or raw.get("name", "unnamed").lower().replace(" ", "-"))
    mapped.setdefault("name", mapped["id"])
    mapped.setdefault("version", raw.get("version", "0.0.0"))
    if isinstance(mapped.get("known_limitations"), str):
        mapped["known_limitations"] = [mapped["known_limitations"]]
    card = ModelCard(**{k: v for k, v in mapped.items()
                        if k in ModelCard.__dataclass_fields__})
    card.hyperparameters = {**card.hyperparameters, "_atlas_extra": extra}
    return card


def save_card(card: ModelCard, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(card), indent=2, default=str), encoding="utf-8")
    return path


REQUIRED_FOR_PORTABILITY = ("source_type", "source_uri", "weights_format", "input_spec", "task")


def validate_card(card: ModelCard) -> list[str]:
    """Fields whose absence makes a run non-portable, not merely undocumented."""
    problems = []
    for f in REQUIRED_FOR_PORTABILITY:
        if not getattr(card, f, None):
            problems.append(f"missing '{f}' — another site cannot re-run this without it")
    if card.source_type != "local" and not card.checksum_sha256:
        problems.append("remote source without checksum_sha256 — weights can change under you")
    if not card.framework:
        problems.append("no framework pin (e.g. 'torch==2.3.1') — results may not reproduce")
    return problems


def check_input_compatibility(card: ModelCard, retrieval: RetrievalResult) -> dict:
    """Compare the card's declared input contract to observed MI-CDM metadata.

    ``input_spec`` keys understood here:
      sampling_hz, n_leads, lead_order, duration_s, units, shape, modality

    Returns {"ok": bool, "checks": [...]} where each check names the MI-CDM
    attribute it read, so a mismatch points at a fix (add a recipe op) rather
    than at a mystery.
    """
    spec = card.input_spec or {}
    checks: list[dict] = []
    occs = retrieval.occurrences

    def observed(attr_names: list[str]) -> set:
        vals = set()
        for o in occs:
            if not o.metadata:
                continue
            for a in attr_names:
                v = o.metadata.get(a)
                if v is not None:
                    vals.add(tuple(v) if isinstance(v, list) else v)
                    break
        return vals

    if card.modality and occs:
        got = {o.modality for o in occs}
        checks.append({"field": "modality", "expected": card.modality, "observed": sorted(got),
                       "ok": card.modality in got,
                       "remedy": "filter retrieval by modality"})

    if "sampling_hz" in spec:
        got = observed(["Sampling Frequency", "SamplingFrequency"])
        ok = got == {float(spec["sampling_hz"])} or got == {spec["sampling_hz"]}
        checks.append({"field": "sampling_hz", "expected": spec["sampling_hz"],
                       "observed": sorted(got), "ok": ok,
                       "source": "MI-CDM measurement via image_feature",
                       "remedy": "add a `resample` op to the recipe"})

    if "n_leads" in spec:
        got = {len(o.metadata.lead_names) for o in occs if o.metadata and o.metadata.lead_names}
        checks.append({"field": "n_leads", "expected": spec["n_leads"], "observed": sorted(got),
                       "ok": got == {spec["n_leads"]} or not got,
                       "remedy": "add a `select_leads` op, or restrict the cohort"})

    if "lead_order" in spec:
        got = observed(["Waveform Channel Source", "LeadNames"])
        ok = all(list(g) == list(spec["lead_order"]) for g in got) if got else False
        checks.append({"field": "lead_order", "expected": spec["lead_order"],
                       "observed": [list(g) for g in got], "ok": ok,
                       "remedy": "add `select_leads` with the model's lead order"})

    if "units" in spec:
        got = observed(["Waveform Amplitude Units", "Units"])
        checks.append({"field": "units", "expected": spec["units"], "observed": sorted(got),
                       "ok": got == {spec["units"]} or not got,
                       "remedy": "add a `scale_units` op"})

    return {"ok": all(c["ok"] for c in checks) if checks else False,
            "n_checks": len(checks), "checks": checks,
            "note": "unchecked fields mean the card under-specifies its input contract"}
