#!/usr/bin/env python3
"""회의·인터뷰 녹음 → 텍스트 자동 받아쓰기 (faster-whisper 기반).

출력: <이름>.txt(전문) · .srt(자막) · .md(타임스탬프 회의록 초안) · .json(세그먼트, consulting-notes 스킬 입력)

사용 예:
  python transcribe.py meeting.m4a -l ko                          # 기본: large-v3-turbo, beam 5
  python transcribe.py meeting.m4a -l ko --beam-size 1            # CPU에서 약 27% 빠름
  python transcribe.py meeting.mp3 -l ko --prompt "IQVIA, CDD, M&A"  # 고유명사 힌트
  python transcribe.py meeting.wav --device cuda --batch-size 16  # NVIDIA GPU
"""
import argparse
import json
import time
from pathlib import Path


def fmt(t: float, srt: bool = False) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{int(round((t % 1) * 1000)) % 1000:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("audio", help="오디오/비디오 파일 (mp3, m4a, wav, mp4 등 ffmpeg 지원 포맷)")
    p.add_argument("-m", "--model", default="large-v3-turbo", help="tiny|base|small|medium|large-v3|large-v3-turbo")
    p.add_argument("-l", "--language", default=None, help="언어 코드(ko, en, ja ...). 생략 시 자동 감지")
    p.add_argument("-o", "--out", default=None, help="출력 폴더 (기본: 입력 파일과 같은 폴더)")
    p.add_argument("--device", default="auto", help="auto|cpu|cuda")
    p.add_argument("--compute-type", default="auto", help="auto|int8|float16 (CPU는 int8이 자동 선택됨)")
    p.add_argument("--beam-size", type=int, default=5, help="1이면 더 빠르고 정확도는 약간 낮아짐")
    p.add_argument("--batch-size", type=int, default=0, help=">0이면 배치 추론(GPU에서 효과 큼, 4코어 CPU에선 효과 없음)")
    p.add_argument("--no-vad", action="store_true", help="무음 구간 제거(VAD) 끄기")
    p.add_argument("--prompt", default=None, help="고유명사·전문용어 힌트 문구")
    a = p.parse_args()

    from faster_whisper import BatchedInferencePipeline, WhisperModel

    src = Path(a.audio)
    out = Path(a.out) if a.out else src.parent
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    model = WhisperModel(a.model, device=a.device, compute_type=a.compute_type)
    print(f"모델 로딩 {time.time() - t0:.0f}초 ({a.model})", flush=True)
    opts = dict(language=a.language, initial_prompt=a.prompt, beam_size=a.beam_size)
    if a.batch_size > 0:
        segments, info = BatchedInferencePipeline(model=model).transcribe(str(src), batch_size=a.batch_size, **opts)
    else:
        segments, info = model.transcribe(str(src), vad_filter=not a.no_vad, **opts)
    print(f"감지 언어: {info.language} (확률 {info.language_probability:.2f}), 길이 {fmt(info.duration)}", flush=True)

    t1 = time.time()
    rows = []
    for i, s in enumerate(segments, 1):
        text = s.text.strip()
        rows.append({"id": i, "start": round(s.start, 3), "end": round(s.end, 3), "text": text})
        print(f"[{fmt(s.start)}] {text}", flush=True)

    stem = src.stem
    (out / f"{stem}.txt").write_text("\n".join(r["text"] for r in rows), encoding="utf-8")
    (out / f"{stem}.srt").write_text(
        "\n".join(f"{r['id']}\n{fmt(r['start'], True)} --> {fmt(r['end'], True)}\n{r['text']}\n" for r in rows), encoding="utf-8")
    (out / f"{stem}.md").write_text(
        "\n".join([f"# {stem} 받아쓰기\n"] + [f"- **[{fmt(r['start'])}]** {r['text']}" for r in rows]), encoding="utf-8")
    meta = {"source": src.name, "language": info.language, "duration": round(info.duration, 3),
            "model": a.model, "beam_size": a.beam_size, "segments": rows}
    (out / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    dt = time.time() - t1
    print(f"전사 {dt:.0f}초 (실시간 대비 {info.duration / max(dt, 1):.1f}배) · 저장: {out}/{stem}.(txt|srt|md|json)", flush=True)


if __name__ == "__main__":
    main()
