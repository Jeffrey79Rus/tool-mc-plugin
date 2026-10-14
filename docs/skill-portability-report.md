# 내 스킬·플러그인 전역 사용 불가 진단 보고서

- 진단일: 2026-10-14 · 대상 환경: Claude Code 2.1.299 (클라우드 세션, 계정 동기화본)
- 진단 도구: [`tools/skill_doctor.py`](../tools/skill_doctor.py) — 같은 결과를 언제든 재현할 수 있다
- 대상: 내가 만든 스킬 15종(claude.ai에서 생성) + 내가 업로드한 플러그인 3종(consulting-deck-suite, iqvia-ppt-template, harness-pipeline) = Skill 도구 기준 21개

---

## 1. 핵심 메시지

**스킬과 플러그인 21개 모두 Claude Code에 설치는 되어 있다. 문제는 두 층위에서 생긴다.**

1. **(근본 원인, 21개 전부) 스킬 목록 예산 초과로 설명이 빠진다.** 설치된 스킬이 325개다(마켓플레이스 248개). 목록 전체는 15.8만 자인데 기본 예산은 8천 자(200K 컨텍스트)~4만 자(1M 컨텍스트)다. 예산을 넘으면 우선순위가 낮은 스킬부터 이름만 남기는데, 내 스킬이 가장 먼저 밀린다. 그러면 Claude는 "팩트체크해줘" 같은 요청을 받아도 `fact-verify`가 무슨 일을 하는지 알 수 없어 호출하지 않는다.
2. **(개별 결함, 4개 단위) 특정 표면에서만 통하는 경로·도구를 하드코딩했다.** claude.ai 채팅 컨테이너 경로(`/mnt/skills/plugins`), 존재하지 않는 경로(`/root/.claude/skills/synced/pptx/...`), 미설치 런타임(pptxgenjs)이 있어서 Claude Code·Cowork에서 실행 단계가 실패한다.

## 2. 근본 원인 — 스킬 목록 예산 (실측 검증)

| 항목 | 값 |
|---|---|
| 동기화된 스킬·명령 수 | 338개 (마켓플레이스 248 · Anthropic 기본 55 · 내 것 21 · 명령 13 · 기타 1) |
| 목록 전체 글자 수 | 158,292자 (내 것 9,435자, 6%) |
| 기본 예산 | 컨텍스트 토큰 × 4 × 1% → 200K 모델 8,000자, 1M 모델 40,000자 |
| 예산 초과 시 동작 | 우선순위 순으로 설명을 채우고 넘친 스킬은 `- 이름`만 남긴다 |

**실측 결과** (`claude -p`로 같은 질문을 던져 모델이 보는 목록을 확인):

| `SLASH_COMMAND_TOOL_CHAR_BUDGET` | `anthropic-skills:*` 28개 중 설명이 보이는 스킬 | `fact-verify` 설명 |
|---|---|---|
| 기본값 | 0 | 없음 |
| 60,000 | 0 | 없음 (내 스킬이 우선순위 최하위라 여전히 밀림) |
| 200,000 | **28** | "팩트·출처·논리 3중 검증. …" 정상 노출 |

플러그인 점유 상위: small-business 36.0K · Anthropic 기본 29.1K · nimble 14.6K · sales 13.3K · adobe 11.4K · figma 6.3K. 이 6개가 전체의 70%를 차지한다.

