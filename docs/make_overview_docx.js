const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  LevelFormat, convertInchesToTwip,
} = require("docx");

const FONT = "맑은 고딕";
const ACCENT = "1F4E79";
const LIGHT = "EEF3F9";

const P = (text, opts = {}) =>
  new Paragraph({
    spacing: { after: opts.after ?? 120, line: 300 },
    alignment: opts.align,
    indent: opts.indent,
    children: [new TextRun({ text, font: FONT, size: opts.size ?? 21,
      bold: opts.bold, italics: opts.italics, color: opts.color })],
  });

const H = (text, level) =>
  new Paragraph({
    heading: level,
    spacing: { before: level === HeadingLevel.HEADING_1 ? 320 : 240, after: 140 },
    children: [new TextRun({ text, font: FONT, bold: true,
      size: level === HeadingLevel.HEADING_1 ? 28 : 24, color: ACCENT })],
  });

const BULLET = (text, level = 0) =>
  new Paragraph({
    numbering: { reference: "b", level },
    spacing: { after: 80, line: 290 },
    children: [new TextRun({ text, font: FONT, size: 21 })],
  });

const cell = (text, { head = false, w, bold = false } = {}) =>
  new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: head ? { type: ShadingType.CLEAR, fill: LIGHT } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [new Paragraph({
      spacing: { after: 0, line: 280 },
      children: [new TextRun({ text, font: FONT, size: 19, bold: head || bold })],
    })],
  });

const table = (widths, rows) =>
  new Table({
    columnWidths: widths,
    width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    rows: rows.map((r, i) =>
      new TableRow({
        tableHeader: i === 0,
        children: r.map((c, j) => cell(c, { head: i === 0, w: widths[j] })),
      })),
  });

const RULE = new Paragraph({
  spacing: { before: 60, after: 200 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "C8D4E3", space: 1 } },
  children: [new TextRun("")],
});

const W = 9360; // 6.5in content width

