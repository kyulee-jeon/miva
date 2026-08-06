---
name: micdm-cohort-retrieval
description: MI-VAL 1단계 — ATLAS 코호트에서 MI-CDM image_occurrence를 거쳐 DICOM 파일까지 끌어오는 표준화된 검색. "코호트 영상 가져와", "index date 기준 ECG 뽑아", "image_occurrence 쿼리", "local_path로 DICOM 찾아", "검색 조건 바꿔서 다시", "윈도우 조정", "attrition 확인", "retrieval spec 수정" 같은 요청에서 반드시 사용할 것. 코호트 정의 자체(ATLAS 작업)나 메타데이터 분포 확인(2단계 스킬)에는 사용하지 않는다.
---

# MI-CDM Cohort Image Retrieval

ATLAS가 만든 코호트를, index date 기준 규칙에 따라 실제 DICOM 파일 목록으로 바꾼다.

**경계:** 코호트 정의는 하지 않는다. ATLAS가 그 일을 하고, MI-VAL이 재구현하면 진실이 둘이 된다. `cohort_definition_id`로만 받는다.

## 실행

```bash
mival sql     -c configs/study.yaml     # 쿼리만 렌더 (DB 불필요)
mival profile -c configs/study.yaml     # 1+2단계 실행
```

프로그램적으로:

```python
from mival.ontology.objects import CohortSpec, RetrievalSpec
from mival.retrieve.resolver import CohortImageResolver

spec = RetrievalSpec(
    cohort=CohortSpec(cohort_definition_id=1042, name="AMI", results_schema="results"),
    modality="ECG", window_start_days=-1, window_end_days=0,
    selection="nearest_to_index", max_per_subject=1,
    metadata_filters={"Sampling Frequency": [500]},
    local_path_root="/data/mimic-micdm/ecg",
)
result = CohortImageResolver(spec, connection=conn).resolve()
```

`connection=None`이면 dry run — SQL과 파라미터만 `_workspace/`에 쓴다. PHI를 건드리기 전 프로토콜 리뷰용 정상 경로다.

## 선택 규칙을 명시하는 이유

"index date 근처 ECG"는 세 개의 결정이다.

| 필드 | 질문 | 예 |
|------|------|-----|
| `window_start_days` | 언제부터 | -1 (24시간 전) |
| `window_end_days` | 언제까지 | 0 (index 당일) |
| `selection` | 여러 개면 무엇을 | `nearest_to_index` |
| `max_per_subject` | 몇 개까지 | 1 |

이 셋을 SQL에 손으로 박아 넣는 순간 재현성이 끝난다. 필드로 두면 spec 해시가 바뀌고, 해시가 바뀌면 다른 연구라는 것이 자동으로 드러난다.

`selection` 옵션: `first` / `last` / `nearest_to_index` / `all`. `all`을 쓰면 환자당 여러 영상이 들어오므로, 평가 단계에서 환자 단위 집계 규칙을 별도로 정해야 한다 — 그러지 않으면 영상을 많이 찍은 환자가 지표를 지배한다.

## 표준화된 쿼리 사용법

쿼리는 `src/mival/retrieve/sql/`에 파일로 있다. ORM으로 숨기지 않는 이유는, 리뷰어가 직접 읽고 돌려볼 수 있어야 "표준화된 쿼리"라는 말이 성립하기 때문이다.

- `cohort_image_occurrence.sql` — 코호트 → 윈도우 → 영상 → local_path
- `image_metadata.sql` — image_feature → measurement → concept
- `cohort_characteristics.sql` — 영상 보유 환자의 임상 특성 (2단계용)

PostgreSQL 방언이며 `:named` 파라미터를 쓴다. 다른 방언으로 옮길 때는 날짜 산술(`+ INTERVAL`)과 `ANY(:array)`가 바뀌는 지점이다.

## 메타데이터 필터: 조화 vs 제한

`metadata_filters={"Sampling Frequency": [500]}`은 500Hz만 남긴다. 대안은 전부 가져와서 3단계에서 리샘플링하는 것이다.

둘 중 어느 쪽도 기본 정답이 아니다.
- **제한**하면 모델이 학습된 조건에 맞춰 검증하지만, 표본이 줄고 일반화 주장이 좁아진다.
- **조화**하면 표본은 크지만, 리샘플링 자체가 성능에 미치는 영향이 결과에 섞인다.

무엇을 택하든 attrition 표와 리포트에 그 결정과 이유를 남긴다.

## attrition 표

```
코호트 N명
  → 윈도우 내 영상 보유 M명       (감소: 영상 미보유)
  → 메타데이터 필터 통과 K건       (감소: 조건 불일치)
  → local 파일 존재 J건            (감소: 파일 부재)
```

각 단계의 감소량이 없으면 selection bias를 논할 수 없다. 최종 리포트 상단에 반드시 싣는다.

## 자주 겪는 문제

| 증상 | 원인 | 대응 |
|------|------|------|
| 커버리지가 이상하게 낮음 | `modality_source_value`가 소스마다 다르게 적힘 (`ECG` vs `ECG12` vs 빈 값) | 필터 없이 한 번 돌려 실제 값 분포를 본다 |
| 메타데이터가 전부 빔 | `image_feature`에서 멈추고 `measurement`까지 안 감 | 조인 경로 확인. `mival-ontology` 스킬의 조인 다이어그램 참조 |
| 리드 순서가 이상함 | `image_feature_value_order` 무시 | 정렬 키에 포함되어 있는지 확인 |
| 파일이 다 없다고 나옴 | `local_path_root` 미설정 또는 오설정 | 루트 문제와 실제 부재를 구분해 보고 |
| 표본이 예상보다 적음 | `procedure_occurrence_id` INNER 조인 | 이 구현에서는 해당 FK의 NOT NULL이 제거되어 있다. LEFT로 |

## 재실행

`_workspace/01_retriever_result.json`의 `spec_id`가 현재 spec의 `object_id`와 같으면 재실행하지 않는다. 다르면 이전 워크스페이스를 `_workspace_prev/`로 옮기고, 무엇이 바뀌었는지(윈도우? 선택 규칙? 필터?) 한 줄로 보고한 뒤 실행한다.
