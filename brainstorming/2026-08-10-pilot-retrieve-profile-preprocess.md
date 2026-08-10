# Pilot 01 — Retrieve / Profile / Preprocess 표준화 범위 탐색

**일시:** 2026-08-10
**대상:** MIMIC MI-CDM (`s3://dryou-workspace/Datasets/MIMIC-IV_CDM/Extension/`)
**목적:** 어떤 표준화 모듈이 필요한지, 어디까지 표준화하고 어디까지 자유도를 줄지 실측으로 가늠

---

## 0. 요약

MI-CDM의 `image_occurrence` / `image_feature` / `measurement` 만으로 ECG 획득 파라미터를
전수 조회하고, DICOM 원본과 대조했다. 결과:

| 질문 | 답 |
|---|---|
| MI-CDM이 DICOM 메타데이터를 충실히 재현하는가 | **예 — 4,800건 값 비교에서 불일치 0** |
| 채널 순서를 MI-CDM만으로 복원할 수 있는가 | **예 — 120/120 정확** |
| 파일을 열지 않고 이질성을 찾을 수 있는가 | **예 — 796,617건 ECG 전수, 85초** |
| MI-CDM만으로 부족한 것이 있는가 | **있음 — 획득 시각(time)이 없어 26.2%가 선택 불능** |

**핵심 발견:** 12-lead ECG 79만 건에서 다른 모든 획득 파라미터가 완전히 균질한데
**Filter Low Frequency(고역통과 차단주파수)만 3개 값으로 갈리고, 환자의 30.8%가
서로 다른 필터 설정의 ECG를 동시에 갖는다.** 이 값은 파형 배열에는 나타나지 않고
ST 분절 형태에 직접 영향을 준다 — 즉 STEMI 모델에 대한 교란 변수이며,
MI-CDM 없이는 사실상 관측 불가능하다.

---

## 1. 실측한 MI-CDM 구조

실제 DDL(`Extension/CREATE TABLE *.txt`)과 데이터를 확인한 결과:

```
image_occurrence (796,617 ECG + 205,653 DX + 9,353 CR)
  └ image_feature                        ← 링크 테이블
      ├ image_feature_concept_id         = DICOM 속성 concept (예: 2128002406 = Sampling Frequency)
      ├ image_feature_value_order        = 채널 순서 (1..12), 스터디 단위 값은 NULL
      ├ image_feature_event_field_concept_id = 1147330  (measurement.measurement_id를 가리킴)
      └ image_feature_event_id ─────────→ measurement.measurement_id
                                            ├ value_as_number      ← 정규화된 수치
                                            ├ value_as_concept_id  ← 코드화된 값
                                            └ value_source_value   ← 원본 문자열
```

DICOM 어휘는 `concept_ADD.csv`에 **8,814개** 등재 (DICOM Attributes 5,183 / Value Sets 3,628),
`vocabulary_id = DICOM`, concept_id 대역 `2128######`.
파형 관련 003A 그룹은 **63개 concept 전부** 등재돼 있다.

### ECG 1건 = feature 68행 (완전 고정)

| 구분 | 개수 | 속성 |
|---|---|---|
| 채널별 (`value_order` 1–12) | 5 × 12 = 60 | Sampling Frequency, Channel Label, Channel Sensitivity, Channel Sensitivity Units, Channel Baseline |
| 스터디 단위 (`value_order` NULL) | 8 | Number of Waveform Channels/Samples, Waveform Bits Allocated/Stored, Filter Low/High Frequency, Series Number, Instance Number |

300건 전수에서 min=max=68. 구조가 완전히 규칙적이라 **스키마 검증이 가능하다**
(68이 아니면 ETL 결손으로 거부).

---

## 2. 검증 ①: MI-CDM ↔ DICOM 충실도 — 불일치 0

120개 DICOM 파일을 pydicom으로 직접 열어 MI-CDM 값과 대조:

```
ChannelSensitivity        1,440 matched      NumberOfWaveformSamples     120 matched
ChannelBaseline           1,440 matched      WaveformBitsStored          120 matched
SamplingFrequency           120 matched      WaveformBitsAllocated       120 matched
NumberOfWaveformChannels    120 matched
mismatch: 0
```

주의: Filter Low/High Frequency는 **multiplex group 레벨**에 있고
Channel Sensitivity/Baseline은 **channel 레벨**에 있다. MI-CDM은 이 계층을
`value_order`의 NULL 여부로 정확히 보존했다.

