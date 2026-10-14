#!/usr/bin/env python3
"""plugin_usage — Claude Code 대화 기록에서 플러그인 실제 사용 이력을 센다.

Claude Code는 대화 기록을 ~/.claude/projects/**/*.jsonl (Windows: %USERPROFILE%\\.claude\\projects)에
남긴다. 이 스크립트는 그 기록에서 어시스턴트가 실제로 호출한 도구·스킬과 사용자가 입력한 슬래시 명령만 센다.
시스템 프롬프트의 스킬 목록처럼 '언급'만 된 것은 세지 않는다. claude.ai 채팅·Cowork 기록은 여기에 없다.

사용:
  python3 tools/plugin_usage.py                      # 기본 5개 플러그인, 최근 30일
  python3 tools/plugin_usage.py --days 60 --plugin sales --plugin figma
  python3 tools/plugin_usage.py --root D:\\claude\\projects
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PLUGINS = ["small-business", "sales", "nimble", "adobe-for-creativity", "figma"]


def matchers(plugin: str) -> list[re.Pattern]:
    p = re.escape(plugin)
    pats = [rf"^{p}:", rf"^mcp__plugin_{p}_"]
    if plugin == "adobe-for-creativity":
        pats.append(r"^mcp__Adobe_for_creativity__")   # 계정 커넥터(플러그인과 별개)도 함께 표시
    if plugin == "figma":
        pats.append(r"^mcp__figma")
    return [re.compile(x) for x in pats]


def tool_name(block: dict) -> str | None:
    """tool_use 블록에서 플러그인 판별용 이름을 뽑는다 (Skill·Agent·Bash는 인자까지 본다)."""
    name = block.get("name", "")
    inp = block.get("input") or {}
    if name == "Skill":
        return inp.get("skill") or inp.get("command")
    if name in ("Agent", "Task"):
        return inp.get("subagent_type")
    if name == "Bash" and re.match(r"\s*nimble\s", inp.get("command", "")):
        return "nimble:cli"
    return name


def scan(root: Path, plugins: list[str], since: datetime):
    pats = {p: matchers(p) for p in plugins}
    hits = defaultdict(list)
    files = 0
    for f in root.rglob("*.jsonl"):
        files += 1
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            ts = ev.get("timestamp")
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
            except ValueError:
                when = None
            if when and when < since:
                continue
            msg = ev.get("message") or {}
            content = msg.get("content")
            names = []
            if ev.get("type") == "assistant" and isinstance(content, list):
                names = [tool_name(b) for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            elif ev.get("type") == "user" and isinstance(content, str):
                m = re.match(r"\s*(?:<command-name>)?/([\w-]+:[\w-]+)", content)
                if m:
                    names = [m.group(1)]
            for n in filter(None, names):
                for p, rx in pats.items():
                    if any(r.search(n) for r in rx):
                        hits[p].append((when.date().isoformat() if when else "?", n, f.parent.name))
    return hits, files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path.home() / ".claude" / "projects")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--plugin", action="append")
    a = ap.parse_args(argv)
    if not a.root.is_dir():
        print(f"기록 폴더가 없다: {a.root}", file=sys.stderr)
        return 2
    plugins = a.plugin or DEFAULT_PLUGINS
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    hits, files = scan(a.root, plugins, since)
    print(f"# 플러그인 사용 이력 (최근 {a.days}일, 기록 파일 {files}개, 기준 {a.root})\n")
    for p in plugins:
        h = hits.get(p, [])
        print(f"- {p}: {len(h)}회" + ("  → 미사용" if not h else ""))
        for d, n, proj in sorted(h)[-10:]:
            print(f"    {d}  {n}  ({proj})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
