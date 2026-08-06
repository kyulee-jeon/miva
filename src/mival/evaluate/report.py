"""Result reporting: a comparison table across models + a portable HTML sheet.

The comparison table is the object the framework exists to produce — two models
(e.g. a supervised STEMI classifier and ECGFounder) evaluated on the *same*
cohort, the *same* recipe, and the *same* metric spec, with the differences
between them attributable to the models rather than to the pipeline.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..ontology.objects import EvaluationResult
from ..profile.report import _CSS, _esc


def comparison_table(results: Sequence[EvaluationResult]) -> list[dict]:
    rows = []
    for r in results:
        row: dict[str, Any] = {"model": r.model_id, "n": r.n, "n_positive": r.n_positive}
        for k, v in r.overall.items():
            if isinstance(v, dict) and "point" in v:
                row[k] = f"{v['point']:.3f} ({v['lo']:.3f}–{v['hi']:.3f})"
        for k, v in (r.overall.get("threshold_metrics") or {}).items():
            row[k] = round(v, 3) if isinstance(v, (int, float)) else v
        row["threshold"] = round(r.overall.get("threshold", float("nan")), 4)
        row["threshold_rule"] = r.overall.get("threshold_rule")
        row["ece"] = round(r.calibration.get("ece", float("nan")), 4) if r.calibration else None
        rows.append(row)
    return rows


def _table(rows: list[dict]) -> str:
    if not rows:
        return "<div class='sub'>no results</div>"
    cols = list(rows[0])
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_esc(r.get(c, ''))}</td>" for c in cols) + "</tr>"
                   for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _subgroup_section(r: EvaluationResult) -> str:
    if not r.subgroups:
        return "<div class='sub'>no subgroups requested</div>"
    out = []
    for var, blk in r.subgroups.items():
        rows = []
        for level, m in blk["levels"].items():
            cell = {"level": level, "n": m["n"], "n_pos": m["n_positive"]}
            for k, v in m.items():
                if isinstance(v, dict) and "point" in v:
                    cell[k] = f"{v['point']:.3f} ({v['lo']:.3f}–{v['hi']:.3f})"
            if m.get("small_n"):
                cell["level"] = f"{level} ⚠"
            rows.append(cell)
        disp = blk.get("disparity", {})
        note = ""
        for metric, d in disp.items():
            note += (f"<div class='flag'><b>{_esc(metric)} gap {d['gap']:.3f}</b> — "
                     f"best <code>{_esc(d['best']['level'])}</code> vs worst "
                     f"<code>{_esc(d['worst']['level'])}</code><br>"
                     f"<span class='sub'>{_esc(d['interpretation'])}</span></div>")
        out.append(f"<div class='card'><h3>{_esc(var)}</h3>{note}{_table(rows)}</div>")
    return "".join(out)


def render_html(results: Sequence[EvaluationResult], manifest: dict | None = None) -> str:
    comp = _table(comparison_table(results))
    subs = "".join(f"<h2>Subgroups — {_esc(r.model_id)}</h2>{_subgroup_section(r)}"
                   for r in results)
    rec = results[0].overall.get("metric_recommendation", {}) if results else {}
    rationale = "".join(f"<li>{_esc(x)}</li>" for x in rec.get("rationale", []))
    man = (f"<div class='card'><details><summary>run_manifest.json</summary>"
           f"<pre style='overflow:auto;font-size:11px'>"
           f"{_esc(json.dumps(manifest, indent=2, default=str))}</pre></details></div>"
           if manifest else "")
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>MI-VAL results</title><style>{_CSS}</style>
<div class="wrap">
<h1>MI-VAL — Validation Results</h1>
<p class="sub">Same cohort, same recipe, same metric spec — differences are attributable
to the models.</p>
<h2>Model comparison</h2><div class="card">{comp}</div>
<h2>Metric choice rationale</h2><div class="card"><ul>{rationale or '<li>—</li>'}</ul></div>
{subs}
<h2>Provenance</h2>{man}
</div></html>"""


def write_report(results: Sequence[EvaluationResult], path: Path | str,
                 manifest: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(results, manifest), encoding="utf-8")
    return path
