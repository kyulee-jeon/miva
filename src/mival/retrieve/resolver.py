"""MI-VAL step (1) — CohortImageResolver.

Turns a RetrievalSpec into a RetrievalResult: cohort rows -> image_occurrence
rows -> resolved DICOM files on disk, with a CONSORT-style attrition trace.

Two execution modes:
  * ``connection`` given  -> real query against PostgreSQL
  * ``connection=None``   -> dry run; emits the rendered SQL and an empty
    result so a run can be reviewed before it touches PHI.

The SQL lives in ``sql/*.sql`` on purpose. An analyst who does not trust the
Python can read, diff, and run the query by hand — which is what "standardized
query" means here rather than "hidden ORM".
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Optional

from ..ontology.objects import (
    ImageAsset,
    ImageMetadata,
    ImageOccurrenceRef,
    Provenance,
    RetrievalResult,
    RetrievalSpec,
    stable_hash,
)

SQL_DIR = Path(__file__).parent / "sql"

_SELECTION_ORDER = {
    "first": "image_occurrence_date ASC, image_occurrence_id ASC",
    "last": "image_occurrence_date DESC, image_occurrence_id DESC",
    "nearest_to_index": "ABS(image_occurrence_date - index_date) ASC, image_occurrence_id ASC",
    "all": "image_occurrence_date ASC, image_occurrence_id ASC",
}


def load_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


class CohortImageResolver:
    def __init__(self, spec: RetrievalSpec, connection: Any = None,
                 workspace: Path | str = "_workspace"):
        self.spec = spec
        self.conn = connection
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    # -- SQL rendering -----------------------------------------------------

    def render_occurrence_sql(self) -> str:
        selection = self.spec.selection
        return load_sql("cohort_image_occurrence.sql").format(
            results_schema=self.spec.cohort.results_schema,
            cohort_table=self.spec.cohort.cohort_table,
            cdm_schema=self.spec.cohort.cdm_schema,
            selection_order=_SELECTION_ORDER[selection],
        )

    def render_metadata_sql(self) -> str:
        return load_sql("image_metadata.sql").format(cdm_schema=self.spec.cohort.cdm_schema)

    def params(self) -> dict[str, Any]:
        return {
            "cohort_definition_id": self.spec.cohort.cohort_definition_id,
            "window_start_days": self.spec.window_start_days,
            "window_end_days": self.spec.window_end_days,
            "modality": self.spec.modality,
            "max_per_subject": (10**9 if self.spec.selection == "all"
                                else (self.spec.max_per_subject or 1)),
        }

    # -- execution ---------------------------------------------------------

    def resolve(self) -> RetrievalResult:
        attrition: list[dict] = []

        if self.conn is None:
            self._dump_dry_run()
            return RetrievalResult(
                spec_id=self.spec.object_id,
                occurrences=[], assets=[],
                n_subjects_in_cohort=0, n_subjects_with_image=0,
                attrition=[{"step": "dry_run",
                            "note": "no connection; SQL written to _workspace"}],
                provenance=Provenance(produced_by="mival.retrieve.CohortImageResolver",
                                      source_ref="dry-run",
                                      inputs_hash=stable_hash(asdict(self.spec))),
            )

        rows = self._fetch(self.render_occurrence_sql(), self.params())
        occurrences = [self._to_occurrence(r) for r in rows]
        attrition.append({"step": "image_occurrence in window",
                          "n_rows": len(occurrences),
                          "n_subjects": len({o.person_id for o in occurrences})})

        # metadata pass
        if occurrences:
            ids = [o.image_occurrence_id for o in occurrences]
            meta_rows = self._fetch(self.render_metadata_sql(), {"image_occurrence_ids": ids})
            by_occ = self._fold_metadata(meta_rows)
            for occ in occurrences:
                occ.metadata = by_occ.get(occ.image_occurrence_id,
                                          ImageMetadata(occ.image_occurrence_id))

        # metadata-level filters (e.g. only 12-lead, only 500Hz)
        if self.spec.metadata_filters:
            before = len(occurrences)
            occurrences = [o for o in occurrences if self._passes_filters(o)]
            attrition.append({"step": "metadata_filters",
                              "filters": self.spec.metadata_filters,
                              "removed": before - len(occurrences),
                              "n_rows": len(occurrences)})

        assets = [self.resolve_asset(o) for o in occurrences]
        if self.spec.require_local_file:
            missing = [a for a in assets if not a.exists]
            if missing:
                keep = {a.image_occurrence_id for a in assets if a.exists}
                occurrences = [o for o in occurrences if o.image_occurrence_id in keep]
                assets = [a for a in assets if a.exists]
            attrition.append({"step": "local file exists",
                              "removed": len(missing), "n_rows": len(occurrences)})

        result = RetrievalResult(
            spec_id=self.spec.object_id,
            occurrences=occurrences,
            assets=assets,
            n_subjects_in_cohort=self._count_cohort(),
            n_subjects_with_image=len({o.person_id for o in occurrences}),
            attrition=attrition,
            provenance=Provenance(produced_by="mival.retrieve.CohortImageResolver",
                                  source_ref="sql/cohort_image_occurrence.sql",
                                  inputs_hash=stable_hash(asdict(self.spec))),
        )
        self._dump_result(result)
        return result

    # -- helpers -----------------------------------------------------------

    def resolve_asset(self, occ: ImageOccurrenceRef) -> ImageAsset:
        """Join site-local root with the CDM-relative ``local_path``.

        MI-CDM stores an institution-relative path by design; the mount point
        is a site parameter. Keeping them apart is exactly what lets a
        RunManifest execute unchanged at another institution.
        """
        root = Path(self.spec.local_path_root) if self.spec.local_path_root else None
        raw = Path(occ.local_path)
        path = raw if raw.is_absolute() or root is None else root / raw
        exists = path.exists()
        return ImageAsset(
            image_occurrence_id=occ.image_occurrence_id,
            path=path,
            exists=exists,
            n_bytes=path.stat().st_size if exists and path.is_file() else None,
        )

    def _passes_filters(self, occ: ImageOccurrenceRef) -> bool:
        meta = occ.metadata
        if meta is None:
            return False
        for key, want in self.spec.metadata_filters.items():
            got = meta.get(key)
            if isinstance(want, (list, tuple, set)):
                if got not in want:
                    return False
            elif got != want:
                return False
        return True

    @staticmethod
    def _fold_metadata(rows: Iterable[dict]) -> dict[int, ImageMetadata]:
        acc: dict[int, ImageMetadata] = {}
        ordered: dict[tuple[int, str], list[tuple[Any, Any]]] = defaultdict(list)
        for r in rows:
            occ_id = r["image_occurrence_id"]
            name = r["feature_name"]
            value = (r.get("value_as_number")
                     if r.get("value_as_number") is not None
                     else r.get("value_as_concept_name") or r.get("value_source_value"))
            meta = acc.setdefault(occ_id, ImageMetadata(occ_id))
            meta.concept_ids[name] = r.get("measurement_concept_id")
            meta.raw_rows.append(dict(r))
            ordered[(occ_id, name)].append((r.get("image_feature_value_order"), value))
        for (occ_id, name), pairs in ordered.items():
            if len(pairs) == 1 and pairs[0][0] in (None, 0, 1):
                acc[occ_id].features[name] = pairs[0][1]
            else:
                pairs.sort(key=lambda p: (p[0] is None, p[0]))
                acc[occ_id].features[name] = [v for _, v in pairs]
        return acc

    @staticmethod
    def _to_occurrence(r: dict) -> ImageOccurrenceRef:
        return ImageOccurrenceRef(
            image_occurrence_id=r["image_occurrence_id"],
            person_id=r["person_id"],
            image_occurrence_date=r["image_occurrence_date"],
            modality=r.get("modality") or "OTHER",
            local_path=r["local_path"],
            image_study_UID=r.get("image_study_uid"),
            image_series_UID=r.get("image_series_uid"),
            anatomic_site_concept_id=r.get("anatomic_site_concept_id"),
            procedure_occurrence_id=r.get("procedure_occurrence_id"),
            days_from_index=(r["days_from_index"].days
                             if hasattr(r.get("days_from_index"), "days")
                             else r.get("days_from_index")),
        )

    def _fetch(self, sql: str, params: dict) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        out = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close()
        return out

    def _count_cohort(self) -> int:
        c = self.spec.cohort
        sql = (f"SELECT COUNT(DISTINCT subject_id) FROM {c.results_schema}.{c.cohort_table} "
               "WHERE cohort_definition_id = :cid")
        try:
            return self._fetch(sql, {"cid": c.cohort_definition_id})[0]["count"]
        except Exception:  # dialect / driver variation should not kill the run
            return -1

    def _dump_dry_run(self) -> None:
        (self.workspace / "01_retrieve_occurrence.sql").write_text(
            self.render_occurrence_sql(), encoding="utf-8")
        (self.workspace / "01_retrieve_metadata.sql").write_text(
            self.render_metadata_sql(), encoding="utf-8")
        (self.workspace / "01_retrieve_params.json").write_text(
            json.dumps(self.params(), indent=2, default=str), encoding="utf-8")

    def _dump_result(self, result: RetrievalResult) -> None:
        payload = {
            "spec_id": result.spec_id,
            "n_subjects_in_cohort": result.n_subjects_in_cohort,
            "n_subjects_with_image": result.n_subjects_with_image,
            "attrition": result.attrition,
            "occurrences": [asdict(o) for o in result.occurrences],
        }
        (self.workspace / "01_retriever_result.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
