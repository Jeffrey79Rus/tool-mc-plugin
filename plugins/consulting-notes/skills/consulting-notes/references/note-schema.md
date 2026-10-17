# note.json 스키마

빌드 스크립트(`scripts/build_note.py`)가 읽는 노트 데이터 형식이다. 모든 "항목(item)"은 같은 모양을 쓴다.

## 항목(item)

```json
{"text": "문장", "src": ["12:42", "12:42-12:49", "#34"], "basis": "S", "check": "ok", "note": "확인할 내용"}
```

| 필드 | 필수 | 설명 |
|---|---|---|
| `text` | ✔ | 노트 문장. To-do는 `task`, 가설은 `h`를 쓴다 |
| `src` | △ | 출처. `"mm:ss"`(그 초에 시작한 세그먼트), `"mm:ss-mm:ss"`(범위), `"#id"`/`"#id-#id"`(세그먼트 번호). 1시간 넘는 녹음은 `"h:mm:ss"` |
| `basis` | | `S` 상대 진술(기본값, src 있을 때) · `I` 작성자 해석·종합(src 없어도 됨) · `V` 별도 자료로 검증됨 |
| `check` | 최종본 ✔ | 팩트체크 판정. `ok`(전사 일치) / `warn`(확인 필요). 없으면 "미검증" |
| `note` | | 확인 필요 사유, 오인식 해석 근거. HTML에 항목 아래 표시된다 |

## 전체 구조

```json
{
  "type": "meeting",                       // 또는 "interview"
  "title": "미팅명 (H1, 기본 정보에 표시)",
  "short_title": "탭 제목용 짧은 이름 (선택)",
  "meta": {
    "purpose": "미팅 목적 한 문장",
    "datetime": "2026-10-17 14:00–15:00 또는 '미상 [확인 필요]'",
    "format": "대면 / 화상 / 전화",
    "interviewee": "Expert A — 前 ○○사 전략 임원 (인터뷰 전용, 실명 금지)",
    "attendees": [{"side": "당사", "name": "홍길동", "role": "PM"}],
    "author": "작성자", "written": "작성일", "version": "v1",
    "confidentiality": "대외비",
    "source": {"audio": "38:12", "engine": "Whisper large-v3-turbo", "quality": "상/중/하", "diarization": "문맥 추정 / VTT 화자 / 없음"}
  },
  "speakers": {
    "inferred": true,
    "ranges": [{"from": "00:00", "to": "03:37", "name": "고객사"}],
    "overrides": {"04:53-05:02": "고객사", "#202": "당사"},
    "note": "화자 표시 설명 (선택, 기본 문구 있음)"
  },
  "executive_summary": {
    "headline": item,                      // 한 줄 결론
    "points": [item, ...],                 // 미팅: 핵심 논의·합의 / 인터뷰: 핵심 발견 (3~5개)
    "so_what": item                        // 우리에게 의미 (보통 basis I)
  },
  "agenda": [
    {"title": "아젠다명 또는 KQ 제목", "time": "00:22–03:25", "question": "Key Question 문장 (인터뷰)",
     "items": [item, ...], "implication": item}
  ],
  "hypotheses": [                          // 인터뷰 필수
    {"h": "가설 문장", "verdict": "support|refute|neutral", "evidence": "근거 요약", "src": [...], "check": "ok"}
  ],
  "conclusion": {
    "summary": item,
    "items_label": "결정사항 / 시사점 (선택, 유형별 기본값 있음)",
    "items": [item, ...]
  },
  "todos": [
    {"task": "할 일", "owner": "담당", "due": "기한", "status": "진행 전", "src": [...], "check": "ok"}
  ],
  "open_issues": [item, ...],              // 선택
  "glossary": [{"heard": "IKBR · IQ백", "correct": "IQVIA"}],
  "excluded": [{"range": "23:43-33:05", "reason": "개인 경력 이야기", "redact": true}]
}
```

## 필수 섹션 (빌드가 검사)

- `meeting`: `meta`, `executive_summary`, `agenda`, `conclusion`, `todos`
- `interview`: 위 5개 + `hypotheses`

## 빌드 검사 항목

| 구분 | 내용 | 처리 |
|---|---|---|
| 오류 | 필수 섹션 누락, 전사본에서 찾을 수 없는 `src`, `basis`가 `I`가 아닌데 `src` 없음 | 빌드 중단 |
| 경고 | 노트 문장의 숫자가 인용 원문에 없음 | 리포트에 표시, 사람이 판단 |
| 경고 | `check` 없음(미검증) | `--strict`면 빌드 중단 |
