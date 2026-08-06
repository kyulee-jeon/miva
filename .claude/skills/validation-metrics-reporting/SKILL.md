---
name: validation-metrics-reporting
description: MI-VAL 5단계 — 표준화되고 재현가능한 성능 지표 산출. 지표 선택 근거, 부트스트랩 신뢰구간, 획득 메타데이터 기준 층화, 모델 비교 리포트. "성능 평가해줘", "AUROC 계산", "지표 뭘 써야 해", "신뢰구간", "하위집단 분석", "층화 분석", "모델 비교표", "임계값 정하기", "캘리브레이션", "결과 리포트 다시" 같은 요청에서 반드시 사용할 것. 데이터 분포 확인(2단계)이나 모델 로딩(4단계)에는 사용하지 않는다.
---

# Validation Metrics & Reporting

같은 코호트·같은 레시피·같은 지표 명세로 여러 모델을 평가한다. 그래야 차이가 파이프라인이 아니라 모델에서 왔다고 말할 수 있다.

## 지표 선택은 결정이지 기본값이 아니다

Metrics Reloaded의 problem fingerprint 논리를 따른다: task 유형 + 유병률 + false positive의 비용이 어떤 지표가 방어 가능한지를 정한다.

```python
from mival.evaluate.metrics import recommend
rec = recommend("binary_classification", prevalence=0.03, cost_of_fp="high")
```

이 함수는 지표만이 아니라 **rationale**을 반환하고, 그 rationale이 리포트에 그대로 실린다. 독자가 추측하는 대신 반박할 수 있어야 한다.

주요 규칙:

| 상황 | 지표 | 이유 |
|------|------|------|
| 기본 이진 분류 | AUROC | 임계값 무관, 기관 간 비교 가능 |
| 유병률 < 10% | AUPRC 추가 | 심한 불균형에서 AUROC는 관대하다 |
| 항상 | MCC / balanced accuracy | accuracy는 skill이 아니라 유병률을 따라간다 |
| FP 비용 높음 | 고정 특이도 + PPV | 임상 배치 조건에 맞춘 운영점 |
| 다중분류 | macro 평균 | 우세 클래스가 희귀 클래스를 가리지 못하게 |

## 숫자 하나는 결과가 아니다

모든 지표에 부트스트랩 신뢰구간을 붙인다. 시드를 고정한다. "부트스트랩했다"에 시드가 없으면 그것도 재현 불가다.

```python
from mival.evaluate.metrics import auroc, bootstrap_ci
ci = bootstrap_ci(auroc, y_true, y_score, n=1000, level=0.95, seed=42)
# → {"point": 0.87, "lo": 0.83, "hi": 0.91, "n_boot": 1000}
```

단일 클래스 resample에서 나온 NaN draw는 자동으로 제외되고, 실제 사용된 draw 수가 `n_boot`에 남는다.

## 임계값 규칙을 반드시 적는다

무언의 0.5는 임상 AI 보고에서 가장 흔한 암묵적 가정이다.

| `operating_point` | 의미 |
|-------------------|------|
| `youden` | 민감도+특이도 최대 |
| `fixed_threshold` | 사전 지정 값 (`threshold` 필수) |
| `fixed_sensitivity` | 목표 민감도에 가장 가까운 지점 |
| `fixed_specificity` | 목표 특이도에 가장 가까운 지점 |

선택한 규칙과 결과 임계값이 모두 `EvaluationResult.overall`에 기록되고 리포트에 표시된다.

## 층화 — 획득 메타데이터를 먼저

```yaml
subgroup_by:
  - Sampling Frequency     # 획득 파라미터 먼저
  - Manufacturer
  - gender                 # 인구통계는 그 다음
  - age_group
```

인구통계 층화는 이미 관행이다. 성능 변동의 상당 부분이 촬영 파라미터에 있고, MI-CDM이 그걸 쿼리 가능하게 만든 유일한 도구다. 여기서 안 하면 CDM을 쓴 이유가 사라진다.

500Hz에서는 잘 맞고 250Hz에서는 동전 던지기인 모델은 전체 AUROC 하나로는 절대 드러나지 않는다.

**작은 하위집단을 숨기지 않는다.** n < 30이면 `small_n` 플래그와 caveat이 붙지만 표에는 남는다. 억제하면 "보고되지 않은 것은 문제없었다"로 읽힌다.

## 격차는 CI 중첩으로 말한다

```json
{"best": {"level": "500", "point": 0.91}, "worst": {"level": "250", "point": 0.54},
 "gap": 0.37, "ci_overlap": false,
 "interpretation": "CIs disjoint — gap is unlikely to be noise"}
```

p값을 쓰지 않는 이유: 이 정도 표본에서 정직한 문장은 대개 "CI가 겹치므로 X만큼의 차이를 배제할 수 없다"이다. 다중비교 보정을 하지 않은 하위집단 p값은 그보다 덜 정직하다.

## 분모를 밝힌다

코호트 N → 영상 보유 M → 전처리 성공 K → 평가 n. 이 사슬이 리포트 상단에 있어야 한다. 모델별 n이 다르면 비교표에 나란히 싣고 왜 다른지 적는다 — 다른 분모의 숫자를 나란히 놓는 것 자체가 오해를 만든다.

## 캘리브레이션

ECE와 Brier score를 함께 낸다. ECE가 크다는 것은 실패가 아니라 findings다: 순위는 맞지만 확률로 읽으면 안 된다는 뜻이고, 임계값 기반 지표의 해석에 주의가 붙어야 한다.

## 리포트

```python
from mival.evaluate.report import write_report
write_report(results, "_workspace/05_results.html", manifest=manifest_dict)
```

모델 비교표 + 지표 선택 근거 + 모델별 층화 표 + 매니페스트 전문. 외부 의존성 없이 열린다.

예측값 CSV(`05_predictions_<model>.csv`)도 남긴다. 다른 사람이 우리 지표 코드를 안 믿어도 원 예측값으로 직접 계산할 수 있어야 한다.

## 자주 겪는 문제

| 증상 | 원인 | 대응 |
|------|------|------|
| 하위집단 AUROC가 NaN | 그 셀이 단일 클래스 | 정상. n과 함께 "계산 불가"로 표시 |
| 유병률 0 또는 1 | 라벨 쿼리 오류 가능성 높음 | 중단하고 라벨 정의 확인 |
| CI가 극단적으로 넓음 | n이 작거나 클래스가 극도로 불균형 | 정상 동작. 넓은 CI가 곧 답이다 — 좁히려 하지 말고 그대로 보고 |
| 층화 변수가 5개 초과 | 셀당 n이 무의미해짐 | 상위 몇 개로 좁히고 나머지는 JSON에만 |

## 재실행

`EvaluationSpec`이 같으면 재사용한다. 층화 변수만 추가되면 예측값 CSV를 읽어 하위집단 블록만 다시 계산한다 — 추론을 다시 돌릴 필요가 없다.
