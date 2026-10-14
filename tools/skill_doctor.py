#!/usr/bin/env python3
"""skill_doctor — 내가 만든 Claude 스킬·플러그인이 '전역에서' 동작하는지 진단하고 고친다.

claude.ai에서 만든 스킬과 업로드한 플러그인은 계정에 묶여 claude.ai 채팅, Cowork, Claude Code
(데스크톱·CLI·웹)로 동기화된다. 파일은 같아도 표면마다 설치 경로, 도구, 런타임이 다르기 때문에
한 곳에서만 쓰던 가정이 다른 곳에서 깨진다. 이 스크립트는 그 가정들을 찾아낸다.

사용:
  python3 tools/skill_doctor.py audit  [--root DIR ...] [--all] [--json]
  python3 tools/skill_doctor.py fix    --out DIR [--root DIR ...]
  python3 tools/skill_doctor.py budget [--apply] [--settings PATH] [--value N]

  audit   진단 보고(기본: 내 스킬·내 업로드 플러그인만, --all이면 전체)
  fix     내 스킬·플러그인의 수정본을 DIR에 만들고 재업로드용 zip으로 묶는다(원본은 건드리지 않음)
  budget  스킬 목록 글자 예산을 계산하고, --apply면 settings.json의 env에 예산값을 기록한다

--root 없이 실행하면 아래를 자동 탐색한다.
  Claude Code/Cowork : ~/.claude/skills/synced/*, ~/.claude/plugins/synced/*, ~/.claude/skills/*
  claude.ai 컨테이너 : /mnt/skills/{user,plugins,public,examples}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()

# Claude Code 스킬 목록 예산(2.1.x 기준): 컨텍스트 토큰 × 4(bytes/token) × 0.01, 설명은 1,536자에서 자른다.
# SLASH_COMMAND_TOOL_CHAR_BUDGET 환경변수가 있으면 그 값을 쓴다.
BYTES_PER_TOKEN = 4
BUDGET_FRACTION = 0.01
MAX_DESC_CHARS = 1536
BUDGET_ENV = "SLASH_COMMAND_TOOL_CHAR_BUDGET"

# pptx 스킬 scripts 폴더를 표면 무관하게 찾는 셸 식 (claude.ai → Claude Code synced → 로컬 설치 순)
PPTX_SCRIPTS_EXPR = ('"$(ls -d /mnt/skills/public/pptx/scripts ~/.claude/skills/synced/*/pptx/scripts '
                     '~/.claude/skills/pptx/scripts 2>/dev/null | head -1)"')


# --------------------------------------------------------------------------- 모델
@dataclass
class Skill:
    name: str            # frontmatter name
    qualified: str       # Skill 도구에서 부르는 이름 (plugin:skill 또는 anthropic-skills:skill)
    path: Path           # SKILL.md가 있는 폴더
    description: str
    owner: str           # mine-skill | mine-plugin | anthropic | marketplace | local
    container: Path      # 패키징 단위 루트 (스킬 폴더 또는 플러그인 루트)
    plugin: str | None = None


@dataclass
class Finding:
    severity: str        # ERROR | WARN | INFO
    rule: str
    unit: str            # 스킬 또는 플러그인 이름
    file: str
    line: int
    message: str
    fixable: bool = False


@dataclass
class Inventory:
    skills: list[Skill] = field(default_factory=list)
    commands: list[tuple[str, str]] = field(default_factory=list)   # (qualified, description)
    roots: list[Path] = field(default_factory=list)


# --------------------------------------------------------------------------- 탐색
FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def frontmatter(text: str) -> dict:
    m = FM_RE.match(text)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if km:
            key, val = km.group(1), km.group(2).strip()
            if val in (">-", ">", "|", "|-"):
                val = ""
            out[key] = val.strip('"').strip("'")
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def default_roots() -> list[Path]:
    cands = [*glob.glob(str(HOME / ".claude/skills/synced/*")),
             *glob.glob(str(HOME / ".claude/plugins/synced/*")),
             str(HOME / ".claude/skills"),
             "/mnt/skills/user", "/mnt/skills/plugins", "/mnt/skills/public", "/mnt/skills/examples"]
    return [Path(c) for c in cands if Path(c).is_dir()]


def load_inventory(roots: list[Path]) -> Inventory:
    inv = Inventory(roots=roots)
    seen: set[Path] = set()
    for root in roots:
        manifest = {}
        mf = root / "manifest.json"
        if mf.is_file():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                manifest = {s["name"]: s for s in data.get("skills", []) if isinstance(s, dict) and "name" in s}
            except (ValueError, KeyError):
                pass

        # 플러그인: <root>/<dir>/.claude-plugin/plugin.json
        for pj in sorted(root.glob("*/.claude-plugin/plugin.json")):
            proot = pj.parent.parent
            if proot in seen:
                continue
            seen.add(proot)
            try:
                pname = json.loads(pj.read_text(encoding="utf-8")).get("name") or proot.name
            except ValueError:
                pname = proot.name
            meta = root / f"{proot.name}.meta.json"
            market = ""
            if meta.is_file():
                try:
                    market = json.loads(meta.read_text(encoding="utf-8")).get("marketplace_name", "")
                except ValueError:
                    pass
            owner = "mine-plugin" if market == "My Uploads" else "marketplace"
            for sk in sorted(proot.glob("skills/*/SKILL.md")):
                fm = frontmatter(sk.read_text(encoding="utf-8", errors="replace"))
                nm = fm.get("name") or sk.parent.name
                inv.skills.append(Skill(nm, f"{pname}:{nm}", sk.parent, fm.get("description", ""), owner, proot, pname))
            for cmd in sorted(proot.glob("commands/*.md")):
                fm = frontmatter(cmd.read_text(encoding="utf-8", errors="replace"))
                inv.commands.append((f"{pname}:{cmd.stem}", fm.get("description", "")))

        # 단독 스킬: <root>/<dir>/SKILL.md
        for sk in sorted(root.glob("*/SKILL.md")):
            sdir = sk.parent
            if sdir in seen or (sdir / ".claude-plugin").exists():
                continue
            seen.add(sdir)
            fm = frontmatter(sk.read_text(encoding="utf-8", errors="replace"))
            nm = fm.get("name") or sdir.name
            src = manifest.get(nm, {}).get("source")
            if src == "plugin" or str(root).startswith("/mnt/skills/user"):
                owner = "mine-skill"
            elif src in ("anthropic", "anthropic-example") or "/mnt/skills/public" in str(root) or "/mnt/skills/examples" in str(root):
                owner = "anthropic"
            else:
                owner = "local"
            qual = f"anthropic-skills:{nm}" if manifest else nm
            inv.skills.append(Skill(nm, qual, sdir, fm.get("description", ""), owner, sdir))
    return inv


def mine(inv: Inventory) -> list[Skill]:
    return [s for s in inv.skills if s.owner in ("mine-skill", "mine-plugin")]


# --------------------------------------------------------------------------- 규칙
SECRET_RES = [
    (re.compile(r"\b[0-9a-f]{40}\b"), "40자리 hex 키(예: OpenDART crtfc_key)"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "sk- 형식 API 키"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "GitHub 토큰"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "Google API 키"),
]
SURFACE_ONLY = [
    (re.compile(r"propose_skills"), "claude.ai 채팅 전용 도구(propose_skills). Claude Code·Cowork에는 없다"),
    (re.compile(r"project_(info|read|write|search)\b|`Projects` 도구"), "claude.ai Projects 도구. Projects가 연결되지 않은 세션(Claude Code 등)에는 없다"),
    (re.compile(r"present_files|computer://"), "claude.ai 채팅 전용 파일 제시 방식"),
    (re.compile(r"schtasks|%USERPROFILE%|\.vbs\b|mklink"), "Windows 전용 명령"),
]
HARDCODED = [
    (re.compile(r"/mnt/(skills|user-data)/[^\s`\"')]*"), "claude.ai 컨테이너 전용 절대경로"),
    (re.compile(r"/root/\.claude/skills/synced/(pptx|docx|xlsx|pdf)/[^\s`\"')]*"),
     "존재하지 않는 경로(synced/ 아래에 <조직>_<계정> 폴더가 한 단계 더 있다)"),
    (re.compile(r"/home/claude/[^\s`\"')]*"), "claude.ai 컨테이너 전용 작업경로"),
]
TEXT_EXT = {".md", ".py", ".js", ".mjs", ".json", ".sh", ".txt", ".vbs", ".ts"}


def iter_text_files(unit_root: Path):
    for p in sorted(unit_root.rglob("*")):
        if p.is_file() and p.suffix.lower() in TEXT_EXT and ".in_use" not in p.parts:
            yield p


def audit_unit(unit: str, unit_root: Path, skills: list[Skill], all_skills: list[Skill]) -> list[Finding]:
    out: list[Finding] = []
    short_to_qual: dict[str, set[str]] = {}
    for s in all_skills:
        short_to_qual.setdefault(s.name, set()).add(s.qualified)

    for f in iter_text_files(unit_root):
        rel = str(f.relative_to(unit_root))
        is_doc = f.suffix == ".md"
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for rx, why in SECRET_RES:
                if rx.search(line):
                    out.append(Finding("ERROR", "secret", unit, rel, i,
                                       f"비밀값 평문 포함: {why}. 모든 동기화 기기와 업로드본에 복제된다"))
            for rx, why in HARDCODED:
                m = rx.search(line)
                if not m:
                    continue
                # 코드 안의 '후보 목록'(여러 경로를 차례로 시도)은 폴백이 있으므로 문서만 오류로 본다
                in_candidates = not is_doc and ("glob" in line or "cands" in line or line.strip().startswith('"') or "expanduser" in line)
                # 문서에서도 다른 표면 경로(~/.claude)를 함께 제시하면 폴백이 있는 것으로 본다
                in_candidates = in_candidates or (is_doc and "~/.claude" in line)
                if in_candidates:
                    continue
                exists = Path(m.group(0).rstrip(".,")).exists()
                sev = "ERROR" if not exists else "WARN"
                fixable = "/root/.claude/skills/synced/" in m.group(0) or "/mnt/skills/plugins/" in m.group(0)
                out.append(Finding(sev, "hardcoded-path", unit, rel, i, f"{why}: {m.group(0)}", fixable))
            for rx, why in SURFACE_ONLY:
                if is_doc and rx.search(line):
                    out.append(Finding("INFO", "surface-only", unit, rel, i, why))
            if is_doc and "<SKILL_DIR>" in line and "find /" in line:
                out.append(Finding("WARN", "skill-dir", unit, rel, i,
                                   "<SKILL_DIR>을 `find /`로 찾는다. 느리고, 같은 스킬이 여러 곳에 있으면 엉뚱한 사본을 고른다",
                                   True))
            if is_doc:
                for ref in re.findall(r"`([a-z][a-z0-9-]{2,})`", line):
                    quals = short_to_qual.get(ref)
                    if quals and ref not in {s.name for s in skills} and all(":" in q for q in quals):
                        out.append(Finding("INFO", "bare-ref", unit, rel, i,
                                           f"다른 스킬을 짧은 이름 `{ref}`로 부른다. Skill 도구 이름은 {', '.join(sorted(quals))}"))
            if not is_doc and re.search(r"require\(['\"]pptxgenjs['\"]\)", line) and not node_has("pptxgenjs") \
                    and not any("npm i pptxgenjs" in sk.read_text(encoding="utf-8") for sk in unit_root.rglob("SKILL.md")):
                out.append(Finding("WARN", "runtime-dep", unit, rel, i,
                                   "pptxgenjs가 이 환경에 없다(claude.ai 컨테이너에만 기본 설치). 작업 폴더에서 `npm i pptxgenjs` 필요", True))
    return dedupe(out)


def dedupe(fs: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in fs:
        k = (f.rule, f.unit, f.file, f.message)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


_NODE_CACHE: dict[str, bool] = {}


def node_has(mod: str) -> bool:
    if mod not in _NODE_CACHE:
        if not shutil.which("node"):
            _NODE_CACHE[mod] = False
        else:
            r = subprocess.run(["node", "-e", f"require.resolve('{mod}')"], capture_output=True)
            _NODE_CACHE[mod] = r.returncode == 0
    return _NODE_CACHE[mod]


def units_of(skills: list[Skill]) -> dict[str, tuple[Path, list[Skill]]]:
    units: dict[str, tuple[Path, list[Skill]]] = {}
    for s in skills:
        key = s.plugin or s.name
        units.setdefault(key, (s.container, []))[1].append(s)
    return units


# --------------------------------------------------------------------------- 예산
def listing_entry_len(qualified: str, desc: str) -> int:
    d = desc if len(desc) <= MAX_DESC_CHARS else desc[:MAX_DESC_CHARS - 1] + "…"
    return len(f"- {qualified}: {d}") + 1


def budget_report(inv: Inventory) -> dict:
    entries = [(s.qualified, s.description, s.owner) for s in inv.skills]
    entries += [(q, d, "command") for q, d in inv.commands]
    total = sum(listing_entry_len(q, d) for q, d, _ in entries)
    mine_total = sum(listing_entry_len(q, d) for q, d, o in entries if o.startswith("mine"))
    env = os.environ.get(BUDGET_ENV)
    b200 = int(200_000 * BYTES_PER_TOKEN * BUDGET_FRACTION)
    b1m = int(1_000_000 * BYTES_PER_TOKEN * BUDGET_FRACTION)
    recommended = int(total * 1.2 // 1000 + 1) * 1000
    return {
        "entries": len(entries), "mine": sum(1 for *_, o in entries if o.startswith("mine")),
        "total_chars": total, "mine_chars": mine_total,
        "budget_200k": b200, "budget_1m": b1m, "env_budget": int(env) if env and env.isdigit() else None,
        "recommended": recommended,
        "by_owner": {o: sum(1 for *_, oo in entries if oo == o) for o in sorted({o for *_, o in entries})},
    }


def apply_budget(settings: Path, value: int) -> None:
    data = {}
    if settings.is_file():
        data = json.loads(settings.read_text(encoding="utf-8") or "{}")
    data.setdefault("env", {})[BUDGET_ENV] = str(value)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- 수정
def patch_text(unit: str, rel: str, text: str, ctx: dict) -> tuple[str, list[str]]:
    notes: list[str] = []
    new = text

    # P1. 존재하지 않는 /root/.claude/skills/synced/pptx/scripts → 표면 무관 탐색식
    rx = re.compile(r"/root/\.claude/skills/synced/pptx/scripts")
    if rel.endswith(".md") and rx.search(new):
        new = rx.sub(PPTX_SCRIPTS_EXPR.replace("\\", "\\\\"), new)
        notes.append("pptx scripts 경로를 claude.ai·Claude Code 공통 탐색식으로 교체")

    # P2. skill-versioning: /mnt/skills/plugins 고정 → 표면별 루트, 대상 목록을 manifest 기준으로
    if unit == "skill-versioning" and rel == "SKILL.md" and "/mnt/skills/plugins/" in new:
        new = new.replace("`/mnt/skills/plugins/<스킬명>/SKILL.md`", "`<SKILLS_ROOT>/<스킬명>/SKILL.md`")
        new = new.replace("`/mnt/skills/plugins/*/manifest.json`", "`<SKILLS_ROOT>/manifest.json`")
        root_note = (
            "\n> **`<SKILLS_ROOT>` 찾기 (표면별)** — claude.ai 채팅: `/mnt/skills/plugins` · "
            "Claude Code·Cowork: `$(ls -d ~/.claude/skills/synced/*/ | head -1)` (manifest.json이 있는 폴더). "
            "둘 다 없으면 사용자에게 스킬 원본 위치를 묻는다.\n"
            "> 스킬 수정은 claude.ai 채팅의 propose_skills 카드로만 저장된다. Claude Code·Cowork에서는 "
            "점검·보존(절차 1·2)만 하고, 복원 카드(절차 3)는 claude.ai 채팅에서 진행하라고 안내한다.\n"
        )
        new = new.replace("## 규칙\n", root_note + "\n## 규칙\n", 1)
        mine_names = ctx.get("mine_skill_names") or []
        if mine_names:
            old_list = re.search(r"대상은 source가 plugin인 Jeffrey 소유 스킬 \d+종이다\(([^)]*)\)", new)
            if old_list:
                new = new.replace(old_list.group(0),
                                  f"대상은 manifest에서 source가 plugin인 Jeffrey 소유 스킬 전체다"
                                  f"(현재 {len(mine_names)}종: {', '.join(mine_names)})")
            new = re.sub(r"(스킬) 10종", r"\1 전체", new)
            # 절차 문장만 바꾼다. 저장소 표의 과거 기준본(실제로 10종) 설명은 그대로 둔다.
            new = new.replace("10종 전체의", "전체의").replace("10종 변경 없음", "전체 변경 없음") \
                     .replace("(10종 전체 해시", "(전체 해시") \
                     .replace("10종 사본", "전체 사본")
        notes.append("skill-versioning: 표면별 SKILLS_ROOT 도입, 대상 목록을 manifest 기준으로 갱신")

    # P3. <SKILL_DIR> = find / → 로드 시 표시되는 Base directory 우선
    if rel.endswith("SKILL.md") and "<SKILL_DIR>" in new and "find /" in new:
        new = re.sub(r"`<SKILL_DIR>`는 `find / -path \"([^\"]+)\" 2>/dev/null`로 확인\.",
                     r"`<SKILL_DIR>`는 스킬을 불러올 때 표시되는 **Base directory**(Claude Code·Cowork) 또는 "
                     r"`/mnt/skills/*/iqvia-ppt-template`(claude.ai)이다. 둘 다 안 보일 때만 "
                     "`find ~/.claude /mnt/skills -path \"\\1\" 2>/dev/null | head -1`로 찾는다.", new)
        notes.append("<SKILL_DIR> 탐색을 Base directory 우선으로 변경(전체 디스크 find 제거)")

    # P4. pptxgenjs 미설치 환경 대비
    if unit == "consulting-deck-suite" and rel == "skills/consulting-deck/SKILL.md" and "npm i pptxgenjs" not in new:
        anchor = "`scripts/build_deck.js`를 작업 디렉터리에 복사해 `SLIDES` 배열을 채운다."
        if anchor in new:
            new = new.replace(anchor, anchor + "\n`node -e \"require('pptxgenjs')\"`가 실패하면(Claude Code·Cowork 등 "
                                               "claude.ai 밖의 환경) 같은 작업 디렉터리에서 `npm i pptxgenjs@3.12.0`을 먼저 실행한다.", 1)
            notes.append("consulting-deck: pptxgenjs 설치 안내 추가")
    return new, notes


def bump_version(pj: Path) -> str | None:
    data = json.loads(pj.read_text(encoding="utf-8"))
    v = data.get("version")
    if not v or not re.match(r"^\d+\.\d+\.\d+$", v):
        return None
    a, b, c = map(int, v.split("."))
    data["version"] = f"{a}.{b}.{c + 1}"
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data["version"]


def zip_dir(src: Path, dest: Path, arc_root: str) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file() and ".in_use" not in p.parts:
                z.write(p, str(Path(arc_root) / p.relative_to(src)) if arc_root else str(p.relative_to(src)))


def run_fix(inv: Inventory, out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "src").mkdir(exist_ok=True)
    (out / "zip").mkdir(exist_ok=True)
    ctx = {"mine_skill_names": sorted(s.name for s in inv.skills if s.owner == "mine-skill")}
    changes = []
    for unit, (root, skills) in units_of(mine(inv)).items():
        dest = out / "src" / unit
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns(".in_use"))
        notes: list[str] = []
        for f in iter_text_files(dest):
            rel = str(f.relative_to(dest))
            text = f.read_text(encoding="utf-8")
            new, n = patch_text(unit, rel, text, ctx)
            if new != text:
                f.write_text(new, encoding="utf-8")
                notes += [f"{rel}: {x}" for x in n]
        if not notes:
            shutil.rmtree(dest)
            continue
        is_plugin = (dest / ".claude-plugin/plugin.json").is_file()
        ver = bump_version(dest / ".claude-plugin/plugin.json") if is_plugin else None
        zp = out / "zip" / f"{unit}.zip"
        # 플러그인 zip은 루트에 .claude-plugin/, 스킬 zip은 <이름>/SKILL.md 구조(claude.ai 업로드 형식)
        zip_dir(dest, zp, "" if is_plugin else unit)
        changes.append({"unit": unit, "kind": "plugin" if is_plugin else "skill", "version": ver,
                        "zip": str(zp), "notes": notes})
    return changes


# --------------------------------------------------------------------------- CLI
def print_audit(inv: Inventory, findings: list[Finding], as_json: bool) -> None:
    b = budget_report(inv)
    if as_json:
        print(json.dumps({"budget": b, "findings": [f.__dict__ for f in findings]}, ensure_ascii=False, indent=2))
        return
    print(f"# skill_doctor 진단\n\n탐색 루트: {len(inv.roots)}곳 · 스킬 {len(inv.skills)}개 · 명령 {len(inv.commands)}개 "
          f"· 내 스킬/플러그인 스킬 {b['mine']}개\n")
    print("## 1. 스킬 목록 예산 (자동 트리거 가능 여부)\n")
    print(f"- 목록 전체 글자 수: {b['total_chars']:,}자 (내 것 {b['mine_chars']:,}자)")
    print(f"- 기본 예산: 200K 컨텍스트 {b['budget_200k']:,}자 / 1M 컨텍스트 {b['budget_1m']:,}자"
          + (f" / 현재 환경변수 {b['env_budget']:,}자" if b["env_budget"] else ""))
    eff = b["env_budget"] or b["budget_1m"]
    if b["total_chars"] > eff:
        print(f"- **초과**: 넘친 스킬은 설명 없이 이름만 노출돼 자동 트리거되지 않는다. "
              f"권장 {BUDGET_ENV}={b['recommended']} (또는 안 쓰는 플러그인 비활성화)")
    print(f"- 출처별 개수: {b['by_owner']}\n")
    print("## 2. 이식성 결함\n")
    if not findings:
        print("결함 없음")
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    # 짧은 이름 참조는 건수가 많아 스킬별 한 줄로 묶는다
    bare: dict[str, set[str]] = {}
    for f in findings:
        if f.rule == "bare-ref":
            bare.setdefault(f.unit, set()).add(f.message.split("`")[1])
    findings = [f for f in findings if f.rule != "bare-ref"]
    for f in sorted(findings, key=lambda x: (order[x.severity], x.unit, x.file, x.line)):
        fx = " [fix]" if f.fixable else ""
        print(f"- {f.severity:5} {f.unit} · {f.file}:{f.line} · {f.rule}{fx} — {f.message}")
    for unit, refs in sorted(bare.items()):
        print(f"- INFO  {unit} · bare-ref — 다른 스킬을 짧은 이름으로 참조 {len(refs)}건 "
              f"({', '.join(sorted(refs))}). 설명이 목록에 없으면 이 이름으로 찾지 못할 수 있다")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("audit", "fix", "budget"):
        p = sub.add_parser(name)
        p.add_argument("--root", action="append", type=Path, help="탐색 루트(여러 번 지정 가능)")
        if name == "audit":
            p.add_argument("--all", action="store_true", help="마켓플레이스·Anthropic 스킬까지 검사")
            p.add_argument("--json", action="store_true")
        if name == "fix":
            p.add_argument("--out", type=Path, required=True)
        if name == "budget":
            p.add_argument("--apply", action="store_true")
            p.add_argument("--settings", type=Path, default=HOME / ".claude" / "settings.json")
            p.add_argument("--value", type=int)
    a = ap.parse_args(argv)
    inv = load_inventory(a.root or default_roots())
    if not inv.skills:
        print("스킬을 찾지 못했다. --root로 스킬·플러그인 폴더를 지정하라.", file=sys.stderr)
        return 2

    if a.cmd == "audit":
        targets = inv.skills if a.all else mine(inv)
        findings: list[Finding] = []
        for unit, (root, skills) in units_of(targets).items():
            findings += audit_unit(unit, root, skills, inv.skills)
        print_audit(inv, findings, a.json)
        return 1 if any(f.severity == "ERROR" for f in findings) else 0

    if a.cmd == "fix":
        changes = run_fix(inv, a.out)
        for c in changes:
            print(f"## {c['unit']} ({c['kind']}{' → v' + c['version'] if c['version'] else ''})\n  zip: {c['zip']}")
            for n in c["notes"]:
                print(f"  - {n}")
        if not changes:
            print("고칠 항목 없음")
        return 0

    if a.cmd == "budget":
        b = budget_report(inv)
        value = a.value or b["recommended"]
        print(json.dumps(b, ensure_ascii=False, indent=2))
        if a.apply:
            apply_budget(a.settings, value)
            print(f"{a.settings} 의 env.{BUDGET_ENV} = {value} 기록. Claude Code를 다시 시작하면 적용된다.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
