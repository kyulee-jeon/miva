# MI-VAL

**목표:** MI-CDM(OMOP CDM + 영상/ECG DICOM) 위에서 서로 다른 AI 모델을 같은 코호트·같은 전처리·같은 지표 명세로 검증하고, 그 실행을 다른 기관에 그대로 이식할 수 있게 한다.

## 하네스: MI-CDM Trustworthy AI Validation

**트리거:** MI-CDM 기반 모델 검증 관련 작업 요청 시 `mival-validation-run` 스킬을 사용하라. 단일 단계만 명확히 필요하면 해당 단계 스킬(`micdm-cohort-retrieval`, `micdm-spec-profiling`, `signal-preprocessing-recipe`, `model-card-integration`, `validation-metrics-reporting`)을 직접 써도 된다. 객체 모델이나 MI-CDM 조인 경로가 필요하면 `mival-ontology`를 함께 읽어라. 단순 질문은 직접 응답 가능.

**경계:** 코호트 정의는 ATLAS가 한다. MI-VAL은 ATLAS가 끝나는 지점에서 시작한다.

**변경 이력:**

| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-08-06 | 초기 구성 — 온톨로지 + 5단계 모듈 + 에이전트 6종 + 스킬 7종 | 전체 | - |

## 개발 규칙

- 테스트는 DB·DICOM·numpy·GPU 없이 돌아야 한다: `python -m pytest tests -q`
- 새 전처리 op는 `register_op`로 등록하고 `requires_metadata`를 선언한다. 등록하지 않은 함수는 레시피에서 쓸 수 없다.
- 새 온톨로지 객체는 `registry.py`의 `OBJECT_TYPES`/`LINKS`에도 등록한다. 등록하지 않으면 `mival ontology`에 나타나지 않고, 다른 에이전트가 존재를 알 방법이 없다.
- SQL은 `src/mival/retrieve/sql/*.sql`에 파일로 유지한다. 리뷰어가 직접 읽고 돌려볼 수 있어야 "표준화된 쿼리"다.
- 중간 산출물은 `_workspace/`에 남기고 삭제하지 않는다. 재실행 시 `_workspace_prev/`로 옮긴다.
- 사이트 로컬 값(`local_path_root`, `site`)은 공유 매니페스트에 넣지 않는다.
