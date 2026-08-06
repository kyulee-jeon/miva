# MI-VAL

**Standardized, reproducible AI validation on the OMOP Medical Imaging CDM.**

Cohort definition stays in ATLAS. MI-VAL starts where ATLAS stops: cohort →
DICOM → data spec → preprocessing → model → metrics, with every decision on the
path recorded as a typed object rather than as analyst habit.

```
ATLAS cohort ──▶ (1) retrieve ──▶ (2) profile
                      │
                      ├──▶ (3) preprocess ──┐
                      │                      ├──▶ (5) evaluate ──▶ report + manifest
                      └──▶ (4) models  ──────┘
```

## Why

Promising medical AI models exist in papers and on leaderboards, and then do not
reproduce on real clinical data. The bottleneck is usually not the algorithm —
it is the infrastructure around it. Three things go unrecorded almost everywhere:

- which patients' which images were used (cohort → image selection rule)
- how those images were processed (preprocessing pipeline)
- against what criteria, in which subgroups, performance was measured

Acquisition parameters — ECG sampling rate and lead set, chest-radiograph
exposure settings — move model performance as much as demographics do, and are
the variable most often left out of a validation report. MI-CDM makes them
queryable; MI-VAL makes not reporting them the harder option.

## Install

```bash
pip install -e .                 # core (pyyaml only)
pip install -e ".[db,dicom,signal,torch]"   # full
```

## Quick start

```bash
mival ontology                          # the object graph
mival ops                               # registered preprocessing ops
mival sql     -c configs/stemi_ecg.yaml # render the standardized query only
mival dry-run -c configs/stemi_ecg.yaml # full protocol check, no DB, no PHI
mival verify  -m _workspace/run_manifest.json   # is this run portable?
```

`mival dry-run` is the one to start with. It renders the SQL, statically
validates the recipe against the metadata the dataset actually carries, checks
every model card for portability, resolves the metric spec, and writes a run
manifest — all before touching a database.

## The five steps

| Step | Module | What it owns |
|---|---|---|
| (1) | `mival.retrieve` | Cohort + index-date window → `image_occurrence` → DICOM on disk, with a CONSORT-style attrition trace |
| (2) | `mival.profile` | Acquisition-metadata distributions + clinical characteristics of the *imaged* subset; self-contained HTML report |
| (3) | `mival.preprocess` | Declarative, metadata-aware recipes; hashed and portable |
| (4) | `mival.models` | Model cards (RSNA ATLAS `model.json` superset), weight resolution from local/GitHub/HF/Zenodo, pre-inference input-contract checks |
| (5) | `mival.evaluate` | Metric choice with recorded rationale, bootstrap CIs, subgroup stratification, cross-model comparison |

## What makes it different

**Metadata-aware preprocessing.** Each op declares the MI-CDM attributes it
reads. `resample` reads the source sampling frequency from the CDM; if the
attribute is missing it refuses the record instead of assuming a default.
Assumed defaults produce garbage without an error, and the model will score that
garbage confidently.

**Input contracts checked before inference.** A model card's declared input
(sampling rate, lead order, units) is compared against the cohort's observed
MI-CDM metadata. A large share of "the model didn't transfer" findings are
really "nobody checked the input contract".

**Stratification by acquisition parameters.** Performance is never one number.
A model that is excellent at 500 Hz and a coin flip at 250 Hz reports a single
AUROC and hides the failure; the stratified table does not.

**Portability as a command, not an opinion.** `mival verify` checks that the
manifest pins the cohort (ATLAS export), the recipe (hash), the models
(checksum), the metric spec, and the environment. Site-local values are kept out
of the shared manifest by design.

## Repository layout

```
.claude/agents/     6 step agents + a cross-cutting reproducibility QA agent
.claude/skills/     ontology reference, 5 step skills, 1 orchestrator
src/mival/          the package
  ontology/         typed objects + the link graph
  retrieve/sql/     standardized SQL, readable and runnable by hand
schemas/            dataset / model card / recipe JSON schemas
configs/            example study protocol (STEMI vs ECGFounder)
docs/               architecture notes and the plan overview
tests/              smoke tests — no DB, no DICOM, no GPU required
```

## Agent harness

The repo ships an agent harness. `mival-validation-run` is the orchestrator;
each of the five steps has a specialist agent and a skill, plus a
`reproducibility-qa` agent that checks the boundaries *between* steps
incrementally rather than once at the end. See `CLAUDE.md` for the trigger rule.

## References

- Jeon K, Park WY, Kahn CE Jr, et al. *Advancing Medical Imaging Research Through Standardization.* Investigative Radiology. 2025;60(1).
- Maier-Hein L, Reinke A, Godau P, et al. *Metrics reloaded: recommendations for image analysis validation.* Nature Methods. 2024;21:195–212.
- RSNA ATLAS — `model.json` / `dataset.json` specification. <https://github.com/RSNA/ATLAS>
- Isensee F, et al. nnU-Net — self-configuring pipeline design. <https://github.com/MIC-DKFZ/nnUNet>
- ACR–SIIM Practice Parameter for Imaging Artificial Intelligence.
- *The AI Nobody Sees: Why the future of medical AI depends on more than algorithms.* The Vasty Deep | Radiology: AI, 2026.

## License

Apache-2.0
