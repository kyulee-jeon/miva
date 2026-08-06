---
name: signal-preprocessing-recipe
description: MI-VAL 3단계 — ECG/영상의 선언적·메타데이터 인지형 전처리 레시피 작성과 검증. "전처리 파이프라인 만들어", "ECG 리샘플링", "리드 순서 맞춰", "밴드패스 필터", "정규화 방법 바꿔", "레시피 수정", "op 추가", "전처리 표준화", "recipe.yaml 작성", "전처리 실패 원인" 같은 요청에서 반드시 사용할 것. 모델 로딩(4단계)이나 데이터 분포 확인(2단계)에는 사용하지 않는다.
---

# Preprocessing Recipes

전처리를 코드가 아니라 **데이터**로 만든다. 레시피는 등록된 op의 순서 있는 목록이고, 해시가 붙고, YAML로 이동한다.

논문 방법론에 "bandpass 후 z-score"라고 쓰는 대신 `ecg-12lead-500hz-standard@a1b2c3d4`라고 쓸 수 있게 하는 것이 목표다. 앞의 문장은 재현되지 않고, 뒤의 것은 재현된다.

## 레시피 구조

```yaml
name: ecg-12lead-500hz-standard
modality: ECG
version: "1.0.0"
description: >
  리드 순서를 표준 12로 재정렬, 500Hz 리샘플, 0.5-40Hz 밴드패스,
  60Hz 노치, 10초 중앙 크롭, 리드별 z-score.
ops:
  - op: select_leads
    leads: [I, II, III, aVR, aVL, aVF, V1, V2, V3, V4, V5, V6]
  - op: scale_units
    target_unit: mV
  - op: resample
    target_hz: 500
  - op: bandpass
    low_hz: 0.5
    high_hz: 40.0
  - op: notch
    freq_hz: 60.0
  - op: crop_or_pad
    n_samples: 5000
  - op: drop_if_flat
  - op: normalize
    method: zscore_per_lead
```

`loader`, `strict` 같은 실행 설정은 config에는 같이 두되 **해시에는 들어가지 않는다**. DICOM 리더를 바꿨다고 레시피 해시가 바뀌면 해시의 의미가 사라진다.

## 메타데이터 인지형이라는 것

각 op는 자신이 읽을 MI-CDM 속성을 선언한다.

```python
@register_op("resample", requires_metadata=["Sampling Frequency"], modality="ECG")
def resample(x, meta, target_hz=500.0, source_hz=None):
    fs = float(source_hz if source_hz is not None else meta.sampling_frequency)
    ...
```

`source_hz`를 명시하지 않으면 MI-CDM에서 읽는다. MI-CDM에도 없으면 **기본값을 가정하지 않고 그 레코드를 거부한다.** 기본값 가정은 에러 없이 쓰레기를 만들어내는 가장 흔한 경로이고, 모델은 그 쓰레기에 대해서도 자신 있게 틀린 점수를 낸다.

`source_hz=250`처럼 명시적으로 override하면 그 값이 레시피에 기록되고 해시에 들어간다. 추측과 명시의 차이는 기록 여부다.

## 데이터를 만지기 전 정적 검증

```python
from mival.preprocess.recipe import validate_recipe
problems = validate_recipe(recipe, available_metadata={"Sampling Frequency", "Manufacturer"})
```

빈 리스트여야 실행한다. DB도 GPU도 필요 없으므로 프로토콜 리뷰 단계에서 돌릴 수 있다. `mival dry-run`이 이걸 포함한다.

## 조화 vs 층화

250Hz와 500Hz가 섞여 있다. 두 선택지가 있다.

- **조화**: `resample`로 통일. 비교는 쉬워지지만 리샘플링 자체의 영향이 결과에 섞인다.
- **층화**: 그대로 두고 5단계에서 나눠 본다. 표본은 유지되고 획득 조건의 영향이 드러나지만, 각 셀의 n이 작아진다.

기본 정답은 없다. 어느 쪽이든 `description`에 결정과 이유를 남긴다. 아무것도 안 하는 것만이 확실히 틀린 선택이다.

## 내장 op

`mival ops`로 현재 등록된 전체 목록(사이트 추가분 포함)을 본다.

**ECG:** `select_leads`, `resample`, `bandpass`, `notch`, `crop_or_pad`, `scale_units`, `normalize`, `drop_if_flat`
**영상(CR):** `apply_voi_lut`, `resize`, `rescale_intensity`

주의가 필요한 몇 가지:

| op | 함정 |
|----|------|
| `select_leads` | 위치로 자르지 말 것. MIMIC-IV-ECG와 벤더 export가 둘 다 '12-lead'여도 채널 순서가 다를 수 있다. 항상 이름으로 |
| `scale_units` | uV/mV 불일치는 1000배 입력 오류다. 대부분의 네트워크는 이걸 에러 없이 흡수해 자신 있게 틀린 예측을 낸다 |
| `notch` | 50Hz vs 60Hz는 사이트 파라미터다. 코드가 아니라 레시피에 있어야 한다 |
| `drop_if_flat` | 예외를 던진다. 전극 탈락 레코드는 지표의 분모가 아니라 attrition 표에 있어야 한다 |
| `apply_voi_lut` | MONOCHROME1 반전을 건너뛰면 일부 검사만 명암이 뒤집히고, 모델은 그것에 대해서도 그럴듯한 점수를 낸다 |

## 순서가 의미다

`normalize` → `resample`과 그 반대는 다른 결과를 낸다. 해시가 순서에 민감한 것은 버그가 아니라 설계다.

일반적으로 권장되는 순서: 채널 정리 → 단위 → 리샘플 → 필터 → 길이 맞춤 → 품질 게이트 → 정규화. 정규화를 마지막에 두는 이유는, 필터나 크롭이 통계량을 바꾸기 때문이다.

## 신규 op 등록

```python
from mival.preprocess.recipe import register_op

@register_op("baseline_wander_removal", requires_metadata=["Sampling Frequency"], modality="ECG")
def baseline_wander_removal(x, meta, cutoff_hz=0.5):
    """무엇을 왜 하는지 한 줄. mival ops에 이 첫 줄이 표시된다."""
    ...
```

등록하면 `validate_recipe`와 `mival ops`가 자동으로 인식한다. 등록하지 않은 함수는 레시피에서 쓸 수 없다 — 그게 레시피가 실행 가능한 명세인 이유다.

## 자주 겪는 문제

| 증상 | 원인 | 대응 |
|------|------|------|
| `requires ['Sampling Frequency']` 에러 | MI-CDM에 해당 속성 없음 | (a) op 파라미터로 명시적 override, (b) op 제거, (c) 코호트 필터로 제외. 임의 기본값은 선택지가 아니다 |
| 실패율이 5% 초과 | 대개 로더 불일치 또는 품질 게이트 | 실패 사유 분포를 먼저 본다 (`03_preprocess_failures.json`) |
| scipy 없어 필터가 폴백 | 환경 문제 | 폴백 사실이 sample warning에 남는다. 조용한 no-op은 금지 |
| 모델 입력 shape 불일치 | 레시피 출력과 카드 input_spec 불일치 | 4단계와 조율. 어느 쪽이 진실인지 먼저 정한다 |

## 재실행

해시가 같고 배열이 있으면 재실행하지 않는다. 해시가 다르면 이전 배열을 `_workspace_prev/`로 옮기고 **전체** 재실행한다. 부분 재실행은 배열 간 불일치를 만든다.
