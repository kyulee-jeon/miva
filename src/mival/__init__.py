"""MI-VAL — a standardized, reproducible validation framework on MI-CDM.

Five steps, five object-in/object-out modules:
  (1) retrieve  cohort -> image_occurrence -> DICOM on disk
  (2) profile   acquisition metadata + clinical characteristics
  (3) preprocess declarative, metadata-aware recipes
  (4) models    model cards + loaders + input-contract checks
  (5) evaluate  metric spec, bootstrap CIs, subgroup stratification

Cohort definition stays in ATLAS by design; MI-VAL starts where ATLAS stops.
"""
__version__ = "0.1.0"
