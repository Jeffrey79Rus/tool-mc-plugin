#!/usr/bin/env python3
"""yt-dlp 래퍼: 영상·음성·자막 내려받기 (YouTube 등 yt-dlp 지원 사이트 전부)

모드:
  info   <URL>              제목·길이·채널·자막 유무·챕터를 확인한다 (내려받지 않음)
  audio  <URL>              음성만 m4a로 받는다 → whisper-transcribe 입력 (기본 모드)
  video  <URL>              영상을 mp4로 받는다 (기본 1080p 이하)
  subs   <URL>              자막을 받아 whisper와 같은 형식(.txt/.srt/.md/.json)으로 바꾼다 (전사 생략 가능)
  convert <자막파일>         이미 가진 .srt/.vtt 자막을 같은 형식으로 바꾼다 (네트워크 불필요)

사용 예:
  python download.py info  "https://www.youtube.com/watch?v=..."
  python download.py audio "https://youtu.be/..." -o work
  python download.py subs  "https://youtu.be/..." -l ko -o work        # 자막 없으면 종료코드 3
  python download.py video "https://youtu.be/..." --max-height 720
  python download.py audio "URL" --range 00:10:00-00:25:00              # 구간만 (ffmpeg 필요)
  python download.py audio "URL" --cookies-from-browser chrome          # 로그인 필요한 영상
"""
import argparse
import json
import re
import sys
from pathlib import Path

NAME_TMPL = "%(title).80s [%(id)s].%(ext)s"


