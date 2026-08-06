---
name: mival-ontology
description: MI-VAL의 온톨로지 객체 모델과 MI-CDM 테이블 매핑 참조. MI-VAL 어느 단계에서든 객체 정의, 객체 간 링크, image_occurrence/image_feature/measurement 조인 경로, 워크스페이스 파일 규약, RunManifest 구조가 필요하면 반드시 이 스킬을 읽을 것. "MI-VAL 객체가 뭐야", "image_feature 어떻게 조인해", "이 단계 산출물 어디에 저장해", "매니페스트에 뭐가 들어가야 해", "온톨로지 확인" 같은 요청에서 사용한다. 개별 단계의 실행 방법은 각 단계 스킬을 함께 읽을 것.
---

# MI-VAL Ontology

MI-VAL의 모든 단계는 느슨한 dict가 아니라 **타입이 있는 객체**를 주고받는다. 이 스킬은 그 객체들과, 객체가 어떤 MI-CDM 테이블에 뿌리를 두는지를 정의한다.

왜 이렇게 하는가: 단계 사이에 흐르는 것이 표준화되어 있어야 단계를 독립적으로 교체할 수 있고, 에이전트가 다음 행동을 판단하는 데 필요한 정보가 전부 객체 위에 있어야 파이프라인이 agentic하게 동작한다. 무엇보다, 재현성은 "무엇이 흘렀는지"를 기록할 수 있을 때만 성립한다.

## 객체 목록

| 객체 | 근거 테이블 | 핵심 필드 |
|------|------------|----------|
| `CohortSpec` | `<results>.cohort` | cohort_definition_id, results_schema, atlas_definition_json |
| `CohortSubject` | person, condition_occurrence, measurement | person_id, index_date, outcome |
| `ImageOccurrenceRef` | `image_occurrence` | image_occurrence_id, local_path, modality, days_from_index |
| `ImageMetadata` | `image_feature` → `measurement` → `concept` | features(dict), concept_ids, raw_rows |
| `ImageAsset` | `image_occurrence.local_path` + 사이트 루트 | path, exists, checksum |
| `RetrievalSpec` | — (선언) | window, selection, metadata_filters |
| `RetrievalResult` | — | occurrences, assets, attrition |
| `PreprocessRecipe` | — (선언) | ops, recipe_hash |
| `PreprocessedSample` | — | array_path, shape, applied_ops, warnings |
| `ModelCard` | — (선언) | source_uri, checksum, input_spec, framework |
| `EvaluationSpec` | — (선언) | primary_metrics, operating_point, subgroup_by, seed |
| `EvaluationResult` | — | overall, subgroups, calibration |
| `RunManifest` | — | 위 전부의 고정된 참조 + 환경 |

`mival ontology`를 실행하면 링크까지 포함한 전체 그래프가 출력된다.

## 객체의 3요소

모든 객체는 **정체성(identity)**, **출처(provenance)**, **내용(payload)** 을 갖는다. 앞의 둘이 재현성을 만들고, 마지막이 유용성을 만든다. 선언형 객체(Spec, Recipe, Card)는 내용의 해시가 곧 정체성이다 — `RetrievalSpec`의 윈도우를 하루 바꾸면 `object_id`가 바뀌고, 그것이 다른 연구라는 뜻이다.

## MI-CDM 조인 경로 (가장 자주 틀리는 부분)

```
image_occurrence
  └─ image_feature            ← 링크 테이블. 값이 여기 있지 않다.
       └─ measurement          ← DICOM 어휘 concept의 값이 여기 있다
            └─ concept          ← concept_name = DICOM 속성명
```

**세 가지 함정:**

1. `image_feature`에서 멈추면 메타데이터가 통째로 빈다. 파이프라인은 에러 없이 계속 돈다.
2. `image_feature_value_order`를 버리면 리스트형 값(리드 순서 등)이 섞인다. 순서가 섞인 12-lead는 예외를 던지지 않고 그냥 틀린 예측을 낸다.
3. `image_occurrence.procedure_occurrence_id`는 이 구현에서 NOT NULL 제약이 제거되어 있다. 연결된 레코드만 적재되므로, procedure 조인을 INNER로 걸면 조용히 표본이 줄어든다.

## 워크스페이스 파일 규약

중간 산출물은 `_workspace/`에, `{단계번호}_{에이전트}_{산출물}.{확장자}` 규칙으로 쓴다. 중간 파일은 삭제하지 않는다 — 사후 검증과 감사 추적이 이 프레임워크의 목적이다.

```
_workspace/
  00_dry_run.json
  01_retrieve_occurrence.sql      01_retrieve_metadata.sql
  01_retrieve_params.json         01_retriever_result.json
  02_profiler_report.json         02_data_spec.html
  03_recipe.json                  03_arrays/*.npy
  03_preprocess_failures.json
  04_model_checks.json
  05_eval_<model>.json            05_predictions_<model>.csv
  05_results.html
  99_qa_report.json
  run_manifest.json
```

재실행 시 기존 `_workspace/`는 삭제하지 말고 `_workspace_prev/`로 옮긴다.

## RunManifest — 이식성 계약

다음 다섯 가지가 전부 고정되어야 다른 기관에서 같은 연구를 재실행할 수 있다.

1. **코호트** — id가 아니라 ATLAS 정의 JSON 전문. id는 기관마다 다른 population을 가리킨다.
2. **검색 명세** — 윈도우 + 선택 규칙 + 메타데이터 필터.
3. **레시피** — 해시로.
4. **모델** — 체크섬 + 프레임워크 핀으로.
5. **지표 명세** — 임계값 규칙과 시드를 포함해서.

여기에 실행 환경(python, 패키지 버전, git commit)이 붙는다.

`local_path_root`처럼 사이트 로컬인 값은 공유 매니페스트에 남기지 않는다 — 받는 기관이 덮어써야 하는 값이다. `mival verify -m run_manifest.json`이 이 전부를 검사한다.

## 새 객체를 추가할 때

`src/mival/ontology/objects.py`에 dataclass를 추가하고, `registry.py`의 `OBJECT_TYPES`와 `LINKS`에 등록한다. 근거 테이블이 있으면 `TABLE_GROUNDING`에도 넣는다. 등록하지 않은 객체는 `mival ontology`에 나타나지 않고, 그러면 다른 에이전트가 그 객체의 존재를 알 방법이 없다.
