# tool-mc-plugin
[Tool] 자동화, 플러그인, 스킬

전략컨설팅 업무용 Claude 플러그인 마켓플레이스.

| 플러그인 | 스킬 | 하는 일 |
|---|---|---|
| `consulting-notes` | `consulting-notes` | 회의·인터뷰 전사본 → 미팅노트/인터뷰노트. 모든 문장을 전사 원문과 팩트체크하고, 출처(시각·화자)를 누르면 원문이 펼쳐지는 공유용 HTML 생성 |
| `whisper` | `whisper-transcribe` | 녹음 파일 → txt·srt·md·json 전사본 (faster-whisper, 한국어 포함 99개 언어) |
| `yt-dlp` | `yt-dlp-download` | YouTube 등 영상 URL → 음성(m4a)·영상(mp4)·자막. 자막은 whisper와 같은 전사본 형식(.json)으로 바로 변환 |

세 스킬은 이어서 쓴다: 영상 URL → `yt-dlp-download`(자막 있으면 바로 `.json`, 없으면 `.m4a`) → `whisper-transcribe` → `.json` → `consulting-notes` → HTML 노트.

## 설치

### Claude Code (로컬 CLI·데스크톱)

```
/plugin marketplace add jeffrey79rus/tool-mc-plugin
/plugin install consulting-notes@tool-mc-plugin
/plugin install whisper@tool-mc-plugin
/plugin install yt-dlp@tool-mc-plugin
```

비공개 저장소라면 GitHub 인증(`gh auth login` 또는 git 자격 증명)이 되어 있어야 한다.

### claude.ai · Cowork · Claude Code 클라우드 (계정 전역)

`packages/` 폴더의 zip을 claude.ai에 올리면 계정에 동기화되어 웹·데스크톱·Cowork·클라우드 세션에서 쓸 수 있다.

- **플러그인으로 올리기**: `consulting-notes-plugin.zip`, `whisper-plugin.zip`, `yt-dlp-plugin.zip` — 기존 플러그인(consulting-deck-suite 등)을 올린 것과 같은 메뉴에서 업로드
- **스킬로 올리기**: `consulting-notes-skill.zip`, `whisper-transcribe-skill.zip`, `yt-dlp-download-skill.zip` — 설정의 스킬 메뉴에서 zip 업로드

zip은 `python tools/package.py`로 다시 만든다.

## 실행 환경 요건 (yt-dlp)

- Python 3.9 이상, `ffmpeg`
- `pip install -U yt-dlp` — `scripts/setup.sh`가 설치. 사이트 변경이 잦아 오류가 나면 먼저 최신판으로 갱신한다
- 허용 도메인: `www.youtube.com`, `youtu.be`, `*.googlevideo.com` (유튜브 기준). Claude Code 클라우드 환경은 기본적으로 막혀 있어 네트워크 액세스 "사용자 지정"에 추가하거나 로컬 Claude Code·Cowork에서 실행한다
- 내려받은 파일은 업무 참고용으로만 쓰고 재배포하지 않는다

## 실행 환경 요건 (whisper)

- Python 3.10 이상, `ffmpeg`
- `faster-whisper==1.2.1`, `av==16.1.0` (av 17 이상은 호환되지 않음) — `scripts/setup.sh`가 설치
- 모델 다운로드 허용 도메인: `huggingface.co`, `*.hf.co`
- Claude Code 클라우드 환경이면 환경 설정 → 네트워크 액세스 "사용자 지정"에 위 도메인 추가, 설정 스크립트에 다음 줄을 넣으면 세션마다 자동 설치된다:
  `pip install "faster-whisper==1.2.1" "av==16.1.0"`
- claude.ai 채팅의 코드 실행 환경은 외부 접속 제한으로 모델을 받지 못할 수 있다. 받아쓰기는 Claude Code나 Cowork에서 실행한다.

## 구조

```
.claude-plugin/marketplace.json
plugins/
  consulting-notes/
    .claude-plugin/plugin.json
    skills/consulting-notes/
      SKILL.md
      scripts/build_note.py          # 팩트체크 리포트 + HTML 빌드
      assets/note_template.html      # 노트 디자인·상호작용
      references/                    # 미팅/인터뷰 작성 규칙, 스키마, 팩트체크 기준
  whisper/
    .claude-plugin/plugin.json
    skills/whisper-transcribe/
      SKILL.md
      scripts/transcribe.py, setup.sh
      references/benchmarks.md
  yt-dlp/
    .claude-plugin/plugin.json
    skills/yt-dlp-download/
      SKILL.md
      scripts/download.py, setup.sh  # info·audio·video·subs·convert 모드
      references/yt-dlp-options.md
packages/                            # claude.ai 업로드용 zip
tools/package.py
```

고객 녹음·노트(note.json, 전사본)는 이 저장소에 커밋하지 않는다.
