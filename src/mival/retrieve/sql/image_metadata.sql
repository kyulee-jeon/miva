-- MI-VAL step (1b): DICOM metadata for the retrieved image_occurrence rows.
--
-- image_feature is a LINK table between image_occurrence and measurement rows
-- carrying DICOM-vocabulary concepts. It does not store values itself, so the
-- join must go all the way through to measurement + concept.
--
-- image_feature_value_order preserves ordering for list-valued attributes
-- (e.g. per-lead attributes on a 12-lead ECG); dropping it silently scrambles
-- lead order, which is the kind of failure a model will not announce.

SELECT f.image_occurrence_id,
       f.image_feature_id,
       f.image_feature_value_order,
       f.image_instance_uid,
       m.measurement_id,
       m.measurement_concept_id,
       c.concept_name                AS feature_name,
       c.concept_code                AS dicom_tag,
       m.value_as_number,
       m.value_as_concept_id,
       vc.concept_name               AS value_as_concept_name,
       m.value_source_value,
       m.unit_concept_id,
       uc.concept_name               AS unit_name
FROM {cdm_schema}.image_feature f
JOIN {cdm_schema}.measurement m
  ON m.measurement_id = f.image_feature_event_id
JOIN {cdm_schema}.concept c
  ON c.concept_id = m.measurement_concept_id
LEFT JOIN {cdm_schema}.concept vc
  ON vc.concept_id = m.value_as_concept_id
LEFT JOIN {cdm_schema}.concept uc
  ON uc.concept_id = m.unit_concept_id
WHERE f.image_occurrence_id = ANY(:image_occurrence_ids)
ORDER BY f.image_occurrence_id,
         c.concept_name,
         f.image_feature_value_order NULLS FIRST;
