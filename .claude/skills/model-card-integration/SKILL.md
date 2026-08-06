---
name: model-card-integration
description: MI-VAL 4단계 — 모델 카드(RSNA ATLAS model.json 확장) 작성, .pth/GitHub/HuggingFace/Zenodo에서 모델 로딩, 추론 전 입력 규격 검증. "모델 불러와", "model.json 작성", "모델 카드 만들어", "ECGFounder 연결", "허깅페이스에서 가져와", "pth 파일 로딩", "하이퍼파라미터 설정", "앙상블 구성", "모델이 입력을 안 받아", "input_spec 확인" 같은 요청에서 반드시 사용할 것. 전처리 레시피(3단계)나 성능 평가(5단계)에는 사용하지 않는다.
---

# Model Card Integration

"누군가 발표한 모델"을 "이 코호트에 돌릴 수 있는 모델"로 바꾼다. 그 변환의 계약서가 모델 카드다.

## RSNA ATLAS와의 관계

ATLAS `model.json`은 문서화 측면(용도, 학습 데이터, 한계, 라이선스)을 이미 잘 정의했다. MI-VAL은 그 필드명을 그대로 유지하고, 실제로 깨지는 세 가지를 더한다.

| MI-VAL 추가 | 왜 |
|-------------|-----|
| `source_type` / `source_uri` / `checksum_sha256` | 가중치를 어떻게 가져오고, 그게 우리가 실행한 그것인지 |
| `input_spec` | 어떤 입력 텐서를 기대하는지 — 자동 대조의 대상 |
| `framework` / `python_version` / `dependencies` | 어떤 환경에서 검증됐는지 |

ATLAS 카드를 import하면 매핑되지 않는 필드는 버리지 않고 `hyperparameters._atlas_extra`에 보존된다.

```python
from mival.models.card import from_atlas, load_card
card = load_card("model.json")   # ATLAS 형식 자동 감지
```

## input_spec — 적지 않은 것은 아무도 검증하지 않는다

```yaml
input_spec:
  sampling_hz: 500
  n_leads: 12
  lead_order: [I, II, III, aVR, aVL, aVF, V1, V2, V3, V4, V5, V6]
  duration_s: 10
  units: mV
  shape: [12, 5000]
```

각 키가 자동 검사 하나가 된다. 빈 `input_spec`으로도 파이프라인은 돌지만, 그건 "입력 계약을 아무도 확인하지 않았다"는 상태를 결과에 기록하는 것과 같다.

## 추론 전 대조 — 이 스킬의 핵심

```python
from mival.models.card import check_input_compatibility
check = check_input_compatibility(card, retrieval_result)
```

카드가 선언한 조건을 **실제 코호트의 관측 MI-CDM 메타데이터**와 비교한다. 불일치는 사유와 처방을 함께 낸다.

```json
{"field": "sampling_hz", "expected": 500, "observed": [250.0], "ok": false,
 "source": "MI-CDM measurement via image_feature",
 "remedy": "add a `resample` op to the recipe"}
```

"모델이 전이되지 않았다"는 결론의 상당수는 사실 여기서 잡혔어야 할 문제다. 이 검사를 건너뛰면 250Hz 데이터를 500Hz 모델에 먹이고, 모델은 그럴듯하게 낮은 성능을 내고, 우리는 그걸 "일반화 실패"라고 부르게 된다.

## 소스 타입별 로딩

| source_type | source_uri 예 | 주의 |
|-------------|--------------|------|
| `local` | `~/models/stemi_cnn.pth` | 가장 단순. 체크섬은 여전히 권장 |
| `github` | `https://github.com/org/repo` | `entrypoint`로 아키텍처를 만든다. **원격 코드 실행**이므로 `allow_remote_code=True`가 필요하고, 사용자 승인을 받아야 한다 |
| `huggingface` | `PKUDigitalHealth/ECGFounder` | 리포지토리는 조용히 바뀐다. `revision` 고정 + 체크섬 필수 |
| `zenodo` / `url` | DOI 또는 직접 URL | 다운로드 후 체크섬 검증 |

```yaml
entrypoint: "stemi_cnn.model:build_model"   # 아키텍처 빌더
hyperparameters:
  build_kwargs: {n_classes: 1}
  strict_load: true
```

`entrypoint`가 없으면 TorchScript나 통째로 pickle된 모델로 간주한다.

## 체크섬은 경고가 아니라 중단 사유

불일치하면 즉시 멈춘다. 우리가 실행하려는 가중치가 카드가 말하는 그것이 아니라는 뜻이고, 그 상태로 나온 숫자는 아무것도 의미하지 않는다.

## 앙상블

N개 모델의 평균이면 N개 카드가 매니페스트에 들어간다. "앙상블"이라는 단어 하나로는 재현되지 않는다.

```python
infer = registry.build_ensemble([card_a, card_b, card_c])
```

## 파운데이션 모델 (ECGFounder 등)

representation 모델은 그 자체로 분류기가 아니다. 비교 대상이 되려면 헤드까지 카드에 있어야 한다.

```yaml
task: representation
hyperparameters:
  head: linear_probe
  freeze_backbone: true
```

지도학습 모델(STEMI CNN)과 파운데이션 모델을 같은 프레임워크에서 비교할 때, 헤드 학습에 쓴 데이터가 평가 코호트와 겹치지 않는지 확인한다. 겹치면 그건 검증이 아니라 학습 성능이다.

## portability 검증

```python
from mival.models.card import validate_card
problems = validate_card(card)
```

문서화가 부족한 것과 재실행이 불가능한 것을 구분한다. 이 함수가 잡는 것은 후자다 — 체크섬 없는 원격 소스, 프레임워크 핀 없음, `input_spec` 없음.

## 자주 겪는 문제

| 증상 | 원인 | 대응 |
|------|------|------|
| `entrypoint` import 실패 | 경로/의존성 | 1회 재시도 후 보고. 아키텍처 코드를 추측해서 만들지 않는다 |
| state_dict 키 불일치 | 래핑된 체크포인트 (`{"state_dict": ...}`) | 로더가 자동 처리. 여전히 안 되면 `strict_load: false`를 고려하되, 무엇이 안 실렸는지 기록한다 |
| 출력이 [0,1] 밖 | activation 미적용 모델인데 카드에 sigmoid로 적힘 | 카드를 고친다. 점수 해석이 통째로 달라진다 |
| 입력 계약 대조가 ok=false인데 통과시킴 | 그러면 안 된다 | 처방대로 레시피를 고치거나, 왜 무시해도 되는지를 리포트에 적는다 |

## 재실행

카드 내용이 같으면 가중치 캐시를 쓴다. 카드가 바뀌면 해당 모델만 재검사한다 — 모델 간 독립이므로 부분 재실행이 안전한 몇 안 되는 단계다.
