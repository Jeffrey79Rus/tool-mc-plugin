#!/usr/bin/env python3
"""컨설팅 노트(JSON) + 전사본(JSON/SRT) → 출처 연결·팩트체크된 단일 HTML.

사용 예:
  # 1) 팩트체크 리포트만 생성 (각 항목의 텍스트와 인용 원문을 나란히 출력)
  python build_note.py --note note.json --transcript meeting.json --check-only --report factcheck.md
  # 2) 최종 빌드 (Artifact 게시용 조각 HTML). --strict 는 미검증·미해결 항목이 있으면 실패
  python build_note.py --note note.json --transcript meeting.json --out note.html --strict
  # 3) 파일로 공유할 때는 --standalone (완전한 HTML 문서)
  # 일시가 미상이면 --audio <녹음 파일> 의 업로드일을 날짜로 쓴다 (HTML에서 날짜 수정 가능)
"""
import argparse
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "note_template.html"

REQUIRED = {
    "meeting": ["meta", "executive_summary", "agenda", "conclusion", "todos"],
    "interview": ["meta", "executive_summary", "agenda", "hypotheses", "conclusion", "todos"],
}
TYPE_LABEL = {"meeting": "Meeting Note", "interview": "Interview Note"}


# ── 전사본 ────────────────────────────────────────────────────────────────
def ts_to_sec(s: str) -> float:
    sec = 0.0
    for p in s.strip().replace(",", ".").split(":"):
        sec = sec * 60 + float(p)
    return sec


