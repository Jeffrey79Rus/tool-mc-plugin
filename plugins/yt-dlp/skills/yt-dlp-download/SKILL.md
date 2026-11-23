---
name: yt-dlp-download
description: YouTube·Vimeo·네이버TV 등 yt-dlp가 지원하는 수천 개 사이트의 영상·음성·자막을 내려받는 스킬. "유튜브 받아줘", "영상 다운로드", "이 링크 음성만", "유튜브 자막 추출", "영상 받아쓰기", "강연 내용 정리해줘", "youtube download", "yt-dlp" 요청이나 영상 URL이 주어졌을 때 사용. 자막이 있으면 전사 없이 바로 전사본(.json/.srt/.txt)으로 바꾸고, 없으면 음성만 받아 whisper-transcribe 스킬로 넘긴다. 이어서 회의록·강연노트가 필요하면 consulting-notes 스킬을 쓴다.
---

# yt-dlp Download — 영상·음성·자막 내려받기

[yt-dlp](https://github.com/yt-dlp/yt-dlp)(유튜브 등에서 영상을 내려받는 공개 도구)를 감싼 스킬이다. 영상 URL 하나로 세 갈래가 가능하다.

| 원하는 것 | 모드 | 결과 |
|---|---|---|
| 내용만 알면 된다 (강연·인터뷰·설명회 정리) | `subs` → 없으면 `audio` + whisper | 전사본 `.json/.srt/.txt/.md` |
| 음성 파일 | `audio` | `.m4a` (whisper-transcribe 입력) |
| 영상 파일 | `video` | `.mp4` (기본 1080p 이하) |

**기본 순서(내용 정리 요청일 때):** `info`로 자막 유무 확인 → 수동 자막이 있으면 `subs` → 없으면 `subs`(자동 자막) 또는 정확도가 중요하면 `audio` 후 whisper-transcribe. 자동 자막은 고유명사·숫자 오인식이 잦으니 중요 문장은 whisper 전사나 영상으로 확인한다.

## 1. 환경 준비 (처음 한 번)

```bash
bash <이 스킬 폴더>/scripts/setup.sh      # 마지막 줄 "준비됨: <python 경로>"를 다음 단계에서 쓴다
```

- 이미 설치돼 있으면 그 python 경로만 알려 준다. 없으면 `~/.cache/yt-dlp-skill/venv`를 만들어 설치한다.
- Windows: `py -m pip install -U yt-dlp` 후 `py <스킬 폴더>\scripts\download.py ...`
- `ffmpeg`가 있어야 음성 추출·화질 합치기·구간 자르기가 된다.
- yt-dlp는 사이트 변경에 맞춰 자주 갱신된다. "Unable to extract", "Sign in to confirm you're not a bot" 류 오류가 나면 먼저 `pip install -U yt-dlp`로 최신판을 받는다.

| 도메인 | 용도 |
|---|---|
| `www.youtube.com`, `youtu.be`, `*.googlevideo.com` | 유튜브 페이지·영상 본체 |
| `i.ytimg.com` | 썸네일(선택) |
| `pypi.org`, `files.pythonhosted.org` | 패키지 설치 |

Claude Code 클라우드 환경은 기본적으로 유튜브 접속이 막혀 있다. 환경 설정의 네트워크 액세스를 "사용자 지정"으로 두고 위 도메인을 추가하거나, 로컬 Claude Code·Cowork에서 실행한다. claude.ai 채팅의 코드 실행 환경에서는 외부 접속이 안 되므로 쓸 수 없다.

## 2. 실행

```bash
PY=<setup.sh가 알려 준 python 경로>
DL="<이 스킬 폴더>/scripts/download.py"

$PY $DL info  "<URL>"                       # 제목·길이·채널·자막 유무·챕터 확인 (내려받지 않음)
$PY $DL subs  "<URL>" -l ko -o work          # 자막 → work/<제목> [id].(txt|srt|md|json). 자막 없으면 종료코드 3
$PY $DL audio "<URL>" -o work                # 음성 m4a → whisper-transcribe 입력
$PY $DL video "<URL>" -o work --max-height 720
```

자주 쓰는 옵션:

- `-l ko,en` 자막 언어 우선순위. `ko`가 없으면 `en`을 찾는다. `--manual-only`면 자동 생성 자막은 쓰지 않는다.
- `--range 00:10:00-00:25:00` 그 구간만 받는다(긴 영상에서 필요한 부분만, ffmpeg 필요).
- `--audio-format mp3|wav|opus` 음성 포맷 변경(기본 m4a, whisper엔 m4a로 충분).
- `--playlist` 재생목록 전체. 기본은 URL이 재생목록이어도 첫 영상만 받는다.
- `--cookies-from-browser chrome` 로그인·멤버십·연령 제한 영상. 사용자 PC에서만 의미가 있고 클라우드에서는 쓸 수 없다.
- `convert <자막파일>` 이미 가진 `.srt/.vtt`를 같은 전사본 형식으로 바꾼다(네트워크 불필요).

출력 파일 옆에 `<제목> [id].meta.json`(원본 URL·제목·채널·업로드일·챕터)이 함께 생긴다. 노트의 출처 표기에 쓴다.

### 긴 영상은 백그라운드로

1시간이 넘는 영상은 백그라운드로 돌리고 PID로 끝났는지 확인한다. `pgrep -f`는 확인하는 명령줄 자체에 걸리므로 쓰지 않는다.

```bash
nohup $PY $DL audio "<URL>" -o work > work/dl.log 2>&1 & echo $! > work/dl.pid
# 대기: while kill -0 "$(cat work/dl.pid)" 2>/dev/null; do sleep 10; done; tail -3 work/dl.log
```

## 3. 다음 단계로 잇기

- **전사가 필요하면** `whisper-transcribe`: `transcribe.py "work/<제목> [id].m4a" -l ko -o work --prompt "<고유명사>"`.
- **노트가 필요하면** `consulting-notes`: `subs` 결과 `.json`이나 whisper `.json`을 `--transcript`로 넘긴다. 영상은 `source.engine`에 "YouTube 자동 자막" 또는 "Whisper large-v3-turbo"를 적고, 화자 구분은 없으므로 문맥 추정임을 표시한다.
- 영상 자체를 받은 경우 `--audio`에 `.mp4`를 넘겨도 된다(ffmpeg가 처리).

## 4. 한계와 주의

- **저작권·이용약관.** 내려받은 파일은 본인 업무상 참고(회의록·리서치 메모)에 쓴다. 재배포·재업로드하지 않는다. 플랫폼 약관상 다운로드를 금지하는 콘텐츠는 사용자가 판단한다.
- 사이트가 봇 차단을 강화하면 최신 yt-dlp에서도 실패할 수 있다. 그때는 오류 메시지를 그대로 보고하고, 사용자 PC에서 `--cookies-from-browser`로 재시도하도록 안내한다.
- 라이브 방송 진행 중인 URL은 끝난 뒤 받는다.
- 자동 자막 전사본의 `end` 시각은 다음 자막 시작까지로 늘려 잡히므로 초 단위 정확도는 낮다. 출처 표기는 `start` 기준으로 한다.
- 지원 사이트 목록과 자주 쓰는 yt-dlp 원본 옵션은 `references/yt-dlp-options.md`.
