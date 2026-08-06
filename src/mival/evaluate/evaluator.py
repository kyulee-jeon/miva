"""MI-VAL step (5) — Evaluator: overall + subgroup + calibration, one object.

Subgroups come from two sources and both matter:
  * MI-CDM acquisition metadata (sampling rate, lead set, device, exposure)
  * clinical/demographic covariates from the OMOP side

Reporting only the second is the current norm; the first is where a large share
of performance variation actually lives, and MI-CDM is what makes it queryable
in the first place. Small subgroups are reported with their n and CI rather
than suppressed, and flagged when n falls below ``min_subgroup_n``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Sequence

from ..ontology.objects import EvaluationResult, EvaluationSpec, Provenance
from . import metrics as M


class Evaluator:
    def __init__(self, spec: EvaluationSpec, workspace: Path | str = "_workspace",
                 min_subgroup_n: int = 30):
        self.spec = spec
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.min_subgroup_n = min_subgroup_n

    # ------------------------------------------------------------------

    def evaluate(self, y_true: Sequence[int], y_score: Sequence[float],
                 model_id: str, groups: Optional[dict[str, Sequence[Any]]] = None,
                 ids: Optional[Sequence[int]] = None) -> EvaluationResult:
        groups = groups or {}
        n = len(y_true)
        n_pos = int(sum(y_true))
        prevalence = n_pos / n if n else None

        threshold = M.pick_threshold(y_true, y_score, self.spec.operating_point,
                                     self.spec.threshold)
        overall = self._block(y_true, y_score, threshold)
        overall["threshold"] = threshold
        overall["threshold_rule"] = self.spec.operating_point
        overall["prevalence"] = prevalence
        overall["metric_recommendation"] = M.recommend(self.spec.task, prevalence)

        subgroups: dict[str, Any] = {}
        for name, values in groups.items():
            subgroups[name] = self._subgroup(name, values, y_true, y_score, threshold)

        calib = (M.calibration_curve(y_true, y_score) if self.spec.calibration else {})

        result = EvaluationResult(
            spec_id=self.spec.object_id, model_id=model_id, n=n, n_positive=n_pos,
            overall=overall, subgroups=subgroups, calibration=calib,
            provenance=Provenance(produced_by="mival.evaluate.Evaluator",
                                  source_ref=self.spec.object_id),
        )
        if ids is not None:
            path = self.workspace / f"05_predictions_{model_id.replace(':', '_')}.csv"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("image_occurrence_id,y_true,y_score\n")
                for i, t, s in zip(ids, y_true, y_score):
                    fh.write(f"{i},{t},{s}\n")
            result.predictions_path = str(path)

        (self.workspace / f"05_eval_{model_id.replace(':', '_')}.json").write_text(
            json.dumps(asdict(result), indent=2, default=str), encoding="utf-8")
        return result

    # ------------------------------------------------------------------

    def _block(self, y_true: Sequence[int], y_score: Sequence[float],
               threshold: float) -> dict:
        out: dict[str, Any] = {"n": len(y_true), "n_positive": int(sum(y_true))}
        for name in self.spec.primary_metrics:
            fn = M.METRIC_FNS.get(name)
            if fn is None:
                continue
            out[name] = M.bootstrap_ci(fn, y_true, y_score, self.spec.bootstrap_n,
                                       self.spec.ci_level, self.spec.seed)
        y_pred = [1 if s >= threshold else 0 for s in y_score]
        c = M.confusion(y_true, y_pred)
        out["confusion"] = c
        point = M.from_confusion(c)
        wanted = self.spec.secondary_metrics or list(point)
        out["threshold_metrics"] = {k: v for k, v in point.items() if k in wanted}
        return out

    def _subgroup(self, name: str, values: Sequence[Any], y_true: Sequence[int],
                  y_score: Sequence[float], threshold: float) -> dict:
        buckets: dict[Any, list[int]] = {}
        for i, v in enumerate(values):
            key = tuple(v) if isinstance(v, list) else v
            buckets.setdefault(key, []).append(i)

        levels = {}
        for key, idx in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            yt = [y_true[i] for i in idx]
            ys = [y_score[i] for i in idx]
            block = self._block(yt, ys, threshold)
            block["small_n"] = len(idx) < self.min_subgroup_n
            if block["small_n"]:
                block["caveat"] = (f"n={len(idx)} < {self.min_subgroup_n}; interpret the CI, "
                                   "not the point estimate")
            levels[str(key)] = block

        spread = self._spread(levels)
        return {"variable": name, "n_levels": len(levels), "levels": levels,
                "disparity": spread}

    @staticmethod
    def _spread(levels: dict) -> dict:
        """Gap between best and worst level on the first primary metric.

        Reported as a gap plus a note on CI overlap rather than a p-value: with
        subgroups this small, the honest statement is usually 'the CIs overlap,
        we cannot rule out a difference of X'.
        """
        pts = {}
        for lvl, blk in levels.items():
            for k, v in blk.items():
                if isinstance(v, dict) and "point" in v:
                    pts.setdefault(k, {})[lvl] = v
                    break
        out = {}
        for metric, by_level in pts.items():
            usable = {k: v for k, v in by_level.items() if v["point"] == v["point"]}
            if len(usable) < 2:
                continue
            hi = max(usable.items(), key=lambda kv: kv[1]["point"])
            lo = min(usable.items(), key=lambda kv: kv[1]["point"])
            overlap = not (lo[1]["hi"] < hi[1]["lo"])
            out[metric] = {
                "best": {"level": hi[0], **hi[1]},
                "worst": {"level": lo[0], **lo[1]},
                "gap": hi[1]["point"] - lo[1]["point"],
                "ci_overlap": overlap,
                "interpretation": ("CIs overlap — gap not established"
                                   if overlap else "CIs disjoint — gap is unlikely to be noise"),
            }
        return out
