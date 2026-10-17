---
name: whisper-transcribe
description: 회의·인터뷰·강의 녹음(mp3, m4a, wav, mp4 등)을 faster-whisper로 받아써 txt·srt·md·json 전사본을 만드는 스킬. 한국어 포함 99개 언어. "받아쓰기", "전사해줘", "녹음 텍스트로", "transcribe", "STT", "음성 인식", "자막 만들어줘", "녹음 파일 정리" 요청이나 오디오·영상 파일이 주어졌을 때 사용. 전사 후 회의록·인터뷰노트가 필요하면 consulting-notes 스킬로 넘긴다.
---

# Whisper Transcribe — 녹음 받아쓰기

로컬에서 faster-whisper(OpenAI Whisper의 고속 구현)로 받아쓴다. 녹음은 외부 API로 보내지 않고, 모델 파일만 Hugging Face에서 한 번 내려받는다.

## 1. 환경 준비 (처음 한 번)

```bash
bash <이 스킬 폴더>/scripts/setup.sh      # 마지막 줄 "준비됨: <python 경로>"를 다음 단계에서 쓴다
```

- 이미 설치돼 있으면(클라우드 환경 설정 스크립트로 설치한 경우 포함) 바로 그 python 경로를 알려 준다. 없으면 `~/.cache/whisper-skill/venv`를 만들어 설치한다.

- Windows: `py -m pip install "faster-whisper==1.2.1" "av==16.1.0"` 후 `py <스킬 폴더>\scripts\transcribe.py ...`
- `ffmpeg`가 있어야 한다.
- **`av`는 16.x로 고정한다.** 17 이상이면 `open() got an unexpected keyword argument 'metadata_errors'` 오류가 난다.
- 첫 실행 때 모델을 내려받는다(`large-v3-turbo` 약 1.6GB). 네트워크가 막혀 있으면 아래 허용 도메인이 필요하다.

| 도메인 | 용도 |
|---|---|
| `huggingface.co` | 모델 메타데이터 |
| `*.hf.co` | 모델 파일 본체(`cas-server.xethub.hf.co`, `us.aws.cdn.hf.co` 등) |
| `pypi.org`, `files.pythonhosted.org` | 패키지 설치 |

Claude Code 클라우드 환경이면 환경 설정의 네트워크 액세스를 "사용자 지정"으로 두고 위 도메인을 추가한다(패키지 관리자 기본 목록 포함은 켠 채로). claude.ai 채팅의 코드 실행 환경은 외부 접속이 제한돼 모델을 받지 못할 수 있으니, 그때는 Claude Code(로컬·클라우드)나 Cowork에서 실행하도록 안내한다.

## 2. 실행

```bash
PY=<setup.sh가 알려 준 python 경로>
$PY <이 스킬 폴더>/scripts/transcribe.py <녹음 파일> -l ko -o <출력 폴더> --prompt "<고유명사, 회사명, 약어>"
```

- 언어를 알면 `-l ko`처럼 고정한다. 오감지가 줄어든다.
- `--prompt`에 회사명·제품명·약어를 넣으면 고유명사 인식이 나아질 수 있다.
- 출력: `.txt`(전문), `.srt`(자막), `.md`(타임스탬프 목록), `.json`(세그먼트, consulting-notes 입력).
- 업로드된 파일은 별도 작업 폴더로 복사해 처리한다.

### 긴 녹음은 백그라운드로

20분이 넘으면 백그라운드로 돌리고 PID로 끝났는지 확인한다. `pgrep -f`는 확인하는 명령줄 자체에 걸려 영원히 "실행 중"으로 보이므로 쓰지 않는다.

```bash
nohup $PY transcribe.py rec.m4a -l ko -o out > out/log.txt 2>&1 & echo $! > out/pid
# 대기 (백그라운드 명령으로): while kill -0 "$(cat out/pid)" 2>/dev/null; do sleep 10; done; tail -2 out/log.txt
```

## 3. 속도와 모델 선택

실측(4코어 Xeon CPU, int8, 한국어 회의 5분 구간, `references/benchmarks.md`):

| 설정 | 5분 처리 | 실시간 대비 | 비고 |
|---|---|---|---|
| `large-v3-turbo`, beam 5 (기본) | 117초 | 2.6배 | 품질 우선 |
| `large-v3-turbo`, `--beam-size 1` | 85초 | 3.5배 | 텍스트 95% 일치, 권장 절충안 |
| `--batch-size 8` (CPU) | 86~89초 | 3.4배 | 4코어 CPU에선 이득 없음 |
| `small`, beam 1 | 45초 | 6.6배 | 텍스트 86% 일치, 고유명사 많은 회의엔 비권장 |

- NVIDIA GPU가 있으면 `--device cuda --batch-size 16`. 공식 벤치마크(13분 오디오, RTX 3070 Ti)에서 배치 처리가 17초였다.
- 모델 첫 로딩은 디스크 상태에 따라 수십 초~수 분 걸린다. 여러 파일은 한 세션에서 이어 처리한다.

## 4. 한계와 보고

- **화자 구분을 하지 않는다.** 화자가 필요하면 consulting-notes 스킬에서 문맥으로 추정하고 추정임을 표시한다.
- 고유명사·숫자 오인식이 흔하다. 결과를 보고할 때 오인식이 의심되는 용어와 품질이 낮은 구간(잡담·겹치는 발화)을 함께 알린다.
- 정확도는 실제 녹음으로 확인하기 전에는 단정하지 않는다.
