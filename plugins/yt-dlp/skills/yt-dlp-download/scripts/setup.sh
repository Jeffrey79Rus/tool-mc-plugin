#!/usr/bin/env bash
# yt-dlp 실행 환경 준비 (macOS·Linux). 이미 준비돼 있으면 아무것도 하지 않는다.
# 결과: 마지막 줄 "준비됨: <python 경로>" 의 python으로 download.py를 실행한다
set -euo pipefail
YTDLP_VENV="${YTDLP_VENV:-$HOME/.cache/yt-dlp-skill/venv}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg 없음 — 음성 추출·포맷 합치기에 필요. macOS: brew install ffmpeg / Ubuntu: sudo apt-get install -y ffmpeg" >&2
fi

# 시스템 Python에 이미 설치돼 있으면(클라우드 환경 설정 스크립트 등) 그대로 쓴다
for SYS_PY in python3 python; do
  if command -v "$SYS_PY" >/dev/null 2>&1 && "$SYS_PY" -c "import yt_dlp" 2>/dev/null; then
    echo "준비됨: $(command -v "$SYS_PY")"
    exit 0
  fi
done

if [ -x "$YTDLP_VENV/bin/python" ] && "$YTDLP_VENV/bin/python" -c "import yt_dlp" 2>/dev/null; then
  echo "준비됨: $YTDLP_VENV/bin/python"
  exit 0
fi

PY="$(command -v python3 || command -v python)"
"$PY" -m venv "$YTDLP_VENV"
"$YTDLP_VENV/bin/pip" install -q --upgrade yt-dlp
"$YTDLP_VENV/bin/python" -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"
echo "준비됨: $YTDLP_VENV/bin/python"
