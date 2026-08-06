"""MI-VAL step (2) — DataSpecProfiler.

Answers, before a single model is loaded: *what is actually in this dataset?*

Two panels, because two different failure modes hide in two different places:

  A. Acquisition metadata (from MI-CDM image_feature/measurement).
     Acquisition parameters — sampling rate, lead set, device, exposure —
     shift model performance as much as demographics do, and they are the
     variable most often left unreported. Profiling them first is what turns a
     surprise into a stratification variable.

  B. Clinical characteristics of the *imaged* subset, as of index date.
     The imaged subset is not the ATLAS cohort. Reporting the two side by side
     makes selection bias visible instead of implicit.

Output is a dict of typed summaries plus an optional self-contained HTML
report; nothing here requires a GPU or a model.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ..ontology.objects import ImageOccurrenceRef, RetrievalResult

MISSING = object()


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not (
        isinstance(x, float) and math.isnan(x))


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    idx = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return float(sorted_vals[int(idx)])
    return float(sorted_vals[lo] * (hi - idx) + sorted_vals[hi] * (idx - lo))


def summarize_values(values: Sequence[Any], name: str, top_k: int = 12) -> dict:
    """One column summary. Numeric and categorical are reported differently
    because conflating them is how a 500-vs-1000 Hz split gets reported as
    'mean sampling frequency 743 Hz' and then quietly breaks a model."""
    n = len(values)
    present = [v for v in values if v is not None and v != ""]
    n_missing = n - len(present)
    flat: list[Any] = []
    list_valued = False
    for v in present:
        if isinstance(v, (list, tuple)):
            list_valued = True
            flat.append(tuple(v))
        else:
            flat.append(v)

    out: dict[str, Any] = {
        "name": name,
        "n": n,
        "n_missing": n_missing,
        "missing_rate": round(n_missing / n, 4) if n else None,
        "n_unique": len(set(flat)),
        "list_valued": list_valued,
    }

    numeric = [float(v) for v in flat if _is_number(v)]
    if numeric and len(numeric) >= 0.8 * max(len(flat), 1):
        s = sorted(numeric)
        mean = sum(s) / len(s)
        var = sum((x - mean) ** 2 for x in s) / len(s) if len(s) > 1 else 0.0
        out.update({
            "type": "numeric",
            "mean": round(mean, 4),
            "sd": round(math.sqrt(var), 4),
            "min": s[0], "p25": _quantile(s, .25), "median": _quantile(s, .5),
            "p75": _quantile(s, .75), "max": s[-1],
            # discrete numeric attributes (500/1000 Hz) deserve a value table too
            "value_counts": dict(Counter(s).most_common(top_k)) if len(set(s)) <= top_k else None,
        })
    else:
        counts = Counter(flat).most_common(top_k)
        out.update({
            "type": "categorical",
            "value_counts": {str(k): v for k, v in counts},
            "top_share": round(counts[0][1] / len(flat), 4) if flat else None,
        })
    return out


class DataSpecProfiler:
    """Profile a RetrievalResult. Pure-python; pandas optional."""

    def __init__(self, retrieval: RetrievalResult,
                 characteristics: Optional[list[dict]] = None,
                 workspace: Path | str = "_workspace"):
        self.retrieval = retrieval
        self.characteristics = characteristics or []
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    # -- panel A: acquisition metadata ------------------------------------

    def metadata_panel(self) -> dict:
        occs = self.retrieval.occurrences
        keys: set[str] = set()
        for o in occs:
            if o.metadata:
                keys.update(o.metadata.features.keys())

        columns = {}
        for k in sorted(keys):
            vals = [o.metadata.get(k) if o.metadata else None for o in occs]
            columns[k] = summarize_values(vals, k)

        structural = {
            "n_occurrences": len(occs),
            "n_subjects": len({o.person_id for o in occs}),
            "modality": summarize_values([o.modality for o in occs], "modality"),
            "days_from_index": summarize_values(
                [o.days_from_index for o in occs], "days_from_index"),
            "n_missing_metadata": sum(1 for o in occs if not o.metadata or not o.metadata.features),
        }
        return {"structural": structural, "features": columns,
                "heterogeneity_flags": self._flag_heterogeneity(columns)}

    @staticmethod
    def _flag_heterogeneity(columns: dict) -> list[dict]:
        """Surface the columns a reviewer should look at, with a reason.

        A flag is not a failure — it is a prompt to either stratify the
        evaluation by that column or state in the report why you did not.
        """
        flags = []
        for name, s in columns.items():
            if s.get("missing_rate", 0) and s["missing_rate"] > 0.05:
                flags.append({"column": name, "flag": "high_missingness",
                              "detail": f"{s['missing_rate']:.1%} missing",
                              "action": "impute explicitly or exclude; do not let it default"})
            if s["type"] == "categorical" and 1 < s["n_unique"] <= 20:
                flags.append({"column": name, "flag": "multi_valued_acquisition",
                              "detail": f"{s['n_unique']} distinct values",
                              "action": "candidate stratification variable for step (5)"})
            if s["type"] == "numeric" and s.get("value_counts") and len(s["value_counts"]) > 1:
                flags.append({"column": name, "flag": "discrete_numeric_mix",
                              "detail": f"values {list(s['value_counts'])}",
                              "action": "harmonize in the recipe (e.g. resample) and record it"})
        return flags

    # -- panel B: clinical characteristics --------------------------------

    def clinical_panel(self) -> dict:
        if not self.characteristics:
            return {"available": False,
                    "note": "run retrieve/sql/cohort_characteristics.sql to populate"}
        by_block: dict[str, list[dict]] = {}
        for row in self.characteristics:
            by_block.setdefault(row.get("block", "other"), []).append(row)

        panel: dict[str, Any] = {"available": True}
        demo = by_block.get("demographics", [])
        if demo:
            panel["age_at_index"] = summarize_values(
                [r.get("value_num") for r in demo], "age_at_index")
            panel["gender"] = summarize_values([r.get("value_txt") for r in demo], "gender")
        if by_block.get("condition"):
            n_people = len({r["person_id"] for r in by_block["condition"]})
            counts = Counter(r["name"] for r in by_block["condition"])
            panel["conditions"] = {name: {"n": c, "pct": round(c / max(n_people, 1), 4)}
                                   for name, c in counts.most_common(30)}
        if by_block.get("measurement"):
            per_lab: dict[str, list[float]] = {}
            for r in by_block["measurement"]:
                if _is_number(r.get("value_num")):
                    per_lab.setdefault(r["name"], []).append(float(r["value_num"]))
            panel["labs"] = {k: summarize_values(v, k) for k, v in per_lab.items()}
        return panel

    # -- assembly ----------------------------------------------------------

    def profile(self) -> dict:
        report = {
            "retrieval_spec_id": self.retrieval.spec_id,
            "attrition": self.retrieval.attrition,
            "coverage": {
                "n_subjects_in_cohort": self.retrieval.n_subjects_in_cohort,
                "n_subjects_with_image": self.retrieval.n_subjects_with_image,
                "image_coverage": (
                    round(self.retrieval.n_subjects_with_image /
                          self.retrieval.n_subjects_in_cohort, 4)
                    if self.retrieval.n_subjects_in_cohort > 0 else None),
            },
            "metadata_panel": self.metadata_panel(),
            "clinical_panel": self.clinical_panel(),
        }
        (self.workspace / "02_profiler_report.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report

    def to_html(self, report: Optional[dict] = None,
                path: Path | str | None = None) -> Path:
        from .report import render_html
        report = report or self.profile()
        path = Path(path or self.workspace / "02_data_spec.html")
        path.write_text(render_html(report), encoding="utf-8")
        return path
