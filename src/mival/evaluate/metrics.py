"""MI-VAL step (5) — standardized, reproducible result metrics.

Two ideas drive this module.

1. **Metric choice is a decision, not a default.** Following the Metrics
   Reloaded logic, the task fingerprint (task type + prevalence + what a false
   positive costs) determines which metrics are defensible. :func:`recommend`
   makes that reasoning explicit and writes it into the report, so a reader can
   disagree with the choice instead of guessing at it.

2. **A single number is not a result.** Every metric ships with a bootstrap CI,
   and every run is sliced by the acquisition-metadata subgroups that step (2)
   flagged. A model that is fine at 500 Hz and broken at 250 Hz reports one
   AUROC and hides the failure; the stratified table does not.

Implementations are dependency-light (pure python + optional numpy) so results
are identical across sites regardless of sklearn version.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Optional, Sequence

Number = float


# ---------------------------------------------------------------- primitives

def _pairs(y_true: Sequence[int], y_score: Sequence[float]) -> list[tuple[int, float]]:
    return [(int(t), float(s)) for t, s in zip(y_true, y_score)]


def auroc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """Rank-based AUROC with tie correction (Mann-Whitney U)."""
    data = sorted(_pairs(y_true, y_score), key=lambda p: p[1])
    n = len(data)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and data[j + 1][1] == data[i][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    pos = [r for r, (t, _) in zip(ranks, data) if t == 1]
    n_pos, n_neg = len(pos), n - len(pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return (sum(pos) - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def auprc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """Average precision. Preferred over AUROC when positives are rare, because
    AUROC stays flattering under heavy class imbalance."""
    data = sorted(_pairs(y_true, y_score), key=lambda p: -p[1])
    n_pos = sum(t for t, _ in data)
    if n_pos == 0:
        return float("nan")
    tp = 0
    total = 0.0
    for i, (t, _) in enumerate(data, start=1):
        if t == 1:
            tp += 1
            total += tp / i
    return total / n_pos


def confusion(y_true: Sequence[int], y_pred: Sequence[int]) -> dict:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def from_confusion(c: dict) -> dict:
    tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
    sens = tp / (tp + fn) if tp + fn else float("nan")
    spec = tn / (tn + fp) if tn + fp else float("nan")
    ppv = tp / (tp + fp) if tp + fp else float("nan")
    npv = tn / (tn + fn) if tn + fn else float("nan")
    f1 = 2 * ppv * sens / (ppv + sens) if (ppv + sens) else float("nan")
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = ((tp * tn - fp * fn) / denom) if denom else float("nan")
    bal = (sens + spec) / 2
    return {"sensitivity": sens, "specificity": spec, "ppv": ppv, "npv": npv,
            "f1": f1, "mcc": mcc, "balanced_accuracy": bal,
            "accuracy": (tp + tn) / max(tp + tn + fp + fn, 1)}


def brier(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    return sum((s - t) ** 2 for t, s in zip(y_true, y_score)) / max(len(y_true), 1)


def calibration_curve(y_true: Sequence[int], y_score: Sequence[float], n_bins: int = 10) -> dict:
    bins: list[list[tuple[int, float]]] = [[] for _ in range(n_bins)]
    for t, s in _pairs(y_true, y_score):
        bins[min(int(s * n_bins), n_bins - 1)].append((t, s))
    rows, ece = [], 0.0
    n = max(len(y_true), 1)
    for i, b in enumerate(bins):
        if not b:
            continue
        obs = sum(t for t, _ in b) / len(b)
        pred = sum(s for _, s in b) / len(b)
        rows.append({"bin": i, "n": len(b), "mean_predicted": pred, "observed": obs})
        ece += len(b) / n * abs(obs - pred)
    return {"bins": rows, "ece": ece, "brier": brier(y_true, y_score)}


# ---------------------------------------------------------- operating points

def pick_threshold(y_true: Sequence[int], y_score: Sequence[float], mode: str = "youden",
                   value: Optional[float] = None) -> float:
    """Choose the decision threshold *explicitly*.

    An unstated 0.5 is the most common silent assumption in clinical AI
    reporting; naming the rule (and, for fixed-sensitivity operating points,
    the target) is what makes a number comparable across sites.
    """
    if mode == "fixed_threshold":
        if value is None:
            raise ValueError("fixed_threshold requires a threshold value")
        return float(value)
    cands = sorted({float(s) for s in y_score})
    if not cands:
        return 0.5
    best, best_score = cands[0], -math.inf
    for thr in cands:
        c = confusion(y_true, [1 if s >= thr else 0 for s in y_score])
        m = from_confusion(c)
        if mode == "youden":
            score = m["sensitivity"] + m["specificity"] - 1
        elif mode == "fixed_sensitivity":
            score = -abs(m["sensitivity"] - (value or 0.9))
        elif mode == "fixed_specificity":
            score = -abs(m["specificity"] - (value or 0.9))
        else:
            raise ValueError(f"unknown operating point mode '{mode}'")
        if score > best_score:
            best, best_score = thr, score
    return best


# -------------------------------------------------------------- uncertainty

def bootstrap_ci(fn: Callable[[Sequence[int], Sequence[float]], float],
                 y_true: Sequence[int], y_score: Sequence[float],
                 n: int = 1000, level: float = 0.95, seed: int = 42) -> dict:
    """Percentile bootstrap. Seeded, because "we bootstrapped" without a seed is
    not reproducible either."""
    rng = random.Random(seed)
    m = len(y_true)
    if m == 0:
        return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "n_boot": 0}
    point = fn(y_true, y_score)
    draws = []
    for _ in range(n):
        idx = [rng.randrange(m) for _ in range(m)]
        try:
            v = fn([y_true[i] for i in idx], [y_score[i] for i in idx])
        except Exception:
            continue
        if v == v:  # drop NaN draws (e.g. single-class resample)
            draws.append(v)
    if not draws:
        return {"point": point, "lo": float("nan"), "hi": float("nan"), "n_boot": 0}
    draws.sort()
    a = (1 - level) / 2
    lo = draws[max(int(a * len(draws)) - 1, 0)]
    hi = draws[min(int((1 - a) * len(draws)), len(draws) - 1)]
    return {"point": point, "lo": lo, "hi": hi, "n_boot": len(draws), "level": level}


# ------------------------------------------------------------- metric choice

METRIC_FNS: dict[str, Callable] = {"auroc": auroc, "auprc": auprc}


def recommend(task: str, prevalence: Optional[float] = None,
              cost_of_fp: str = "moderate") -> dict:
    """Problem fingerprint -> defensible metric set, with the reasoning kept.

    This is intentionally a small, auditable rule set rather than a black box:
    the point is that a reader can see the assumption and argue with it.
    """
    rec: dict[str, Any] = {"task": task, "prevalence": prevalence, "rationale": []}
    if task in ("binary_classification",):
        primary = ["auroc"]
        rec["rationale"].append("AUROC: threshold-free discrimination, comparable across sites")
        if prevalence is not None and prevalence < 0.10:
            primary.append("auprc")
            rec["rationale"].append(
                f"AUPRC added: prevalence {prevalence:.1%} < 10%, AUROC is optimistic "
                "under heavy imbalance")
        secondary = ["sensitivity", "specificity", "ppv", "npv", "f1", "mcc",
                     "balanced_accuracy"]
        rec["rationale"].append(
            "MCC / balanced accuracy over raw accuracy: accuracy tracks prevalence, not skill")
        if cost_of_fp == "high":
            rec["rationale"].append(
                "cost_of_fp=high: report at a fixed-specificity operating point and give PPV")
        rec.update({"primary": primary, "secondary": secondary,
                    "calibration": ["brier", "ece"],
                    "operating_point": "fixed_specificity" if cost_of_fp == "high" else "youden"})
    elif task == "multiclass_classification":
        rec.update({"primary": ["macro_auroc"],
                    "secondary": ["per_class_sensitivity", "macro_f1", "mcc"],
                    "calibration": ["ece"], "operating_point": "argmax"})
        rec["rationale"].append("macro averaging: unweighted by class size, so rare "
                                "classes cannot be hidden by a dominant one")
    else:
        rec.update({"primary": ["auroc"], "secondary": [], "calibration": [],
                    "operating_point": "youden"})
        rec["rationale"].append(f"no tailored rule for task '{task}' — review manually")
    return rec
