"""Self-contained HTML rendering for the data-spec profile.

Deliberately dependency-free: the report has to open on a locked-down analysis
workstation inside a hospital network, where "just pip install plotly" is not
always an option. Bars are CSS; there is no CDN call.
"""

from __future__ import annotations

import html
import json
from typing import Any

_CSS = """
:root{--ink:#1a1d21;--muted:#6b7280;--line:#e5e7eb;--bg:#fff;--accent:#2563eb;
--warn:#b45309;--warnbg:#fef3c7}
*{box-sizing:border-box}
body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;
color:var(--ink);background:#f7f8fa;margin:0;padding:32px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:32px 0 12px;
padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:13px;margin:18px 0 6px;color:var(--muted);text-transform:uppercase;
letter-spacing:.04em}
.sub{color:var(--muted);margin:0 0 24px}
.card{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:18px;
margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:14px}
.kpi .v{font-size:22px;font-weight:650} .kpi .l{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:8px;background:var(--accent);border-radius:4px;display:inline-block;min-width:2px}
.barwrap{display:flex;align-items:center;gap:8px}
.flag{background:var(--warnbg);border-left:3px solid var(--warn);padding:9px 12px;
border-radius:0 6px 6px 0;margin-bottom:8px}
.flag b{color:var(--warn)}
.tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:5px;
padding:1px 7px;font-size:11px;margin-left:6px}
code{background:#f3f4f6;padding:1px 5px;border-radius:4px;font-size:12px}
"""


def _esc(x: Any) -> str:
    return html.escape(str(x))


def _bar_row(label: str, count: int, total: int) -> str:
    pct = (count / total * 100) if total else 0
    return (f"<tr><td>{_esc(label)}</td><td class='num'>{count}</td>"
            f"<td class='num'>{pct:.1f}%</td>"
            f"<td style='width:45%'><div class='barwrap'>"
            f"<span class='bar' style='width:{max(pct,0.5):.1f}%'></span></div></td></tr>")


def _column_card(s: dict) -> str:
    head = f"<h3>{_esc(s['name'])}<span class='tag'>{_esc(s['type'])}</span></h3>"
    meta = (f"<div class='sub'>n={s['n']} &middot; missing={s['n_missing']} "
            f"({(s.get('missing_rate') or 0):.1%}) &middot; unique={s['n_unique']}</div>")
    if s["type"] == "numeric":
        body = ("<table><tr><th>mean</th><th>sd</th><th>min</th><th>p25</th>"
                "<th>median</th><th>p75</th><th>max</th></tr><tr>"
                + "".join(f"<td class='num'>{_esc(s.get(k))}</td>"
                          for k in ("mean", "sd", "min", "p25", "median", "p75", "max"))
                + "</tr></table>")
        vc = s.get("value_counts")
        if vc:
            total = sum(vc.values())
            body += ("<table style='margin-top:10px'><tr><th>value</th><th>n</th>"
                     "<th>%</th><th></th></tr>"
                     + "".join(_bar_row(k, v, total) for k, v in vc.items()) + "</table>")
    else:
        vc = s.get("value_counts", {})
        total = sum(vc.values()) or 1
        body = ("<table><tr><th>value</th><th>n</th><th>%</th><th></th></tr>"
                + "".join(_bar_row(k, v, total) for k, v in vc.items()) + "</table>")
    return f"<div class='card'>{head}{meta}{body}</div>"


def render_html(report: dict) -> str:
    cov = report.get("coverage", {})
    mp = report.get("metadata_panel", {})
    st = mp.get("structural", {})
    cp = report.get("clinical_panel", {})

    kpis = [
        ("Cohort subjects", cov.get("n_subjects_in_cohort")),
        ("With image", cov.get("n_subjects_with_image")),
        ("Coverage", f"{cov['image_coverage']:.1%}" if cov.get("image_coverage") else "—"),
        ("Image occurrences", st.get("n_occurrences")),
        ("Missing metadata", st.get("n_missing_metadata")),
    ]
    kpi_html = "".join(f"<div class='kpi'><div class='v'>{_esc(v)}</div>"
                       f"<div class='l'>{_esc(l)}</div></div>" for l, v in kpis)

    flags = mp.get("heterogeneity_flags", [])
    flag_html = "".join(
        f"<div class='flag'><b>{_esc(f['flag'])}</b> &middot; <code>{_esc(f['column'])}</code> "
        f"— {_esc(f['detail'])}<br><span class='sub'>{_esc(f['action'])}</span></div>"
        for f in flags) or "<div class='sub'>No heterogeneity flags raised.</div>"

    attrition = report.get("attrition", [])
    att_html = ("<table><tr><th>step</th><th>rows remaining</th><th>removed</th></tr>"
                + "".join(f"<tr><td>{_esc(a.get('step'))}</td>"
                          f"<td class='num'>{_esc(a.get('n_rows', '—'))}</td>"
                          f"<td class='num'>{_esc(a.get('removed', '—'))}</td></tr>"
                          for a in attrition) + "</table>")

    cols = mp.get("features", {})
    struct_cards = "".join(_column_card(st[k]) for k in ("modality", "days_from_index")
                           if isinstance(st.get(k), dict))
    feat_cards = "".join(_column_card(s) for s in cols.values())

    clin_html = "<div class='sub'>Not available.</div>"
    if cp.get("available"):
        parts = []
        for key in ("age_at_index", "gender"):
            if isinstance(cp.get(key), dict):
                parts.append(_column_card(cp[key]))
        if cp.get("conditions"):
            rows = "".join(f"<tr><td>{_esc(k)}</td><td class='num'>{v['n']}</td>"
                           f"<td class='num'>{v['pct']:.1%}</td></tr>"
                           for k, v in cp["conditions"].items())
            parts.append("<div class='card'><h3>Conditions (lookback window)</h3>"
                         f"<table><tr><th>concept</th><th>n</th><th>%</th></tr>{rows}</table></div>")
        for k, s in (cp.get("labs") or {}).items():
            parts.append(_column_card(s))
        clin_html = "".join(parts)

    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>MI-VAL data specification</title><style>{_CSS}</style>
<div class="wrap">
<h1>MI-VAL — Data Specification</h1>
<p class="sub">retrieval spec <code>{_esc(report.get('retrieval_spec_id'))}</code></p>
<div class="kpis">{kpi_html}</div>
<h2>Attrition</h2><div class="card">{att_html}</div>
<h2>Heterogeneity flags</h2><div class="card">{flag_html}</div>
<h2>Panel A — Acquisition metadata (MI-CDM)</h2>{struct_cards}{feat_cards}
<h2>Panel B — Clinical characteristics at index</h2>{clin_html}
<h2>Raw</h2><div class="card"><details><summary>report.json</summary>
<pre style="overflow:auto;font-size:11px">{_esc(json.dumps(report, indent=2, default=str))}</pre>
</details></div>
</div></html>"""