const doc = new Document({
  creator: "Kyulee Jeon",
  title: "MI-VAL 프레임워크 계획서",
  numbering: {
    config: [{
      reference: "b",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: convertInchesToTwip(0.3), hanging: convertInchesToTwip(0.19) } } } },
        { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: convertInchesToTwip(0.6), hanging: convertInchesToTwip(0.19) } } } },
      ],
    }],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 1300, bottom: 1300, left: 1440, right: 1440 } } },
    children: [
      new Paragraph({
        spacing: { after: 60 },
        children: [new TextRun({ text: "MI-VAL", font: FONT, bold: true, size: 44, color: ACCENT })],
      }),
      P("MI-CDM 기반 표준화·재현가능 의료 AI 검증 프레임워크", { size: 24, color: "444444", after: 40 }),
      P("계획 개요 (Overview) · 2026-08-06 · 작성 지원: Claude", { size: 18, color: "888888", after: 60 }),
      RULE,

      H("1. 문제 정의", HeadingLevel.HEADING_1),
      P("유망한 의료 AI 모델의 상당수는 논문과 리더보드에는 존재하지만 실제 임상 데이터 위에서는 재현되지 않는다. 'The AI Nobody Sees'가 지적하듯, 병목은 알고리즘이 아니라 알고리즘 주변의 인프라 — 데이터가 어떻게 선택되고, 전처리되고, 평가되는지가 기록되지 않는다는 점 — 에 있다."),
      P("구체적으로 세 가지가 표준화되어 있지 않다."),
      BULLET("어떤 환자의 어떤 영상을 썼는가 (코호트 → 영상 선택 규칙)"),
      BULLET("그 영상을 어떻게 가공했는가 (전처리 파이프라인)"),
      BULLET("성능을 어떤 기준으로, 어떤 하위집단에서 측정했는가 (결과 지표)"),
      P("특히 촬영·획득 파라미터(ECG의 샘플링 주파수·리드 구성, CXR의 노출 조건 등)는 인구통계학적 요인만큼 혹은 그 이상으로 모델 성능을 좌우하지만, 대부분의 검증 연구에서 아예 보고되지 않는다.", { after: 60 }),

      H("2. 목표", HeadingLevel.HEADING_1),
      P("이미 구축한 MIMIC 기반 MI-CDM(OMOP CDM + ECG-DICOM ETL) 위에서, 서로 다른 모델을 동일한 조건으로 검증할 수 있는 표준화된 프레임워크 MI-VAL을 만든다."),
      P("1차 검증 목표: STEMI 분류 모델과 ECGFounder(파운데이션 모델)를 같은 코호트·같은 전처리·같은 지표 명세로 비교한다. 두 모델의 성능 차이가 파이프라인 차이가 아니라 모델 차이에서 온다는 것을 보장하는 것이 프레임워크의 존재 이유다.", { after: 60 }),
      table([1900, 7460], [
        ["목적", "내용"],
        ["기관 간 데이터 이동", "데이터를 옮기는 대신 '실행 명세(RunManifest)'를 옮긴다 → 다기관 분석"],
        ["결과 재현", "코호트·전처리·모델·지표를 각각 ID/해시/체크섬으로 고정"],
        ["전처리 표준화", "코드가 아닌 선언적 레시피(YAML) + 해시로 방법론 기술"],
        ["통제 밖 적용", "엄격히 통제된 연구환경 밖의 다양한 코호트에 유망 모델을 적용·검증"],
      ]),
      P("", { after: 40 }),

      H("3. 설계 원칙 — 온톨로지 기반 모듈화", HeadingLevel.HEADING_1),
      P("팔란티어의 Ontology 개념을 차용해, 각 단계를 '객체를 받아 객체를 내놓는' 모듈로 정의했다. 모든 단계는 느슨한 dict가 아니라 타입이 있는 객체를 주고받으며, 각 객체는 정체성(identity)·출처(provenance)·내용(payload)을 함께 갖는다."),
      BULLET("객체가 표준화되어 있으므로 각 단계를 독립적으로 교체·검증할 수 있다"),
      BULLET("에이전트가 다음 단계를 판단하는 데 필요한 정보는 전부 객체 위에 있다 (agentic 아키텍처의 전제)"),
      BULLET("자유도는 '정형화된 툴'이 아니라 '표준화된 명세와 쿼리'로 확보한다 — 사용자는 config만 바꾼다"),
      P("주요 객체: CohortSpec, ImageOccurrenceRef, ImageMetadata, ImageAsset, RetrievalSpec, PreprocessRecipe, ModelCard, EvaluationSpec, RunManifest.", { after: 60 }),

      H("4. 5단계 파이프라인", HeadingLevel.HEADING_1),
      P("코호트 정의는 기존 도구인 ATLAS를 그대로 사용한다. MI-VAL은 ATLAS가 끝나는 지점에서 시작한다.", { italics: true, color: "555555" }),
      table([700, 2100, 6560], [
        ["단계", "모듈", "하는 일"],
        ["(1)", "retrieve", "코호트 + index date 기준 윈도우/선택규칙으로 image_occurrence 조회 → local_path로 DICOM 실체 해석. 표준화된 SQL과 attrition(탈락) 표 산출"],
        ["(2)", "profile", "MI-CDM 메타데이터 분포 + 영상 보유 환자의 임상 특성(Table 1)을 시각적으로 skim하는 HTML 리포트. 이질성 플래그 자동 표시"],
        ["(3)", "preprocess", "선언적·메타데이터 인지형 전처리 레시피(리드 선택, 리샘플링, 필터, 정규화 등). 레시피는 해시로 고정"],
        ["(4)", "models", "모델 카드(RSNA ATLAS model.json 확장) 기반 모델 로딩(.pth/GitHub/HuggingFace/Zenodo) + 입력 규격 사전 검증"],
        ["(5)", "evaluate", "지표 선택 근거 기록, 부트스트랩 신뢰구간, 획득 메타데이터 기준 하위집단 층화 분석, 모델 비교표"],
      ]),
      P("", { after: 40 }),

      H("5. 핵심 차별점", HeadingLevel.HEADING_1),
      P("① 메타데이터를 인지하는 전처리 — 레시피의 각 연산은 자신이 필요로 하는 MI-CDM 속성을 선언한다. 예를 들어 '500Hz로 리샘플링' 연산은 원본 샘플링 주파수를 MI-CDM에서 읽는다. 값이 없으면 기본값을 가정하지 않고 그 레코드를 거부하고 보고한다."),
      P("② 추론 전 입력 규격 검증 — 모델 카드가 선언한 입력 조건(샘플링 주파수, 리드 순서, 단위)을 실제 코호트의 관측 메타데이터와 대조한다. '모델이 이전(transfer)되지 않았다'는 결론의 상당수는 사실 '입력 규격을 아무도 확인하지 않았다'이다."),
      P("③ 획득 파라미터 기준 층화 — 성능을 하나의 숫자로 보고하지 않는다. 500Hz에서는 잘 맞고 250Hz에서는 동전 던지기인 모델은 전체 AUROC 하나로는 드러나지 않는다."),
      P("④ 이식 가능성 검사 — RunManifest에 코호트 정의(ATLAS export), 레시피 해시, 모델 체크섬, 지표 명세, 실행 환경이 모두 담겼는지를 명령 한 줄로 확인한다. '이 연구가 재현 가능한가'가 의견이 아니라 검사 결과가 된다.", { after: 60 }),

      H("6. 산출물", HeadingLevel.HEADING_1),
      BULLET("Python 패키지 + CLI (mival) — 5개 모듈, 온톨로지 객체, 표준화된 SQL"),
      BULLET("JSON 스키마 3종 — dataset / model card / recipe (RSNA ATLAS 호환)"),
      BULLET("에이전트 하네스 — 단계별 전문 에이전트 정의 + 스킬 + 오케스트레이터"),
      BULLET("예제 스터디 config — STEMI vs ECGFounder 비교 프로토콜 전문"),
      BULLET("HTML 리포트 2종 — 데이터 명세 리포트, 검증 결과 리포트 (외부 의존성 없이 병원 내 워크스테이션에서 열림)"),
      P("", { after: 40 }),

      H("7. 다음 단계", HeadingLevel.HEADING_1),
      table([700, 5200, 3460], [
        ["", "할 일", "상태"],
        ["1", "프레임워크 스캐폴드 + 온톨로지 + 5개 모듈 구현", "완료 (dry-run 검증)"],
        ["2", "에이전트 하네스 (에이전트·스킬·오케스트레이터)", "진행 중"],
        ["3", "실제 MI-CDM(PostgreSQL) 연결 후 (1)(2) 실행 검증", "예정"],
        ["4", "STEMI 모델 카드 작성 및 가중치 연결", "예정"],
        ["5", "ECGFounder 연결 (linear probe 헤드)", "예정"],
        ["6", "두 모델 비교 실행 → 결과 리포트 초안", "예정"],
        ["7", "타 기관 RunManifest 이식 테스트 (다기관 검증)", "예정"],
      ]),
      P("", { after: 60 }),

      H("참고 문헌", HeadingLevel.HEADING_1),
      BULLET("Jeon K, Park WY, Kahn CE Jr, et al. Advancing Medical Imaging Research Through Standardization. Investigative Radiology. 2025;60(1)."),
      BULLET("Maier-Hein L, Reinke A, Godau P, et al. Metrics reloaded: recommendations for image analysis validation. Nature Methods. 2024;21:195-212."),
      BULLET("The Vasty Deep | Radiology: AI. The AI Nobody Sees: Why the future of medical AI depends on more than algorithms. 2026."),
      BULLET("RSNA ATLAS — model.json / dataset.json specification. github.com/RSNA/ATLAS"),
      BULLET("Isensee F, et al. nnU-Net — 자동 구성형 파이프라인 설계 참고. github.com/MIC-DKFZ/nnUNet"),
      BULLET("ACR–SIIM Practice Parameter for Imaging Artificial Intelligence."),
    ],
  }],
});

Packer.toBuffer(doc).then((b) => {
  fs.writeFileSync("/root/mi-val/docs/MI-VAL_계획개요.docx", b);
  console.log("written");
});
