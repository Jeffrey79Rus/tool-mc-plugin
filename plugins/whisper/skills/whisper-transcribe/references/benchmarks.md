# 실측 기록과 문제 해결

## 실측 (2026-10, Claude Code 클라우드 컨테이너)

- 하드웨어: Intel Xeon 2.8GHz 4코어(AVX-512), RAM 15GB, GPU 없음
- 소프트웨어: faster-whisper 1.2.1, ctranslate2 4.8.3, av 16.1.0, compute_type int8
- 입력: 한국어 대면 회의 녹음 중 업무 논의 5분 구간(발화 밀도 높음)

| 설정 | 소요 | 실시간 대비 | 기본 설정 대비 텍스트 일치도 |
|---|---|---|---|
| large-v3-turbo, beam 5, VAD | 117.0초 | 2.56배 | 1.000 (기준) |
| large-v3-turbo, beam 1, VAD | 85.4초 | 3.51배 | 0.946 |
| large-v3-turbo, batch 8, beam 1 | 85.7초 | 3.50배 | 0.906 |
| large-v3-turbo, batch 8, beam 5 | 89.0초 | 3.37배 | 0.908 |
| small, beam 1, VAD | 45.2초 | 6.63배 | 0.856 |

- 일치도는 `difflib.SequenceMatcher` 비율이며, **어느 쪽이 더 정확한지를 뜻하지 않는다**(정답 대본과 비교하지 않음).
- 전체 38분 회의(beam 5)는 모델 다운로드 포함 12분 10초가 걸렸다.
- 모델 로딩: 첫 로딩 163초, 같은 세션 재로딩 7초(디스크 캐시 영향으로 추정).

## 공식 벤치마크 (SYSTRAN/faster-whisper README)

13분 오디오 기준. large-v2 · RTX 3070 Ti: fp16 beam 5 1분 3초, batch 8 17초. small · CPU(i7-12700K 8스레드): int8 1분 42초, int8 batch 8 51초.
출처: https://github.com/SYSTRAN/faster-whisper#benchmark

## 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `open() got an unexpected keyword argument 'metadata_errors'` | av 17 이상 | `pip install "av==16.1.0"` |
| `ProxyError: 403` (모델 다운로드) | huggingface.co 차단 | 허용 도메인에 `huggingface.co` 추가 |
| `CAS Client Error ... xethub.hf.co` 또는 `cdn.hf.co` | 모델 파일 서버 차단 | 허용 도메인에 `*.hf.co` 추가 |
| 백그라운드 작업이 끝나지 않는 것처럼 보임 | `pgrep -f`가 자기 명령줄에 매칭 | PID 파일 + `kill -0`으로 확인 |
| 고유명사가 엉뚱하게 나옴 | Whisper의 일반적 한계 | `--prompt`에 용어 나열, 노트 단계에서 용어 대응표 작성 |
