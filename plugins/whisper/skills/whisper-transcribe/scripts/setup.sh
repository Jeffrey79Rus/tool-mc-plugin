#!/usr/bin/env bash
# faster-whisper 실행 환경 준비 (macOS·Linux). 이미 준비돼 있으면 아무것도 하지 않는다.
# 결과: $WHISPER_VENV/bin/python 으로 transcribe.py 실행 가능
set -euo pipefail
WHISPER_VENV="${WHISPER_VENV:-$HOME/.cache/whisper-skill/venv}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg 없음 — macOS: brew install ffmpeg / Ubuntu: sudo apt-get install -y ffmpeg" >&2
fi

if [ -x "$WHISPER_VENV/bin/python" ] && "$WHISPER_VENV/bin/python" -c "import faster_whisper, av" 2>/dev/null; then
  echo "준비됨: $WHISPER_VENV/bin/python"
  exit 0
fi

PY="$(command -v python3 || command -v python)"
"$PY" -m venv "$WHISPER_VENV"
# av 17 이상은 faster-whisper 1.2.1과 호환되지 않는다 (decode_audio의 metadata_errors 인자 오류)
"$WHISPER_VENV/bin/pip" install -q "faster-whisper==1.2.1" "av==16.1.0"
"$WHISPER_VENV/bin/python" -c "import faster_whisper; print('faster-whisper', faster_whisper.__version__)"
echo "준비됨: $WHISPER_VENV/bin/python"
