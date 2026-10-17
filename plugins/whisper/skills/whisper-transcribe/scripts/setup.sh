#!/usr/bin/env bash
# faster-whisper 실행 환경 준비 (macOS·Linux). 이미 준비돼 있으면 아무것도 하지 않는다.
# 결과: 마지막 줄 "준비됨: <python 경로>" 의 python으로 transcribe.py를 실행한다
set -euo pipefail
WHISPER_VENV="${WHISPER_VENV:-$HOME/.cache/whisper-skill/venv}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg 없음 — macOS: brew install ffmpeg / Ubuntu: sudo apt-get install -y ffmpeg" >&2
fi

# 클라우드 환경 설정 스크립트 등으로 시스템 Python에 이미 설치돼 있으면 그대로 쓴다
for SYS_PY in python3 python; do
  if command -v "$SYS_PY" >/dev/null 2>&1 && "$SYS_PY" -c "import faster_whisper, av, sys; sys.exit(0 if int(av.__version__.split('.')[0]) < 17 else 1)" 2>/dev/null; then
    echo "준비됨: $(command -v "$SYS_PY")"
    exit 0
  fi
done

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
