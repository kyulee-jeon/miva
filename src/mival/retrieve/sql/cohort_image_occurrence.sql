-- MI-VAL step (1): cohort -> image_occurrence -> local_path
-- Dialect: PostgreSQL. Parameters are :named (psycopg / SQLAlchemy style).
--
-- Contract
--   :results_schema     schema holding the ATLAS-generated cohort table
--   :cohort_table       usually 'cohort'
--   :cdm_schema         schema holding OMOP CDM + MI-CDM extension tables
--   :cohort_definition_id
--   :window_start_days  e.g. -1  (relative to cohort_start_date)
--   :window_end_days    e.g.  0
--   :modality           e.g. 'ECG'
--
-- Selection ('first' | 'last' | 'nearest_to_index' | 'all') is applied by the
-- ORDER BY inside the window function; the Python layer substitutes it so the
-- rule is recorded on the RetrievalSpec rather than hand-edited into SQL.

WITH cohort AS (
    SELECT c.subject_id                      AS person_id,
           c.cohort_start_date               AS index_date,
           c.cohort_start_date,
           c.cohort_end_date
    FROM {results_schema}.{cohort_table} c
    WHERE c.cohort_definition_id = :cohort_definition_id
),
candidate AS (
    SELECT co.person_id,
           co.index_date,
           co.cohort_start_date,
           co.cohort_end_date,
           io.image_occurrence_id,
           io.image_occurrence_date,
           io.modality_source_value                       AS modality,
           io.local_path,
           io.image_study_uid,
           io.image_series_uid,
           io.anatomic_site_concept_id,
           io.procedure_occurrence_id,
           (io.image_occurrence_date - co.index_date)     AS days_from_index
    FROM cohort co
    JOIN {cdm_schema}.image_occurrence io
      ON io.person_id = co.person_id
    WHERE io.image_occurrence_date
              BETWEEN co.index_date + (:window_start_days * INTERVAL '1 day')
                  AND co.index_date + (:window_end_days   * INTERVAL '1 day')
      AND (:modality IS NULL OR io.modality_source_value = :modality)
      AND (io.local_path IS NOT NULL AND io.local_path <> '')
),
ranked AS (
    SELECT candidate.*,
           ROW_NUMBER() OVER (
               PARTITION BY person_id
               ORDER BY {selection_order}
           ) AS rn
    FROM candidate
)
SELECT person_id,
       index_date,
       cohort_start_date,
       cohort_end_date,
       image_occurrence_id,
       image_occurrence_date,
       modality,
       local_path,
       image_study_uid,
       image_series_uid,
       anatomic_site_concept_id,
       procedure_occurrence_id,
       days_from_index
FROM ranked
WHERE rn <= :max_per_subject
ORDER BY person_id, rn;
