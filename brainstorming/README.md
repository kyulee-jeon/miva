# brainstorming

MI-VAL 설계 논의와 파일럿 실험 기록. 시간순.

| 날짜 | 문서 | 내용 |
|---|---|---|
| 2026-08-06 | [2026-08-06.md](2026-08-06.md) | 킥오프 회의록 — 6단계 파이프라인 확정, 역할 분담, 연구 비전, 데이터 경로 |
| 2026-08-10 | [2026-08-10-pilot-retrieve-profile-preprocess.md](2026-08-10-pilot-retrieve-profile-preprocess.md) | 파일럿 01 — MI-CDM ECG 79만 건 전수 스캔, DICOM 충실도 검증, 표준화/자유도 경계 제안 |

## 파일럿 스크립트

[`pilots/`](pilots/) — 재현용. MI-CDM 원본은 S3에 있고 repo에 포함하지 않는다.

| 스크립트 | 하는 일 |
|---|---|
| [pilots/pilot1_inventory.py](pilots/pilot1_inventory.py) | `image_feature` × `measurement` 조인 → 실제 채워진 DICOM 속성 인벤토리 |
| [pilots/pilot2b_fidelity.py](pilots/pilot2b_fidelity.py) | MI-CDM ↔ DICOM 원본 값 대조 + `image_feature_value_order` 채널 순서 검증 |

실행 전제: `/home/ubuntu/dryou_mount` 에 s3fs 마운트, `concept_ADD.csv` 를 작업 디렉터리에 다운로드,
`pydicom>=3.0`.

## 파일럿 01 요지

- MI-CDM은 DICOM 파형 메타데이터를 **불일치 0**으로 재현한다 (4,800건 값 비교)
- 채널 순서는 `image_feature_value_order`로 **정확히 복원**된다 — 실제 순서가 `aVR|aVF|aVL`로
  관례와 달라, 순서를 무시하면 aVL/aVF가 조용히 뒤바뀐다
- ECG 79만 건에서 **Filter Low Frequency만** 3개 값으로 갈린다 (0.005 / 0.0005 / 0.05 Hz).
  환자의 **30.8%가 서로 다른 필터 설정의 ECG를 함께 가짐** → acquisition 단위 층화만 유효
- MI-CDM에 **획득 시각이 없어** ECG의 26.2%가 `nearest_to_index`로 판정 불가능
- 미해결 결함: `cohort_image_occurrence.sql`이 존재하지 않는 `modality_source_value`를 참조
  (실제는 `modality_concept_id`)