### 채널 순서 — `image_feature_value_order`가 정확히 복원

120/120 완전 일치. 그런데 실제 순서가 관례와 다르다:

```
I | II | III | aVR | aVF | aVL | V1 | V2 | V3 | V4 | V5 | V6
                     ^^^^^^^^^  aVL/aVF가 통상 순서와 뒤바뀜
```

표준 순서(aVR, aVL, aVF)를 가정하고 배열을 그대로 넣는 모델은 **aVL과 aVF가
조용히 뒤바뀐다.** 오류도 경고도 없다. 이것이 `value_order`를 버리면 안 되는 이유다.

MI-CDM은 라벨을 표준 concept(`I`, `aVR`)로 정규화해 저장했고,
원본 파일은 `ChannelSourceSequence`의 CodeMeaning(`Lead I`, `aVR, augmented voltage, right`)을
갖는다. **MI-CDM 쪽이 기관 간 비교에 더 안전하다.**

---

## 3. 검증 ②: 전수 이질성 스캔 (796,617건, 85초)

`measurement_ADD.csv` 1회 순회로 모든 DICOM 속성의 값 분포를 얻었다.
**DICOM 파일은 한 개도 열지 않았다.**

### ECG — 하나를 뺀 전부가 완전 균질

| 속성 | distinct | 값 |
|---|---|---|
| Sampling Frequency | 1 | 500.0 Hz (100%) |
| Channel Sensitivity | 1 | 0.005 mV/LSB (100%) |
| Channel Sensitivity Units | 1 | millivolt (100%) |
| Channel Baseline | 1 | 0.0 (100%) |
| Number of Waveform Channels | 1 | 12 (100%) |
| Number of Waveform Samples | 1 | 5000 (100%) |
| Waveform Bits Allocated/Stored | 1 | 16 (100%) |
| Filter High Frequency | 1 | 150.0 Hz (100%) |
| **Filter Low Frequency** | **3** | **0.005 (79.71%) / 0.0005 (13.85%) / 0.05 (6.44%)** |

### Filter Low Frequency — 유일한 이질성이자 가장 위험한 변수

- **100배 범위** (0.0005 ~ 0.05 Hz)
- 소수 설정 ECG **161,671건 (20.3%)**
- 연도별 분포 **평탄** (~80/14/6이 80여 년 내내 유지) → 시대 효과 아님
- **환자 158,586명 중 48,827명(30.8%)이 서로 다른 필터 설정의 ECG를 함께 가짐**
  → 환자 단위 공변량으로 보정 불가능. **acquisition 단위 층화만이 유효**

임상적 의미: 고역통과 차단주파수는 baseline wander 제거 강도를 정한다.
차단주파수가 높을수록 **ST 분절이 왜곡**되며, 이 때문에 진단용 ECG는 ≤0.05 Hz를 권고한다.
STEMI 판정은 ST 상승 형태 자체를 읽는 과제이므로 이 변수는 결과에 직접 연결된다.

> 이것이 overview 문서 §5-③ "획득 파라미터 기준 층화"의 **실증 사례**다.
> 파형 배열에는 안 보이고, 논문에는 보고된 적이 없으며, MI-CDM에는 조회 가능한
> 표준 concept으로 들어 있다.

### 덤: CXR 355,347건도 같은 방식으로 조회된다

MI-CDM에 MIMIC-CXR도 ETL돼 있다. ECG와 달리 **모든 축이 이질적**이다:

| 속성 | distinct | 주요 값 |
|---|---|---|
| KVP | 121 (문자열) | 120 (35.7%) / 90 (24.0%) / 110 (19.9%) |
| Detector Type | 3 | DIRECT (81.4%) / SCINTILLATOR (18.7%) |
| View Position | 15 | AP (39.3%) / PA (25.4%) / LATERAL (21.8%) |
| Bits Stored | 6 | 12 (79.5%) / 14 (17.8%) / 10 (2.6%) |
| **Photometric Interpretation** | 2 | MONOCHROME2 (95.8%) / **MONOCHROME1 (4.2%)** |
| Exposure Control Mode | 2 | AUTOMATIC (99.96%) / MANUAL |