def load_transcript(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        segs = data["segments"] if isinstance(data, dict) else data
        return [{"id": int(s.get("id", i + 1)), "start": float(s["start"]), "end": float(s["end"]),
                 "text": s["text"].strip(), "speaker": s.get("speaker")} for i, s in enumerate(segs)]
    if path.suffix.lower() in (".srt", ".vtt"):
        # SRT(Whisper·클로바노트) / VTT(Teams·Zoom). VTT의 <v 이름> 태그는 화자로 쓴다.
        segs = []
        for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
            lines = [x for x in block.strip().splitlines() if x.strip()]
            ti = next((i for i, x in enumerate(lines) if "-->" in x), None)
            if ti is None:
                continue
            a, b = [x.strip().split(" ")[0] for x in lines[ti].split("-->")]
            body = " ".join(lines[ti + 1:]).strip()
            m = re.match(r"<v\s+([^>]+)>(.*)", body)
            speaker = None
            if m:
                speaker, body = m.group(1).strip(), m.group(2)
            body = re.sub(r"</?[^>]+>", "", body).strip()
            sid = int(lines[0]) if ti == 1 and lines[0].strip().isdigit() else len(segs) + 1
            segs.append({"id": sid, "start": ts_to_sec(a), "end": ts_to_sec(b), "text": body, "speaker": speaker})
        return segs
    sys.exit(f"지원하지 않는 전사본 형식: {path.suffix} (.json, .srt, .vtt)")


class Timeline:
    def __init__(self, segs: list[dict]):
        self.segs = segs
        self.by_id = {s["id"]: s for s in segs}
        self.hours = bool(segs) and segs[-1]["end"] >= 3600

    def fmt(self, sec: float) -> str:
        sec = int(sec)
        h, r = divmod(sec, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if self.hours else f"{m:02d}:{s:02d}"

    def resolve(self, ref: str) -> list[dict]:
        """'#12', '#12-#20', 'mm:ss', 'h:mm:ss', 'mm:ss-mm:ss' → 세그먼트 목록."""
        ref = ref.strip().replace("–", "-").replace("~", "-")
        if ref.startswith("#"):
            if "-" in ref:
                a, b = [int(x.strip().lstrip("#")) for x in ref.split("-")]
                return [s for s in self.segs if a <= s["id"] <= b]
            s = self.by_id.get(int(ref[1:]))
            return [s] if s else []
        if "-" in ref:
            a, b = [ts_to_sec(x) for x in ref.split("-")]
            return [s for s in self.segs if a <= int(s["start"]) <= b]
        t = int(ts_to_sec(ref))
        exact = [s for s in self.segs if int(s["start"]) == t]
        if exact:
            return exact
        near = [s for s in self.segs if abs(s["start"] - t) <= 2]
        return near[:1]


def apply_speakers(tl: Timeline, cfg: dict) -> None:
    for r in cfg.get("ranges", []):
        a, b = ts_to_sec(r["from"]), ts_to_sec(r["to"])
        for s in tl.segs:
            if a <= int(s["start"]) <= b and not s.get("speaker"):
                s["speaker"] = r["name"]
    for ref, name in cfg.get("overrides", {}).items():
        for s in tl.resolve(ref):
            s["speaker"] = name


# ── 노트 항목 수집 ─────────────────────────────────────────────────────────
# Executive Summary 하위 블록: (키, 미팅노트 라벨, 인터뷰노트 라벨)
ES_BLOCKS = [
    ("background", "미팅 배경 및 목적", "인터뷰 배경 및 목적"),
    ("points", "주요 논의 내용", "핵심 발견"),
    ("schedule", "주요 일정 및 To-do", "후속 일정 및 To-do"),
]


def es_label(t: str, key: str) -> str:
    return next(m if t == "meeting" else i for k, m, i in ES_BLOCKS if k == key)


def as_item(x) -> dict:
    return x if isinstance(x, dict) else {"text": str(x)}


def agenda_groups(ag: dict) -> list[dict]:
    """아젠다 본문 = 그룹 없는 items(있으면 맨 앞) + 사안별 groups."""
    out = []
    if ag.get("items"):
        out.append({"label": None, "items": ag["items"]})
    out += ag.get("groups", [])
    return out


def collect_items(note: dict) -> list[tuple[str, dict]]:
    out = []
    t = note["type"]
    es = note.get("executive_summary", {})
    if es.get("headline"):
        es["headline"] = as_item(es["headline"]); out.append(("Executive Summary · 한 줄 결론", es["headline"]))
    for key, *_ in ES_BLOCKS:
        for i, p in enumerate(es.get(key, [])):
            es[key][i] = as_item(p); out.append((f"Executive Summary · {es_label(t, key)} {i + 1}", es[key][i]))
    for ai, ag in enumerate(note.get("agenda", [])):
        for g in agenda_groups(ag):
            pre = f"아젠다 {ai + 1}" + (f" · {g['label']}" if g["label"] else "")
            for ii, it in enumerate(g["items"]):
                g["items"][ii] = as_item(it); out.append((f"{pre} · {ii + 1}", g["items"][ii]))
        if ag.get("implication"):
            ag["implication"] = as_item(ag["implication"]); out.append((f"아젠다 {ai + 1} · 시사점", ag["implication"]))
    for i, h in enumerate(note.get("hypotheses", [])):
        out.append((f"가설 {i + 1}", h))
    c = note.get("conclusion", {})
    if c.get("summary"):
        c["summary"] = as_item(c["summary"]); out.append(("결론 · 요약", c["summary"]))
    for i, it in enumerate(c.get("items", [])):
        c["items"][i] = as_item(it); out.append((f"결론 · {i + 1}", c["items"][i]))
    for i, t_ in enumerate(note.get("todos", [])):
        out.append((f"To-do {i + 1}", t_))
    for i, it in enumerate(note.get("open_issues", [])):
        note["open_issues"][i] = as_item(it); out.append((f"열린 이슈 {i + 1}", note["open_issues"][i]))
    return out


def item_text(it: dict) -> str:
    return it.get("text") or it.get("task") or it.get("h") or ""


NUM_RE = re.compile(r"\d[\d,.]*")


def check_items(note: dict, tl: Timeline) -> tuple[list, list, list]:
    errors, warnings, rows = [], [], []
    for sec in REQUIRED[note["type"]]:
        if not note.get(sec):
            errors.append(f"필수 섹션 누락: {sec}")
    es = note.get("executive_summary") or {}
    for key, *_ in ES_BLOCKS:
        if es and not es.get(key):
            errors.append(f"Executive Summary 누락: {key} ({es_label(note['type'], key)})")
    if es.get("so_what"):
        warnings.append("executive_summary.so_what 은 더 이상 표시하지 않음 — 필요한 내용은 points 또는 결론으로 옮길 것")
    for n, (loc, it) in enumerate(collect_items(note), 1):
        it["_id"] = f"i{n}"
        refs = it.get("src", []) or []
        segs, unresolved = [], []
        for ref in refs:
            got = tl.resolve(ref)
            (segs.extend(got) if got else unresolved.append(ref))
        seen, uniq = set(), []
        for s in segs:
            if s["id"] not in seen:
                seen.add(s["id"]); uniq.append(s)
        it["_segs"] = uniq
        it["_groups"] = [(ref, tl.resolve(ref)) for ref in refs if tl.resolve(ref)]
        basis = it.get("basis") or ("S" if refs else "I")
        it["basis"] = basis
        for ref in unresolved:
            errors.append(f"[{loc}] 전사본에서 찾을 수 없는 출처: {ref}")
        if not refs and basis != "I":
            errors.append(f"[{loc}] 출처(src) 없음 — 출처를 달거나 basis를 'I'(작성자 해석)로 표시")
        cited = re.sub(r",", "", " ".join(s["text"] for s in uniq))
        missing = []
        for num in NUM_RE.findall(item_text(it)):
            key = num.replace(",", "").rstrip(".")
            if refs and key and key not in cited:
                missing.append(num)
        if missing:
            warnings.append(f"[{loc}] 인용 원문에 없는 숫자: {', '.join(missing)}")
        if it.get("check") not in ("ok", "warn"):
            warnings.append(f"[{loc}] 팩트체크 미완료 (check 값 없음)")
        rows.append((loc, it, missing))
    return errors, warnings, rows


def write_report(path: Path, rows, tl: Timeline, errors, warnings) -> None:
    out = ["# 팩트체크 리포트", "",
           "각 항목의 노트 문장과 인용된 전사 원문을 비교하고, note.json의 `check`(ok/warn)와 `note`를 채운다.", ""]
    if errors:
        out += ["## 오류", *[f"- {e}" for e in errors], ""]
    if warnings:
        out += ["## 경고", *[f"- {w}" for w in warnings], ""]
    out.append("## 항목별 대조")
    for loc, it, missing in rows:
        out += ["", f"### {loc} — 현재 check: `{it.get('check', '미검증')}`",
                f"**노트**: {item_text(it)}"]
        if missing:
            out.append(f"**원문에 없는 숫자**: {', '.join(missing)}")
        if not it["_segs"]:
            out.append("_(인용 없음 — 작성자 해석)_")
        for s in it["_segs"]:
            out.append(f"> [{tl.fmt(s['start'])}] {s.get('speaker') or '미상'}: {s['text']}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── 일시 ──────────────────────────────────────────────────────────────────
DATE_RE = re.compile(r"(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})")
UNKNOWN_RE = re.compile(r"미상|\[?확인 필요\]?|unknown", re.I)


def resolve_date(meta: dict, audio: str | None) -> tuple[str, str, str]:
    """(YYYY-MM-DD, 시각 문구, 추정 근거). 근거가 빈 문자열이면 확정된 날짜."""
    raw = meta.get("datetime") or ""
    if meta.get("date"):
        return meta["date"], meta.get("time", ""), ""
    m = DATE_RE.search(raw)
    rest = UNKNOWN_RE.sub("", DATE_RE.sub("", raw)).strip(" ·,")
    if m:
        return date(*map(int, m.groups())).isoformat(), meta.get("time") or rest, ""
    if audio and Path(audio).exists():
        d = datetime.fromtimestamp(Path(audio).stat().st_mtime).date()
        return d.isoformat(), meta.get("time") or rest, "녹음 파일 업로드일 기준"
    up = (meta.get("source") or {}).get("uploaded")
    if up and DATE_RE.search(up):
        return date(*map(int, DATE_RE.search(up).groups())).isoformat(), meta.get("time") or rest, "녹음 파일 업로드일 기준"
    return date.today().isoformat(), meta.get("time") or rest, "노트 작성일 기준"


WEEKDAY = "월화수목금토일"


def date_label(iso: str) -> str:
    return f"{iso} ({WEEKDAY[date.fromisoformat(iso).weekday()]})"


# ── HTML 렌더링 ───────────────────────────────────────────────────────────
def e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


CHECK_BADGE = {
    "ok": '<span class="badge ok" title="전사 원문과 대조해 일치를 확인함">전사 일치</span>',
    "warn": '<span class="badge warn" title="원문 청취 또는 당사자 확인이 필요함">확인 필요</span>',
    None: '<span class="badge unk" title="팩트체크가 끝나지 않음">미검증</span>',
}
BASIS_TAG = {"I": ("해석", "작성자의 해석·종합"), "V": ("검증 자료", "별도 자료로 검증됨")}


def group_label(segs: list, tl: Timeline) -> str:
    first, last = segs[0], segs[-1]
    return tl.fmt(first["start"]) + (f"–{tl.fmt(last['start'])}" if int(last["start"]) != int(first["start"]) else "")


def basis_tag(it: dict) -> str:
    if it.get("basis") in BASIS_TAG:
        lab, tip = BASIS_TAG[it["basis"]]
        return f' <span class="tag" title="{tip}">{lab}</span>'
    return ""


def render_rail(it: dict, tl: Timeline) -> str:
    """우측 검증 레일: 메모 · 판정 · 원문 버튼. 칸 위치가 항목마다 같도록 빈 칸도 자리를 지킨다."""
    iid = it["_id"]
    memo = (f'<button type="button" class="rbtn memo-btn" aria-expanded="false" aria-controls="m-{iid}" title="검토 메모 보기">'
            f'<span class="dot" aria-hidden="true"></span>메모</button>') if it.get("note") else '<span class="slot"></span>'
    groups = it.get("_groups", [])
    if groups:
        more = f'<span class="more">+{len(groups) - 1}</span>' if len(groups) > 1 else ""
        spk = groups[0][1][0].get("speaker") or "미상"
        src = (f'<button type="button" class="rbtn src-btn" aria-expanded="false" aria-controls="q-{iid}" '
               f'title="전사 원문 보기 · {e(group_label(groups[0][1], tl))} {e(spk)}"><span class="cite-t">{tl.fmt(groups[0][1][0]["start"])}</span>{more}'
               f'<span class="chev" aria-hidden="true"></span></button>')
    else:
        src = '<span class="slot no-src" title="인용 원문 없음">—</span>'
    return f'<div class="rail">{memo}{CHECK_BADGE.get(it.get("check"), CHECK_BADGE[None])}{src}</div>'


def render_panels(it: dict, tl: Timeline) -> str:
    iid = it["_id"]
    out = ""
    if it.get("note"):
        out += f'<div class="memo" id="m-{iid}" hidden><p class="memo-l">검토 메모</p><p class="memo-t">{e(it["note"])}</p></div>'
    groups = it.get("_groups", [])
    if groups:
        gs = []
        for ref, segs in groups:
            lines = "".join(
                f'<div class="qline"><a class="qt" href="#s{s["id"]}" title="스크립트 원문에서 위치 보기">{tl.fmt(s["start"])}</a>'
                f'<span class="qs">{e(s.get("speaker") or "미상")}</span><span class="qx">{e(s["text"])}</span></div>' for s in segs)
            gs.append(f'<div class="qg">{lines}</div>')
        out += (f'<div class="quotes" id="q-{iid}" hidden><p class="quotes-h">전사 원문 · 자동 전사 그대로, 수정하지 않음</p>'
                + "".join(gs) + "</div>")
    return out


def render_item(it: dict, tl: Timeline, extra: str = "") -> str:
    return (f'<li class="it{extra}"><p class="item-text">{e(item_text(it))}{basis_tag(it)}</p>'
            f'{render_rail(it, tl)}{render_panels(it, tl)}</li>')


def item_list(items: list, tl: Timeline, ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    return f'<{tag} class="items">' + "".join(render_item(it, tl) for it in items) + f"</{tag}>"


def section(num: str, sid: str, title: str, sub: str, body: str) -> str:
    return (f'<section class="sec" id="{sid}"><header class="sec-h"><span class="sec-n">{num}</span>'
            f'<h2>{e(title)}</h2><span class="sec-sub">{e(sub)}</span></header>{body}</section>')


def render(note: dict, tl: Timeline, stats: dict, items: list, audio: str | None) -> tuple[str, list]:
    t = note["type"]
    m = note.get("meta", {})
    src = m.get("source", {})
    nav = []
    parts = []

    # 헤더
    conf = m.get("confidentiality")
    iso, time_txt, inferred = resolve_date(m, audio)
    eyebrow = f'<span>{e(TYPE_LABEL[t])}</span>' + (f'<span class="conf">{e(conf)}</span>' if conf else "")
    parts.append(f'<header class="doc-h"><p class="eyebrow">{eyebrow}</p><h1>{e(note["title"])}</h1>'
                 + (f'<p class="lede">{e(m["purpose"])}</p>' if m.get("purpose") else "") + "</header>")

    # 01 기본 정보
    hint = f'<span class="date-hint" id="date-hint">{e(inferred)} · 확인 필요</span>' if inferred else ""
    date_dd = (f'<dd class="date-dd"><span class="date-wrap" id="meet-date" data-key="{e(note["title"])}">'
               f'<time id="date-text" datetime="{iso}">{date_label(iso)}</time>'
               + (f' <span class="time-part">{e(time_txt)}</span>' if time_txt else "") + f"</span>{hint}"
               f'<span class="date-tools"><button type="button" class="link-btn" id="date-edit" aria-controls="date-input" aria-expanded="false">날짜 수정</button>'
               f'<input type="date" id="date-input" value="{iso}" aria-label="미팅 날짜" hidden>'
               f'<span class="date-status" id="date-status" aria-live="polite"></span></span></dd>')
    rows = [("미팅명", e(note["title"])), ("목적", e(m.get("purpose")) if m.get("purpose") else None), ("일시", None),
            ("형태·장소", e(m.get("format")) if m.get("format") else None),
            ("작성", e(" · ".join(x for x in [m.get("author"), m.get("written"), m.get("version")] if x)) or None),
            ("보안 등급", e(conf) if conf else None)]
    if t == "interview":
        rows.insert(1, ("인터뷰이", e(m.get("interviewee")) if m.get("interviewee") else None))
    dl = ""
    for k, v in rows:
        if k == "일시":
            dl += f'<div class="meta-date"><dt>일시</dt>{date_dd}</div>'
        elif v:
            dl += f"<div><dt>{e(k)}</dt><dd>{v}</dd></div>"
    att = m.get("attendees", [])
    att_html = ""
    if att:
        att_html = ('<div class="tbl-wrap"><table class="att"><thead><tr><th>소속</th><th>성명</th><th>직책·역할</th></tr></thead><tbody>'
                    + "".join(f"<tr><td>{e(a.get('side'))}</td><td>{e(a.get('name'))}</td><td>{e(a.get('role'))}</td></tr>" for a in att)
                    + "</tbody></table></div>")
    src_line = " · ".join(x for x in [src.get("audio") and f"녹음 {src['audio']}", src.get("engine"),
                                       src.get("quality") and f"전사 품질 {src['quality']}",
                                       src.get("diarization") and f"화자 {src['diarization']}"] if x)
    src_html = f'<p class="src-line">소스 · {e(src_line)}</p>' if src_line else ""
    parts.append(section("01", "info", "기본 정보", "Overview", f'<dl class="meta">{dl}</dl>{att_html}{src_html}'))
    nav.append(("info", "기본 정보"))

    # 02 Executive Summary
    es = note["executive_summary"]
    body = ""
    if es.get("headline"):
        h = es["headline"]
        body += (f'<div class="headline it"><p class="item-text">{e(item_text(h))}{basis_tag(h)}</p>'
                 f'{render_rail(h, tl)}{render_panels(h, tl)}</div>')
    blocks = ""
    for key, *_ in ES_BLOCKS:
        if es.get(key):
            blocks += f'<div class="es-row"><h3 class="es-l">{e(es_label(t, key))}</h3>{item_list(es[key], tl)}</div>'
    body += f'<div class="es">{blocks}</div>'
    parts.append(section("02", "summary", "Executive Summary", "요약", body))
    nav.append(("summary", "요약"))

    # 03 아젠다 / Key Question — 주제·사안별
    body = ""
    for ai, ag in enumerate(note["agenda"], 1):
        kq = f'<p class="kq"><span class="kq-l">KQ</span>{e(ag["question"])}</p>' if ag.get("question") else ""
        tm = f'<span class="ag-time">{e(ag["time"])}</span>' if ag.get("time") else ""
        groups = ""
        for g in agenda_groups(ag):
            lab = f'<h4 class="ag-gl">{e(g["label"])}</h4>' if g["label"] else ""
            groups += f'<div class="ag-group">{lab}{item_list(g["items"], tl)}</div>'
        imp = ""
        if ag.get("implication"):
            im = ag["implication"]
            imp = (f'<div class="impl"><p class="impl-l">시사점</p><ul class="items"><li class="it">'
                   f'<p class="item-text">{e(item_text(im))}{basis_tag(im)}</p>{render_rail(im, tl)}{render_panels(im, tl)}</li></ul></div>')
        body += (f'<article class="agenda"><header class="ag-h"><span class="ag-n">{ai}</span><h3>{e(ag["title"])}</h3>{tm}</header>'
                 f'{kq}{groups}{imp}</article>')
    if t == "interview":
        parts.append(section("03", "body", "Key Question별 신규 정보", "Findings", body))
        nav.append(("body", "신규 정보"))
    else:
        parts.append(section("03", "body", "아젠다별 논의 내용", "Discussion", body))
        nav.append(("body", "논의 내용"))

    n = 4
    # 가설 검증 (인터뷰)
    if t == "interview":
        verdict = {"support": ("지지", "ok"), "refute": ("반박", "bad"), "neutral": ("중립·불명", "unk")}
        rows_h = ""
        for h in note.get("hypotheses", []):
            lab, c = verdict.get(h.get("verdict"), ("—", "unk"))
            rows_h += (f'<tr><td class="h-text">{e(h.get("h"))}</td><td><span class="pill {c}">{lab}</span></td>'
                       f'<td class="cell-main">{e(h.get("evidence"))}{render_panels(h, tl)}</td><td class="rail-cell">{render_rail(h, tl)}</td></tr>')
        body = ('<div class="tbl-wrap"><table class="hyp"><thead><tr><th>가설</th><th>판정</th><th>근거</th><th class="th-rail">검증 · 원문</th></tr></thead><tbody>'
                + rows_h + "</tbody></table></div>")
        parts.append(section(f"{n:02d}", "hyp", "가설 검증", "Hypotheses", body)); nav.append(("hyp", "가설 검증")); n += 1

    # 결론
    c = note["conclusion"]
    body = ""
    if c.get("summary"):
        s = c["summary"]
        body += (f'<div class="concl it"><p class="item-text strong">{e(item_text(s))}{basis_tag(s)}</p>'
                 f'{render_rail(s, tl)}{render_panels(s, tl)}</div>')
    if c.get("items"):
        label = c.get("items_label") or ("시사점" if t == "interview" else "결정사항")
        body += f'<h3 class="sub">{e(label)}</h3>{item_list(c["items"], tl, ordered=True)}'
    parts.append(section(f"{n:02d}", "conclusion", "결론", "Conclusion", body)); nav.append(("conclusion", "결론")); n += 1

    # To-do
    rows_t = ""
    for i, td in enumerate(note["todos"], 1):
        rows_t += (f'<tr><td class="num">{i}</td><td class="cell-main">{e(td.get("task"))}{basis_tag(td)}{render_panels(td, tl)}</td>'
                   f'<td>{e(td.get("owner"))}</td><td>{e(td.get("due"))}</td><td>{e(td.get("status", "진행 전"))}</td>'
                   f'<td class="rail-cell">{render_rail(td, tl)}</td></tr>')
    sub = "후속 확인·추가 질문" if t == "interview" else "Action Items"
    body = ('<div class="tbl-wrap"><table class="todo"><thead><tr><th>#</th><th>할 일</th><th>담당</th><th>기한</th><th>상태</th>'
            '<th class="th-rail">검증 · 원문</th></tr></thead><tbody>' + rows_t + "</tbody></table></div>")
    parts.append(section(f"{n:02d}", "todo", "To-do", sub, body)); nav.append(("todo", "To-do")); n += 1

    if note.get("open_issues"):
        parts.append(section(f"{n:02d}", "issues", "열린 이슈·리스크", "Open Issues", item_list(note["open_issues"], tl)))
        nav.append(("issues", "열린 이슈")); n += 1

    # Appendix — 접힌 상태가 기본. A 전사·검증 메모 / B 스크립트 원문(토글)
    gl = note.get("glossary", [])
    gl_html = ""
    if gl:
        gl_html = ('<h4 class="sub">전사 표기 대응표</h4><div class="tbl-wrap"><table class="gl"><thead><tr><th>전사 표기</th><th>올바른 표기(추정)</th></tr></thead><tbody>'
                   + "".join(f"<tr><td class=\"mono\">{e(g['heard'])}</td><td>{e(g['correct'])}</td></tr>" for g in gl) + "</tbody></table></div>")
    ex = note.get("excluded", [])
    ex_html = ""
    if ex:
        ex_html = ('<h4 class="sub">노트에서 제외한 구간</h4><ul class="plain">'
                   + "".join(f"<li><span class=\"mono\">{e(x['range'])}</span> {e(x['reason'])}{' — 공유본 스크립트에서 가림' if x.get('redact') else ''}</li>" for x in ex) + "</ul>")
    spk_note = note.get("speakers", {}).get("note") or ("화자 표시는 전사 엔진이 구분한 것이 아니라 문맥으로 추정한 것임." if note.get("speakers", {}).get("inferred", True) else "")
    fc = (f'<p class="fc-sum"><span class="badge ok">전사 일치 {stats["ok"]}</span><span class="badge warn">확인 필요 {stats["warn"]}</span>'
          + (f'<span class="badge unk">미검증 {stats["unk"]}</span>' if stats["unk"] else "") + "</p>")
    appx_a = (f'<div class="appx-part" id="notes"><h3 class="appx-h"><span class="sec-n">A</span>전사·검증 메모</h3>'
              f'<p>노트의 모든 문장을 전사 원문과 대조함. 각 문장 오른쪽의 시각 버튼을 누르면 해당 원문이 펼쳐짐.</p>{fc}'
              + (f'<p class="muted">{e(spk_note)}</p>' if spk_note else "") + gl_html + ex_html + "</div>")

    redact = []
    for x in ex:
        if x.get("redact"):
            a, b = [ts_to_sec(v) for v in x["range"].replace("–", "-").split("-")]
            redact.append((a, b))
    cited_ids = {s["id"] for it in items for s in it.get("_segs", [])}
    lines, in_red = [], False
    for s in tl.segs:
        red = any(a <= int(s["start"]) <= b for a, b in redact)
        if red and s["id"] in cited_ids:
            red = False
        if red:
            if not in_red:
                lines.append('<div class="sline redacted">업무와 무관한 구간이라 공유본에서 가림</div>')
            in_red = True
            continue
        in_red = False
        cls = "sline cited" if s["id"] in cited_ids else "sline"
        lines.append(f'<div class="{cls}" id="s{s["id"]}"><span class="st">{tl.fmt(s["start"])}</span>'
                     f'<span class="ss">{e(s.get("speaker") or "미상")}</span><span class="sx">{e(s["text"])}</span></div>')
    appx_b = ('<div class="appx-part" id="appendix-script"><h3 class="appx-h"><span class="sec-n">B</span>스크립트 원문</h3>'
              '<div class="script-ph" id="script-placeholder"><p>스크립트 원문은 숨겨져 있음. 노트의 시각 버튼으로 필요한 원문만 볼 수 있음.</p>'
              '<button type="button" class="btn" data-toggle-script aria-pressed="false">스크립트 원문 보기</button></div>'
              '<div id="script" hidden><div class="script-top"><p class="muted">자동 전사 결과를 수정 없이 실음. 밑줄 친 줄은 노트에서 인용한 발언임.</p>'
              '<button type="button" class="btn" data-toggle-script aria-pressed="true">스크립트 숨기기</button></div>'
              f'<div class="script">{"".join(lines)}</div></div></div>')
    parts.append('<section class="sec appx" id="appendix"><details id="appx"><summary class="appx-sum">'
                 '<span class="sec-n">A–B</span><h2>Appendix</h2><span class="appx-desc">전사·검증 메모 · 스크립트 원문</span>'
                 '<span class="appx-act" aria-hidden="true"></span></summary>'
                 f'<div class="appx-body">{appx_a}{appx_b}</div></details></section>')
    nav.append(("appendix", "Appendix"))
    return "".join(parts), nav


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--note", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--audio", help="원본 녹음 파일. 일시가 미상이면 이 파일의 업로드(수정)일을 날짜로 씀")
    ap.add_argument("--out")
    ap.add_argument("--report", help="팩트체크 리포트(.md) 출력 경로")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--strict", action="store_true", help="오류 또는 미검증 항목이 있으면 실패")
    ap.add_argument("--standalone", action="store_true", help="파일 공유용 완전한 HTML 문서로 출력")
    a = ap.parse_args()

    note = json.loads(Path(a.note).read_text(encoding="utf-8"))
    if note.get("type") not in REQUIRED:
        sys.exit("note.type 은 'meeting' 또는 'interview' 여야 합니다.")
    tl = Timeline(load_transcript(Path(a.transcript)))
    apply_speakers(tl, note.get("speakers", {}))
    errors, warnings, rows = check_items(note, tl)

    stats = {"ok": 0, "warn": 0, "unk": 0}
    for _, it, _m in rows:
        stats[it.get("check") if it.get("check") in ("ok", "warn") else "unk"] += 1
    print(f"항목 {len(rows)}개 · 전사 일치 {stats['ok']} · 확인 필요 {stats['warn']} · 미검증 {stats['unk']}")
    for x in errors:
        print("오류:", x)
    for x in warnings:
        print("경고:", x)
    if a.report:
        write_report(Path(a.report), rows, tl, errors, warnings)
        print("리포트:", a.report)
    if a.check_only:
        sys.exit(1 if errors else 0)
    if errors or (a.strict and stats["unk"]):
        sys.exit("빌드 중단: 오류 또는 미검증 항목을 먼저 해결하세요.")
    if not a.out:
        sys.exit("--out 이 필요합니다.")

    body, nav = render(note, tl, stats, [it for _, it, _m in rows], a.audio)
    nav_html = "".join(f'<a href="#{sid}">{e(lab)}</a>' for sid, lab in nav)
    page = (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{TITLE}}", e(note.get("short_title") or note["title"]))
            .replace("{{NAV}}", nav_html)
            .replace("{{FC_OK}}", str(stats["ok"])).replace("{{FC_WARN}}", str(stats["warn"]))
            .replace("{{BODY}}", body))
    if a.standalone:
        page = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body>'
                + page + "</body></html>")
    Path(a.out).write_text(page, encoding="utf-8")
    print("HTML:", a.out)


if __name__ == "__main__":
    main()