def fmt(t: float, srt: bool = False) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{int(round((t % 1) * 1000)) % 1000:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_ts(s: str) -> float:
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    parts = [float(x) for x in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


# ---------- 자막 → 세그먼트 ----------

def parse_subtitle(path: Path) -> list[dict]:
    """srt/vtt를 (start, end, text) 목록으로. 태그 제거, 자동자막의 겹치는 반복 줄 제거."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\r", "")
    blocks = re.split(r"\n\s*\n", raw.strip())
    rows = []
    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip()]
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        m = re.match(r"\s*([\d:.,]+)\s*-->\s*([\d:.,]+)", lines[ti])
        if not m:
            continue
        start, end = parse_ts(m.group(1)), parse_ts(m.group(2))
        text = " ".join(lines[ti + 1:])
        text = re.sub(r"<[^>]+>", "", text)            # <c>, <00:00:01.000>, <i> 등
        text = re.sub(r"\{\\[^}]*\}", "", text)        # ass 스타일 태그
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text in ("[Music]", "[음악]", "[박수]", "[Applause]"):
            continue
        rows.append([start, end, text])

    # 자동자막은 같은 문장이 두 블록에 걸쳐 반복된다 → 앞 블록에 포함된 텍스트는 버린다
    out: list[list] = []
    for start, end, text in rows:
        if out:
            prev = out[-1]
            if text == prev[2] or (text in prev[2]):
                prev[1] = max(prev[1], end)
                continue
            if prev[2] in text and len(prev[2]) > 3:   # 앞 블록이 뒤 블록의 앞부분이면 합친다
                prev[1], prev[2] = max(prev[1], end), text
                continue
        out.append([start, end, text])
    return [{"id": i, "start": round(s, 3), "end": round(e, 3), "text": t} for i, (s, e, t) in enumerate(out, 1)]


def write_transcript(rows: list[dict], out: Path, stem: str, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.txt").write_text("\n".join(r["text"] for r in rows), encoding="utf-8")
    (out / f"{stem}.srt").write_text(
        "\n".join(f"{r['id']}\n{fmt(r['start'], True)} --> {fmt(r['end'], True)}\n{r['text']}\n" for r in rows), encoding="utf-8")
    (out / f"{stem}.md").write_text(
        "\n".join([f"# {stem} 자막\n"] + [f"- **[{fmt(r['start'])}]** {r['text']}" for r in rows]), encoding="utf-8")
    data = dict(meta)
    data["segments"] = rows
    (out / f"{stem}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    dur = rows[-1]["end"] if rows else 0
    print(f"세그먼트 {len(rows)}개, 마지막 {fmt(dur)} · 저장: {out}/{stem}.(txt|srt|md|json)", flush=True)


# ---------- yt-dlp ----------

def base_opts(a) -> dict:
    opts = {
        "outtmpl": str(Path(a.out) / NAME_TMPL),
        "noplaylist": not a.playlist,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "quiet": False,
        "no_warnings": False,
        "retries": 5,
        "fragment_retries": 5,
    }
    if a.cookies_from_browser:
        opts["cookiesfrombrowser"] = (a.cookies_from_browser,)
    if a.cookies:
        opts["cookiefile"] = a.cookies
    if a.proxy:
        opts["proxy"] = a.proxy
    if a.range:
        from yt_dlp.utils import download_range_func
        s, e = a.range.split("-", 1)
        opts["download_ranges"] = download_range_func(None, [(parse_ts(s), parse_ts(e) if e else float("inf"))])
        opts["force_keyframes_at_cuts"] = True
    return opts


def pick_meta(info: dict) -> dict:
    return {
        "source": info.get("webpage_url") or info.get("original_url"),
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "upload_date": info.get("upload_date"),
        "duration": info.get("duration"),
        "chapters": [{"title": c.get("title"), "start": c.get("start_time"), "end": c.get("end_time")}
                     for c in (info.get("chapters") or [])],
    }


def cmd_info(a) -> int:
    import yt_dlp
    opts = base_opts(a)
    opts.update({"skip_download": True, "quiet": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(a.url, download=False)
    entries = info.get("entries") if info.get("_type") == "playlist" else [info]
    for e in entries:
        if not e:
            continue
        m = pick_meta(e)
        m["subtitles"] = sorted((e.get("subtitles") or {}).keys())
        m["auto_captions"] = sorted(k for k in (e.get("automatic_captions") or {}).keys() if "-" not in k or k.endswith("-orig"))
        print(json.dumps(m, ensure_ascii=False, indent=1))
        print(f"\n제목: {m['title']}\n채널: {m['channel']}  업로드: {m['upload_date']}  길이: {fmt(m['duration'] or 0)}")
        print(f"수동 자막: {', '.join(m['subtitles']) or '없음'}")
        print(f"자동 자막: {', '.join(m['auto_captions'][:12]) or '없음'}{' …' if len(m['auto_captions']) > 12 else ''}")
        if m["chapters"]:
            print("챕터: " + " / ".join(f"[{fmt(c['start'] or 0)}] {c['title']}" for c in m["chapters"][:20]))
    return 0


def cmd_audio(a) -> int:
    import yt_dlp
    opts = base_opts(a)
    opts.update({
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": a.audio_format, "preferredquality": "0"}],
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(a.url, download=True)
    return _report(a, info, a.audio_format)


def cmd_video(a) -> int:
    import yt_dlp
    opts = base_opts(a)
    opts.update({
        "format": f"bv*[height<={a.max_height}][ext=mp4]+ba[ext=m4a]/bv*[height<={a.max_height}]+ba/b[height<={a.max_height}]/b",
        "merge_output_format": "mp4",
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(a.url, download=True)
    return _report(a, info, "mp4")


def _report(a, info: dict, ext: str) -> int:
    entries = info.get("entries") if info.get("_type") == "playlist" else [info]
    for e in entries:
        if not e:
            continue
        rd = (e.get("requested_downloads") or [{}])[0]
        path = rd.get("filepath") or rd.get("filename") or e.get("filepath") or ""
        if path and not path.endswith("." + ext):
            cand = Path(path).with_suffix("." + ext)
            if cand.exists():
                path = str(cand)
        meta = pick_meta(e)
        meta["file"] = Path(path).name if path else None
        if path:
            Path(path).with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {path}\n제목: {meta['title']}  길이: {fmt(meta['duration'] or 0)}  채널: {meta['channel']}")
    return 0


def cmd_subs(a) -> int:
    import yt_dlp
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    langs = [l.strip() for l in a.language.split(",") if l.strip()]
    opts = base_opts(a)
    opts.update({"skip_download": True, "quiet": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(a.url, download=False)
    if info.get("_type") == "playlist":
        print("subs 모드는 영상 하나씩 처리한다. 재생목록이면 개별 URL을 넘겨 달라.", file=sys.stderr)
        return 2
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}

    chosen, kind = None, None
    for l in langs:
        for key in (l, f"{l}-orig"):
            if key in manual:
                chosen, kind = key, "manual"
                break
        if chosen:
            break
    if not chosen and not a.manual_only:
        for l in langs:
            for key in (f"{l}-orig", l):
                if key in auto:
                    chosen, kind = key, "auto"
                    break
            if chosen:
                break
    if not chosen:
        print(f"요청한 언어({a.language}) 자막 없음. 수동: {sorted(manual) or '없음'} / 자동: "
              f"{sorted(k for k in auto if '-' not in k)[:15] or '없음'}\n→ audio 모드로 받아 whisper-transcribe로 전사한다.", file=sys.stderr)
        return 3

    stem_tmpl = str(out / "%(title).80s [%(id)s]")
    dl = base_opts(a)
    dl.update({
        "skip_download": True, "quiet": True,
        "writesubtitles": kind == "manual", "writeautomaticsub": kind == "auto",
        "subtitleslangs": [chosen], "subtitlesformat": "vtt/srt/best",
        "outtmpl": stem_tmpl + ".%(ext)s",
    })
    with yt_dlp.YoutubeDL(dl) as ydl:
        info = ydl.extract_info(a.url, download=True)
    subs = (info.get("requested_subtitles") or {}).get(chosen) or {}
    path = subs.get("filepath")
    if not path or not Path(path).exists():
        cands = sorted(out.glob(f"*[{info['id']}].{chosen}.*"))
        if not cands:
            print("자막 파일을 찾지 못했다.", file=sys.stderr)
            return 4
        path = str(cands[0])
    rows = parse_subtitle(Path(path))
    meta = pick_meta(info)
    meta.update({"language": chosen.split("-")[0], "engine": f"{'수동 자막' if kind == 'manual' else '자동 자막'} ({chosen}, yt-dlp)",
                 "subtitle_kind": kind})
    stem = Path(path).name.split(f".{chosen}.")[0]
    write_transcript(rows, out, stem, meta)
    print(f"자막 종류: {meta['engine']}" + ("  ※ 자동 자막은 고유명사·숫자 오인식이 잦다. 중요한 문장은 영상으로 확인." if kind == "auto" else ""))
    return 0


def cmd_convert(a) -> int:
    src = Path(a.file)
    rows = parse_subtitle(src)
    stem = re.sub(r"\.[a-z]{2,3}(-[A-Za-z]+)?$", "", src.stem)   # name.ko.srt → name
    meta = {"source": src.name, "language": a.language.split(",")[0] if a.language else None,
            "engine": "자막 변환 (yt-dlp-download convert)"}
    write_transcript(rows, Path(a.out) if a.out else src.parent, stem, meta)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["info", "audio", "video", "subs", "convert"])
    p.add_argument("url", help="영상/재생목록 URL (convert 모드에서는 .srt/.vtt 파일 경로)")
    p.add_argument("-o", "--out", default=".", help="출력 폴더 (기본: 현재 폴더)")
    p.add_argument("-l", "--language", default="ko,en", help="자막 언어 우선순위, 쉼표 구분 (기본 ko,en)")
    p.add_argument("--manual-only", action="store_true", help="자동 생성 자막은 쓰지 않는다")
    p.add_argument("--audio-format", default="m4a", help="audio 모드 출력 포맷: m4a|mp3|wav|opus (기본 m4a)")
    p.add_argument("--max-height", type=int, default=1080, help="video 모드 최대 세로 해상도 (기본 1080)")
    p.add_argument("--range", default=None, help="구간만 받기 HH:MM:SS-HH:MM:SS (ffmpeg 필요)")
    p.add_argument("--playlist", action="store_true", help="재생목록 URL이면 전체를 받는다 (기본: 첫 영상만)")
    p.add_argument("--cookies-from-browser", default=None, help="chrome|firefox|safari|edge … 로그인 필요한 영상")
    p.add_argument("--cookies", default=None, help="cookies.txt 파일 경로")
    p.add_argument("--proxy", default=None, help="프록시 URL")
    a = p.parse_args()
    if a.mode == "convert":
        a.file = a.url
        return cmd_convert(a)
    return {"info": cmd_info, "audio": cmd_audio, "video": cmd_video, "subs": cmd_subs}[a.mode](a)


if __name__ == "__main__":
    sys.exit(main())