MONOCHROME1 4.2%는 **흑백이 반전된 영상**이다. 반전 처리를 안 하면 그 4.2%는
모델 입장에서 네거티브 이미지다. ECG의 Filter Low Frequency와 정확히 같은 구조의 함정이며,
같은 쿼리 한 줄로 잡힌다.

---

## 4. 검증 ③: MI-CDM만으로 **안 되는** 것 — 획득 시각

`image_occurrence`에는 `image_occurrence_date`(date)만 있고 시각이 없다.
`measurement_datetime`도 비어 있다. 반면 DICOM 파일에는 있다:

```
(0008,002A) Acquisition DateTime = 21800723084400   ← 초 단위
```

정량화:

```
person-day 중 ECG 2건 이상: 91,768 / 679,380 (13.5%)
그런 person-day에 속한 ECG: 209,005건 (전체의 26.2%)
한 person-day 최대 ECG 수: 22건
```

**`configs/stemi_ecg.yaml`의 `selection: nearest_to_index`는 ECG의 26.2%에 대해
MI-CDM만으로는 판정 불가능하다.** 세 가지 선택지밖에 없다:

1. ETL에 `image_occurrence_datetime` 추가 (권장 — 근본 해결)
2. RetrievalSpec에 결정론적 tie-break를 **명시** (예: `image_occurrence_id` 최소값)
3. 모호한 person-day를 **탈락시키고 attrition 표에 보고**

지금처럼 침묵하는 것이 제일 나쁘다. 사이트마다 다르게 깨지고, 재현이 안 된다.

---

## 5. 검증 ④: ETL / 코드 결함 3건

### (a) `value_source_value`는 수치 비교에 쓰면 안 된다

```
KVP:  "120"  과  "120.000000"  이 서로 다른 문자열로 저장 (121 distinct)
      → value_as_number 는 둘 다 120.0 으로 정규화 (정상)
```
**Profile/Preprocess는 반드시 `value_as_number`를 쓴다.** `value_source_value`는 감사용.

### (b) 다중값(DS multi-value) 속성은 성분이 유실됨

```
Pixel Spacing (0028,0030)
  value_source_value = " 0.139]"     ← 리스트 직렬화가 깨진 채 저장
  value_as_number    = 0.139         ← 두 성분(행/열) 중 하나만 남음
```
MIMIC-CXR은 행/열 간격이 대개 같아 실害는 작지만, 구조적으로는 정보 손실이다.
비등방 픽셀 데이터가 오면 조용히 틀린다. ETL 이슈로 등록 필요.

### (c) repo SQL이 존재하지 않는 컬럼을 참조

`src/mival/retrieve/sql/cohort_image_occurrence.sql` L32, L45:

```sql
io.modality_source_value      -- ❌ 이 컬럼은 image_occurrence에 없다
```

실제 DDL은 `modality_concept_id bigint`. 실측 값:

| modality_concept_id | 의미 | 건수 |
|---|---|---|
| 4145308 | ECG (표준 OMOP) | 796,617 |
| 2128009197 | Digital radiography (DICOM `DX`) | 205,653 |
| 2128009189 | Computed radiography (DICOM `CR`) | 9,353 |

실제 DB에 붙이는 순간 실패한다. `config`의 `modality: ECG`도 concept_id 기반으로 바꿔야 한다.

---

## 6. 다음 파일럿 실험 (제안)

각 실험은 **결정 하나**를 해소하도록 설계했다.

### Retrieve

| # | 실험 | 해소할 결정 |
|---|---|---|
| R1 | 동일 코호트에 tie-break 3안(최소 id / 전부 유지 / 탈락)을 적용해 코호트 구성 변화 측정 | 모호 26.2%를 어떻게 처리할지 |
| R2 | `metadata_filters`를 retrieve 단계 vs preprocess 단계에 각각 걸고 attrition 비교 | 필터링 책임을 어느 모듈에 둘지 |
| R3 | ECG/DX/CR 3 modality를 같은 SQL로 조회 | 쿼리를 modality 불문 단일 표준으로 유지 가능한지 |

### Profile

| # | 실험 | 해소할 결정 |
|---|---|---|
| P1 | 68행 스키마 검증기 — feature 수/`value_order` 커버리지 이상 탐지 | ETL 결손을 자동 거부할 수 있는지 |
| P2 | 이질성 플래그 규칙 튜닝: "distinct ≥ 2 & 소수 비율 ≥ 1%"를 ECG·CXR에 적용 | 임계값을 고정할지 사용자에게 열지 |
| P3 | 코호트 vs 전체 모집단의 획득 파라미터 분포 비교 (예: STEMI 코호트의 필터 분포가 전체와 다른가) | Table 1에 획득 파라미터를 넣을지 |

