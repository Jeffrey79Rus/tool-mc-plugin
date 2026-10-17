#!/usr/bin/env python3
"""컨설팅 노트(JSON) + 전사본(JSON/SRT) → 출처 연결·팩트체크된 단일 HTML.

사용 예:
  # 1) 팩트체크 리포트만 생성 (각 항목의 텍스트와 인용 원문을 나란히 출력)
  python build_note.py --note note.json --transcript meeting.json --check-only --report factcheck.md
  # 2) 최종 빌드 (Artifact 게시용 조각 HTML). --strict 는 미검증·미해결 항목이 있으면 실패
  python build_note.py --note note.json --transcript meeting.json --out note.html --strict
  # 3) 파일로 공유할 때는 --standalone (완전한 HTML 문서)
"""
import argparse
import html
import json
import re
import sys
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
def as_item(x) -> dict:
    return x if isinstance(x, dict) else {"text": str(x)}


def collect_items(note: dict) -> list[tuple[str, dict]]:
    out = []
    es = note.get("executive_summary", {})
    if es.get("headline"):
        es["headline"] = as_item(es["headline"]); out.append(("Executive Summary · 결론", es["headline"]))
    for i, p in enumerate(es.get("points", [])):
        es["points"][i] = as_item(p); out.append((f"Executive Summary · {i + 1}", es["points"][i]))
    if es.get("so_what"):
        es["so_what"] = as_item(es["so_what"]); out.append(("Executive Summary · So-what", es["so_what"]))
    for ai, ag in enumerate(note.get("agenda", [])):
        for ii, it in enumerate(ag.get("items", [])):
            ag["items"][ii] = as_item(it); out.append((f"아젠다 {ai + 1} · {ii + 1}", ag["items"][ii]))
        if ag.get("implication"):
            ag["implication"] = as_item(ag["implication"]); out.append((f"아젠다 {ai + 1} · 시사점", ag["implication"]))
    for i, h in enumerate(note.get("hypotheses", [])):
        out.append((f"가설 {i + 1}", h))
    c = note.get("conclusion", {})
    if c.get("summary"):
        c["summary"] = as_item(c["summary"]); out.append(("결론 · 요약", c["summary"]))
    for i, it in enumerate(c.get("items", [])):
        c["items"][i] = as_item(it); out.append((f"결론 · {i + 1}", c["items"][i]))
    for i, t in enumerate(note.get("todos", [])):
        out.append((f"To-do {i + 1}", t))
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


# ── HTML 렌더링 ───────────────────────────────────────────────────────────
def e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


CHECK_BADGE = {
    "ok": '<span class="badge ok" title="전사 원문과 대조해 일치를 확인함">전사 일치</span>',
    "warn": '<span class="badge warn" title="원문 청취 또는 당사자 확인이 필요함">확인 필요</span>',
    None: '<span class="badge unk" title="팩트체크가 끝나지 않음">미검증</span>',
}


def render_sources(it: dict, tl: Timeline) -> str:
    """출처 칩 + 펼침 패널(인용 원문)."""
    groups = it.get("_groups", [])
    badge = CHECK_BADGE.get(it.get("check"), CHECK_BADGE[None])
    tags = badge
    if it.get("basis") == "I":
        tags += '<span class="tag" title="작성자의 해석·종합">해석</span>'
    elif it.get("basis") == "V":
        tags += '<span class="tag" title="별도 자료로 검증됨">검증 자료</span>'
    pid = f"q-{it['_id']}"
    chips = []
    for gi, (ref, segs) in enumerate(groups):
        first, last = segs[0], segs[-1]
        label = tl.fmt(first["start"]) + (f"–{tl.fmt(last['start'])}" if len(segs) > 1 and int(last["start"]) != int(first["start"]) else "")
        spk = first.get("speaker") or "미상"
        chips.append(f'<button type="button" class="cite" aria-expanded="false" aria-controls="{pid}" data-ref="{it["_id"]}-{gi}">'
                     f'<span class="cite-t">{e(label)}</span><span class="cite-s">{e(spk)}</span></button>')
    note = f'<p class="check-note">{e(it["note"])}</p>' if it.get("note") else ""
    panel = ""
    if groups:
        lines = []
        for gi, (ref, segs) in enumerate(groups):
            for s in segs:
                lines.append(f'<div class="qline" data-ref="{it["_id"]}-{gi}"><a class="qt" href="#s{s["id"]}" title="스크립트 원문에서 위치 보기">{tl.fmt(s["start"])}</a>'
                             f'<span class="qs">{e(s.get("speaker") or "미상")}</span><span class="qx">{e(s["text"])}</span></div>')
        panel = (f'<div class="quotes" id="{pid}" hidden><p class="quotes-h">전사 원문 (Whisper 자동 전사, 수정하지 않음)</p>'
                 + "".join(lines) + "</div>")
    return f'<div class="src-row">{tags}{"".join(chips)}</div>{note}{panel}'


