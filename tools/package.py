#!/usr/bin/env python3
"""claude.ai 업로드용 zip 생성: packages/<플러그인>-plugin.zip, packages/<스킬>-skill.zip"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packages"
SKIP = {"__pycache__", ".DS_Store"}


def add_tree(z: zipfile.ZipFile, base: Path, prefix: str) -> None:
    for f in sorted(base.rglob("*")):
        if f.is_file() and not SKIP.intersection(f.parts):
            z.write(f, f"{prefix}{f.relative_to(base).as_posix()}")


OUT.mkdir(exist_ok=True)
for plugin in sorted((ROOT / "plugins").iterdir()):
    with zipfile.ZipFile(OUT / f"{plugin.name}-plugin.zip", "w", zipfile.ZIP_DEFLATED) as z:
        add_tree(z, plugin, "")
    for skill in sorted((plugin / "skills").iterdir()):
        with zipfile.ZipFile(OUT / f"{skill.name}-skill.zip", "w", zipfile.ZIP_DEFLATED) as z:
            add_tree(z, skill, f"{skill.name}/")
for f in sorted(OUT.glob("*.zip")):
    print(f"{f.relative_to(ROOT)}  {f.stat().st_size // 1024} KB")
