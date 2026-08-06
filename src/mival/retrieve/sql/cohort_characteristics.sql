-- MI-VAL step (2a): clinical characteristics of the imaged cohort, as of index.
--
-- Purpose: the "Table 1" for the subset that actually has an image, not the
-- whole ATLAS cohort. Those two populations differ, and the difference is
-- itself a finding worth reporting.
--
-- Concept sets are passed in rather than hard-coded so that the same query
-- serves any phenotype without editing SQL.

WITH imaged AS (
    SELECT DISTINCT person_id, index_date
    FROM {workspace_schema}.{retrieval_table}
    WHERE retrieval_spec_id = :retrieval_spec_id
),
demo AS (
    SELECT i.person_id,
           i.index_date,
           EXTRACT(YEAR FROM AGE(i.index_date, MAKE_DATE(p.year_of_birth,
                                                         COALESCE(p.month_of_birth, 1),
                                                         COALESCE(p.day_of_birth, 1)))) AS age_at_index,
           g.concept_name AS gender,
           r.concept_name AS race,
           e.concept_name AS ethnicity
    FROM imaged i
    JOIN {cdm_schema}.person p ON p.person_id = i.person_id
    LEFT JOIN {cdm_schema}.concept g ON g.concept_id = p.gender_concept_id
    LEFT JOIN {cdm_schema}.concept r ON r.concept_id = p.race_concept_id
    LEFT JOIN {cdm_schema}.concept e ON e.concept_id = p.ethnicity_concept_id
),
conds AS (
    SELECT i.person_id,
           c.concept_name AS condition_name,
           co.condition_concept_id
    FROM imaged i
    JOIN {cdm_schema}.condition_occurrence co
      ON co.person_id = i.person_id
     AND co.condition_start_date <= i.index_date
     AND co.condition_start_date >= i.index_date - (:lookback_days * INTERVAL '1 day')
    JOIN {cdm_schema}.concept c ON c.concept_id = co.condition_concept_id
    WHERE co.condition_concept_id = ANY(:condition_concept_ids)
),
labs AS (
    SELECT i.person_id,
           c.concept_name AS measurement_name,
           m.value_as_number,
           uc.concept_name AS unit,
           ROW_NUMBER() OVER (PARTITION BY i.person_id, m.measurement_concept_id
                              ORDER BY ABS(m.measurement_date - i.index_date)) AS rn
    FROM imaged i
    JOIN {cdm_schema}.measurement m
      ON m.person_id = i.person_id
     AND m.measurement_date BETWEEN i.index_date - (:lab_window_days * INTERVAL '1 day')
                                AND i.index_date + (:lab_window_days * INTERVAL '1 day')
    JOIN {cdm_schema}.concept c ON c.concept_id = m.measurement_concept_id
    LEFT JOIN {cdm_schema}.concept uc ON uc.concept_id = m.unit_concept_id
    WHERE m.measurement_concept_id = ANY(:measurement_concept_ids)
)
SELECT 'demographics' AS block, d.person_id, NULL::text AS name,
       d.age_at_index AS value_num, d.gender AS value_txt, d.race AS extra_1, d.ethnicity AS extra_2
FROM demo d
UNION ALL
SELECT 'condition', c.person_id, c.condition_name, NULL, NULL, NULL, NULL
FROM conds c
UNION ALL
SELECT 'measurement', l.person_id, l.measurement_name, l.value_as_number, l.unit, NULL, NULL
FROM labs l
WHERE l.rn = 1;