> 근거: Claude Code 바이너리 2.1.299의 목록 생성 로직(`SLASH_COMMAND_TOOL_CHAR_BUDGET` 우선, 없으면 `context × 4 × 0.01`, 설명 1,536자 상한). 공식 문서: [Skills — 설명 예산](https://code.claude.com/docs/en/skills), [Settings — 환경변수](https://code.claude.com/docs/en/settings)

## 3. 개별 결함 목록

| 심각도 | 단위 | 위치 | 결함 | 영향 표면 | 조치 |
|---|---|---|---|---|---|
| ERROR | consulting-deck-suite | consulting-deck/SKILL.md:126, references/build-constraints.md:112, deck-design-review/SKILL.md:18 | `python3 /root/.claude/skills/synced/pptx/scripts/office/validate.py` — 실제 경로는 `synced/<조직>_<계정>/pptx/...`라 파일이 없다 | Claude Code·Cowork | 표면 무관 탐색식으로 교체 (수정본 v0.2.4) |
| ERROR | consulting-deck-suite | deck-template-register/SKILL.md:77 | 같은 이유로 `thumbnail.py` 경로가 없다 | Claude Code·Cowork | 위와 같음 |
| WARN | consulting-deck-suite | consulting-deck/scripts/build_deck.js:18 | `require('pptxgenjs')` — claude.ai 컨테이너에만 기본 설치돼 있다 | Claude Code·Cowork | 실패 시 `npm i pptxgenjs@3.12.0` 안내 추가 |
| ERROR | skill-versioning | SKILL.md:39, 47 | `/mnt/skills/plugins/...` 고정 — claude.ai 채팅 컨테이너에만 있는 경로 | Claude Code·Cowork | 표면별 `<SKILLS_ROOT>` 규칙 도입 |
| WARN | skill-versioning | SKILL.md:25 | 관리 대상이 "10종"으로 고정돼 10/14에 만든 BD Proposal WS1~WS4·skill-versioning 5종이 버전 관리에서 빠진다 | 전 표면 | manifest 기준(현재 15종)으로 변경 |
| INFO | skill-versioning | SKILL.md:59 | `propose_skills`(스킬 저장 카드)는 claude.ai 채팅 전용이다 | Claude Code·Cowork | 복원은 claude.ai에서 하도록 안내 문구 추가 |
| WARN | iqvia-ppt-template | SKILL.md:99 | `<SKILL_DIR>`를 `find /`(전체 디스크)로 찾는다. 느리고, 사본이 여러 개면 엉뚱한 것을 고른다 | 전 표면 | 로드 시 표시되는 Base directory 우선 (수정본 v1.2.1) |
| ERROR | dart-lookup | SKILL.md:12 | OpenDART 인증키가 평문으로 들어 있다. 동기화된 모든 기기와 업로드본에 복제된다 | 보안 | **자동 수정 안 함** — 아래 4-3 참조 |
| INFO | bd-proposal-ws1~4 | SKILL.md:56 등 | Projects 도구(project_read/write)는 claude.ai Projects 세션 전용이다. 문서가 이미 "연결된 세션이면"으로 조건을 달아 두었다 | Claude Code | 조치 불필요 |
| INFO | 다수 | — | 다른 스킬을 짧은 이름(`fact-verify`)으로 부른다. Skill 도구 이름은 `anthropic-skills:fact-verify`, `consulting-deck-suite:consulting-deck`이다. 목록에 설명이 보이면 모델이 매칭하므로 2절을 해결하면 함께 풀린다 | Claude Code | 2절 조치로 해소 |
| INFO | harness-pipeline | pipeline/scripts, lib/setup.mjs | 스케줄러가 Windows 전용(schtasks·.vbs)이다. 설계상 Code 전용이고 `${CLAUDE_PLUGIN_ROOT}` 치환은 정상 동작함을 확인했다 | macOS·Linux | 조치 불필요 (README에 cron 대안 명시됨) |

정상 확인: 나머지 스킬(fact-verify, consulting-persona-team, deliverable-upgrade, korean-polish, storyline-development, financial-statement-analysis 등)은 경로·도구 결함이 없다. 2절만 해결하면 전역에서 동작한다.

## 4. 실행한 조치와 남은 조치

### 4-1. 실행 완료

| # | 조치 | 결과 |
|---|---|---|
| 1 | 진단·수정 도구 `tools/skill_doctor.py` 작성 (`audit` / `fix` / `budget`) | 원본 진단 ERROR·WARN 9건 → 수정본 재진단 0건 |
| 2 | 수정본 3종 생성 및 재업로드용 zip 패키징 | `consulting-deck-suite.zip`(v0.2.4), `iqvia-ppt-template.zip`(v1.2.1), `skill-versioning.zip` |
| 3 | 이 저장소의 `.claude/settings.json`에 `SLASH_COMMAND_TOOL_CHAR_BUDGET=190000` 설정 | 이 저장소에서 여는 Claude Code 세션은 내 스킬 설명이 모두 노출된다 |
| 4 | (10/14 추가) 같은 파일에서 small-business·sales·nimble·adobe-for-creativity·figma를 `enabledPlugins`로 끄고 예산을 90,000으로 낮춤 | 실측: 5개를 끄면 **기본 예산만으로도** 내 스킬 28개 설명이 모두 노출된다. claude.ai·Cowork 계정 설정은 바뀌지 않는다 |

> zip에는 IQVIA 템플릿·폰트·내부 정책이 들어 있다. 이 저장소는 **public**이므로 zip과 스킬 원문은 커밋하지 않았다(대화 첨부로만 전달).

### 4-2. 사용자가 해야 할 조치 (계정 권한 필요)

1. **수정본 재업로드** (claude.ai → Customize)
   - 플러그인 2종: 기존 `consulting-deck-suite`, `iqvia-ppt-template`를 zip으로 교체 업로드
   - 스킬 1종: `skill-versioning.zip` 업로드 또는 claude.ai 채팅에서 내용 교체
2. **전역 예산 설정** — 내 PC의 Claude Code·Cowork 전체에 적용하려면 한 번만 실행한다.
   ```bash
   python3 tools/skill_doctor.py budget --apply      # ~/.claude/settings.json 의 env에 기록
   ```
3. **(권장) 안 쓰는 마켓플레이스 플러그인 끄기** — PC의 Claude Code는 `claude plugin disable sales@synced --scope user`처럼 명령으로도 끌 수 있다(이름은 `claude plugin list`로 확인). — 예산을 키우면 세션마다 목록이 약 4~5만 토큰을 차지한다. 컨설팅·제약 업무와 거리가 먼 small-business(44개), sales(36개), nimble(15개), adobe(15개), figma(14개)를 끄면 목록이 약 8만 자 줄어든다. 그 뒤 `budget`을 다시 실행해 예산을 낮추면 된다.

### 4-3. 보안 권고 — dart-lookup 인증키

- 인증키가 SKILL.md 본문에 평문으로 있다. 이 파일은 계정에 연결된 모든 기기와 클라우드 세션에 복제된다.
- 권고: OpenDART에서 키를 재발급하고, 스킬에는 "키는 사용자에게 묻거나 환경변수 `DART_API_KEY`에서 읽는다"고만 적는다. 키 교체는 운영 판단이 필요해 자동 수정 대상에서 뺐다.

## 5. 재진단 방법

```bash
python3 tools/skill_doctor.py audit            # 내 스킬·플러그인 진단 (ERROR가 있으면 종료코드 1)
python3 tools/skill_doctor.py audit --all      # 마켓플레이스 포함 전체
python3 tools/skill_doctor.py fix --out ./skill-fixes   # 수정본+zip 생성 (원본 무변경)
python3 tools/skill_doctor.py budget           # 예산 계산만
```

스킬을 새로 만들거나 고친 뒤 `audit`을 돌리면 같은 유형의 결함을 배포 전에 잡을 수 있다.