**P3가 가장 중요하다.** STEMI 코호트에서 필터 분포가 전체(80/14/6)와 다르면
선택 편향이 있다는 뜻이고, 그 자체가 논문에 실을 결과다.

### Preprocess

| # | 실험 | 해소할 결정 |
|---|---|---|
| X1 | 동일 ECG에 0.0005 / 0.005 / 0.05 Hz 고역통과를 **재적용**해 ST 분절 변화량 측정 | 필터 차이가 실제로 얼마나 큰지 (효과 크기) |
| X2 | ProphECG-STEMI를 필터 계층별로 평가 → AUROC 차이 | 층화가 필요한지 (핵심 가설 검정) |
| X3 | 레시피에 `requires_metadata: [Filter Low Frequency]`를 선언하고 값 결측 시 거부 동작 확인 | 메타데이터 인지형 전처리가 실제로 작동하는지 |
| X4 | 채널 순서를 `value_order` 무시하고 파일 순서대로 넣었을 때 성능 변화 | aVL/aVF 뒤바뀜의 실제 영향 |

**X2가 프레임워크의 존재 증명이다.** 필터 계층별 AUROC 차이가 유의하면,
"단일 AUROC 보고는 불충분하다"는 주장이 MIMIC 실측으로 증명된다.

**X1을 X2보다 먼저 한다.** 효과 크기가 애초에 무시할 수준이면 X2는 의미가 없다.

---

## 7. 표준화 vs 자유도 — 실측 기반 제안

이번 파일럿이 시사하는 경계선.

### 강제 (사용자가 못 바꿈)

| 항목 | 근거 |
|---|---|
| 채널 순서는 `image_feature_value_order`로 복원 | aVL/aVF 뒤바뀜이 조용히 발생 |
| 수치는 `value_as_number` 사용 | `value_source_value`는 `120` ≠ `120.000000` |
| 레시피 op은 `requires_metadata` 선언 필수, 결측 시 기본값 금지·레코드 거부 | overview §5-① |
| 모든 획득 파라미터의 관측 분포를 RunManifest에 기록 | 재현성의 최소 단위 |
| 탈락 사유별 attrition 표 산출 | 26.2% 같은 손실이 숨으면 안 됨 |

### 선언 강제, 값은 자유 (반드시 고르되 무엇을 고를지는 연구자 몫)

| 항목 | 이유 |
|---|---|
| 동일 person-day tie-break 규칙 | 정답이 없다. 단 **침묵은 금지** |
| 층화 기준 파라미터 목록 | 과제마다 다르다 (ECG=필터, CXR=Photometric/Detector) |
| 이질성 플래그 임계값 | 코호트 크기에 따라 다르다 |
| 소수 설정 처리 (제외 / 층화 / 보정) | 세 가지 다 정당하다. 선택을 기록만 하면 된다 |

### 완전 자유

- 전처리 op의 조합·순서 (레시피 해시로 고정되기만 하면 됨)
- 모델 로딩 경로, 하이퍼파라미터
- 평가지표 선택 (선택 *근거* 기록은 강제)

> 원칙: **"무엇을 골랐는지 기록되지 않는 선택"만 금지한다.**
> 자유도를 좁히는 게 아니라, 선택이 명세에 남게 만드는 것이 프레임워크의 일이다.

---

## 8. 재현

```
brainstorming/pilots/pilot1_inventory.py    속성 인벤토리 (image_feature × measurement)
brainstorming/pilots/pilot2b_fidelity.py    MI-CDM ↔ DICOM 대조 + 채널 순서 검증
```

전수 분포 (85초, 6.7GB 1-pass):

```bash
aws s3 cp s3://dryou-workspace/Datasets/MIMIC-IV_CDM/Extension/measurement_ADD.csv - \
  | awk -F',' 'NR>1{c[$3"\t"$NF]++} END{for(k in c) print c[k]"\t"k}'
```

환경: `/home/ubuntu/dryou_mount` 에 s3fs로 마운트돼 있어 `image_occurrence.local_path`가
그대로 해석된다. pydicom 3.0.1.