def render_item(it: dict, tl: Timeline, cls: str = "item") -> str:
    return f'<li class="{cls}"><p class="item-text">{e(item_text(it))}</p>{render_sources(it, tl)}</li>'


def section(num: str, sid: str, title: str, sub: str, body: str) -> str:
    return (f'<section class="sec" id="{sid}"><header class="sec-h"><span class="sec-n">{num}</span>'
            f'<h2>{e(title)}</h2><span class="sec-sub">{e(sub)}</span></header>{body}</section>')


def render(note: dict, tl: Timeline, stats: dict, items: list) -> tuple[str, list]:
    t = note["type"]
    m = note.get("meta", {})
    src = m.get("source", {})
    nav = []
    parts = []

    # 헤더
    conf = m.get("confidentiality")
    eyebrow = TYPE_LABEL[t] + (f" · {conf}" if conf else "")
    parts.append(f'<header class="doc-h"><p class="eyebrow">{e(eyebrow)}</p><h1>{e(note["title"])}</h1>'
                 + (f'<p class="lede">{e(m["purpose"])}</p>' if m.get("purpose") else "") + "</header>")

    # 01 기본 정보
    rows = [("미팅명", note["title"]), ("목적", m.get("purpose")), ("일시", m.get("datetime")),
            ("형태·장소", m.get("format")), ("작성", " · ".join(x for x in [m.get("author"), m.get("written"), m.get("version")] if x)),
            ("보안 등급", conf)]
    if t == "interview":
        rows.insert(1, ("인터뷰이", m.get("interviewee")))
    dl = "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in rows if v)
    att = m.get("attendees", [])
    att_html = ""
    if att:
        att_html = ('<div class="tbl-wrap"><table class="att"><thead><tr><th>소속</th><th>성명</th><th>직책·역할</th></tr></thead><tbody>'
                    + "".join(f"<tr><td>{e(a.get('side'))}</td><td>{e(a.get('name'))}</td><td>{e(a.get('role'))}</td></tr>" for a in att)
                    + "</tbody></table></div>")
    src_line = " · ".join(x for x in [src.get("audio") and f"녹음 {src['audio']}", src.get("engine"),
                                       src.get("quality") and f"전사 품질 {src['quality']}",
                                       src.get("diarization") and f"화자 {src['diarization']}"] if x)
    src_html = f'<p class="src-line">소스: {e(src_line)}</p>' if src_line else ""
    parts.append(section("01", "info", "기본 정보", "Overview", f'<dl class="meta">{dl}</dl>{att_html}{src_html}'))
    nav.append(("info", "기본 정보"))

    # 02 Executive Summary
    es = note["executive_summary"]
    body = ""
    if es.get("headline"):
        body += f'<div class="headline"><p class="headline-t">{e(item_text(es["headline"]))}</p>{render_sources(es["headline"], tl)}</div>'
    if es.get("points"):
        label = "핵심 발견" if t == "interview" else "핵심 논의·합의"
        body += f'<h3 class="sub">{label}</h3><ol class="items">' + "".join(render_item(p, tl) for p in es["points"]) + "</ol>"
    if es.get("so_what"):
        body += f'<div class="sowhat"><p class="sowhat-l">So-what</p><p class="item-text">{e(item_text(es["so_what"]))}</p>{render_sources(es["so_what"], tl)}</div>'
    parts.append(section("02", "summary", "Executive Summary", "요약", body))
    nav.append(("summary", "요약"))

    # 03 아젠다 / Key Question
    body = ""
    for ai, ag in enumerate(note["agenda"], 1):
        kq = f'<p class="kq">{e(ag["question"])}</p>' if ag.get("question") else ""
        tm = f'<span class="ag-time">{e(ag["time"])}</span>' if ag.get("time") else ""
        imp = ""
        if ag.get("implication"):
            imp = f'<div class="impl"><p class="impl-l">시사점</p><p class="item-text">{e(item_text(ag["implication"]))}</p>{render_sources(ag["implication"], tl)}</div>'
        body += (f'<article class="agenda"><header class="ag-h"><span class="ag-n">{ai}</span><h3>{e(ag["title"])}</h3>{tm}</header>{kq}'
                 f'<ul class="items">' + "".join(render_item(it, tl) for it in ag.get("items", [])) + f"</ul>{imp}</article>")
    if t == "interview":
        parts.append(section("03", "body", "Key Question별 신규 정보", "Findings", body))
        nav.append(("body", "신규 정보"))
    else:
        parts.append(section("03", "body", "아젠다별 논의 내용", "Discussion", body))
        nav.append(("body", "논의 내용"))

    n = 4
    # 04 가설 검증 (인터뷰)
    if t == "interview":
        verdict = {"support": ("지지", "ok"), "refute": ("반박", "bad"), "neutral": ("중립·불명", "unk")}
        rows_h = ""
        for h in note.get("hypotheses", []):
            lab, c = verdict.get(h.get("verdict"), ("—", "unk"))
            rows_h += (f'<tr><td class="h-text">{e(h.get("h"))}</td><td><span class="pill {c}">{lab}</span></td>'
                       f'<td>{e(h.get("evidence"))}{render_sources(h, tl)}</td></tr>')
        body = ('<div class="tbl-wrap"><table class="hyp"><thead><tr><th>가설</th><th>판정</th><th>근거·출처</th></tr></thead><tbody>'
                + rows_h + "</tbody></table></div>")
        parts.append(section(f"{n:02d}", "hyp", "가설 검증", "Hypotheses", body)); nav.append(("hyp", "가설 검증")); n += 1

    # 결론
    c = note["conclusion"]
    body = ""
    if c.get("summary"):
        body += f'<div class="concl"><p class="item-text strong">{e(item_text(c["summary"]))}</p>{render_sources(c["summary"], tl)}</div>'
    if c.get("items"):
        label = c.get("items_label") or ("시사점" if t == "interview" else "결정사항")
        body += f'<h3 class="sub">{e(label)}</h3><ol class="items">' + "".join(render_item(it, tl) for it in c["items"]) + "</ol>"
    parts.append(section(f"{n:02d}", "conclusion", "결론", "Conclusion", body)); nav.append(("conclusion", "결론")); n += 1

    # To-do
    rows_t = ""
    for i, td in enumerate(note["todos"], 1):
        rows_t += (f'<tr><td class="num">{i}</td><td class="td-task">{e(td.get("task"))}{render_sources(td, tl)}</td>'
                   f'<td>{e(td.get("owner"))}</td><td>{e(td.get("due"))}</td><td>{e(td.get("status", "진행 전"))}</td></tr>')
    sub = "후속 확인·추가 질문" if t == "interview" else "Action Items"
    body = ('<div class="tbl-wrap"><table class="todo"><thead><tr><th>#</th><th>할 일 · 출처</th><th>담당</th><th>기한</th><th>상태</th></tr></thead><tbody>'
            + rows_t + "</tbody></table></div>")
    parts.append(section(f"{n:02d}", "todo", "To-do", sub, body)); nav.append(("todo", "To-do")); n += 1

    if note.get("open_issues"):
        body = '<ul class="items">' + "".join(render_item(it, tl) for it in note["open_issues"]) + "</ul>"
        parts.append(section(f"{n:02d}", "issues", "열린 이슈·리스크", "Open Issues", body)); nav.append(("issues", "열린 이슈")); n += 1

    # Appendix A — 전사·검증 메모
    gl = note.get("glossary", [])
    gl_html = ""
    if gl:
        gl_html = ('<h3 class="sub">전사 표기 대응표</h3><div class="tbl-wrap"><table class="gl"><thead><tr><th>전사 표기</th><th>올바른 표기(추정)</th></tr></thead><tbody>'
                   + "".join(f"<tr><td class=\"mono\">{e(g['heard'])}</td><td>{e(g['correct'])}</td></tr>" for g in gl) + "</tbody></table></div>")
    ex = note.get("excluded", [])
    ex_html = ""
    if ex:
        ex_html = ('<h3 class="sub">노트에서 제외한 구간</h3><ul class="plain">'
                   + "".join(f"<li><span class=\"mono\">{e(x['range'])}</span> {e(x['reason'])}{' — 공유본 스크립트에서 가림' if x.get('redact') else ''}</li>" for x in ex) + "</ul>")
    spk_note = note.get("speakers", {}).get("note") or ("화자 표시는 전사 엔진이 구분한 것이 아니라 문맥으로 추정한 것이다." if note.get("speakers", {}).get("inferred", True) else "")
    fc = (f'<p class="fc-sum"><span class="badge ok">전사 일치 {stats["ok"]}</span><span class="badge warn">확인 필요 {stats["warn"]}</span>'
          + (f'<span class="badge unk">미검증 {stats["unk"]}</span>' if stats["unk"] else "") + "</p>")
    body = (f'<p>노트의 모든 문장은 전사 원문과 대조했다. 출처 칩을 누르면 해당 시각의 원문이 펼쳐진다.</p>{fc}'
            + (f'<p class="muted">{e(spk_note)}</p>' if spk_note else "") + gl_html + ex_html)
    parts.append(section("A", "notes", "전사·검증 메모", "Appendix A", body)); nav.append(("notes", "검증 메모"))

    # Appendix B — 스크립트 원문 (토글)
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
                lines.append('<div class="sline redacted">— 업무와 무관한 구간이라 공유본에서 가렸다 —</div>')
            in_red = True
            continue
        in_red = False
        cls = "sline cited" if s["id"] in cited_ids else "sline"
        lines.append(f'<div class="{cls}" id="s{s["id"]}"><span class="st">{tl.fmt(s["start"])}</span>'
                     f'<span class="ss">{e(s.get("speaker") or "미상")}</span><span class="sx">{e(s["text"])}</span></div>')
    body = ('<div class="script-ph" id="script-placeholder"><p>스크립트 원문은 숨겨져 있다. 노트 안의 출처 칩으로 필요한 원문만 볼 수 있다.</p>'
            '<button type="button" class="btn" data-toggle-script aria-pressed="false">스크립트 원문 보기</button></div>'
            '<div id="script" hidden><p class="muted">Whisper 자동 전사 결과를 수정 없이 실었다. 밑줄 친 줄은 노트에서 인용한 발언이다.</p>'
            f'<div class="script">{"".join(lines)}</div></div>')
    parts.append(section("B", "appendix-script", "스크립트 원문", "Appendix B", body)); nav.append(("appendix-script", "스크립트"))
    return "".join(parts), nav


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--note", required=True)
    ap.add_argument("--transcript", required=True)
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

    body, nav = render(note, tl, stats, [it for _, it, _m in rows])
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
