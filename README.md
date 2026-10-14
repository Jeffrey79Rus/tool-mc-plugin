# tool-mc-plugin
[Tool] 자동화, 플러그인, 스킬

## tools/skill_doctor.py

claude.ai에서 만든 스킬과 업로드한 플러그인이 Claude Code·Cowork·claude.ai 어디서나 동작하는지 진단하고 고친다.

```bash
python3 tools/skill_doctor.py audit                     # 진단 (내 스킬·플러그인)
python3 tools/skill_doctor.py fix --out ./skill-fixes   # 수정본과 재업로드용 zip 생성
python3 tools/skill_doctor.py budget --apply            # 스킬 목록 예산을 ~/.claude/settings.json에 기록
python3 tools/plugin_usage.py                           # 지난 30일 플러그인 실제 사용 횟수 (PC의 Claude Code 기록 기준)
```

진단 결과와 원인 분석: [docs/skill-portability-report.md](docs/skill-portability-report.md)

이 저장소의 `.claude/settings.json`은 스킬 목록 예산(`SLASH_COMMAND_TOOL_CHAR_BUDGET`)을 넓혀,
이 저장소에서 여는 세션에서는 내 스킬이 설명과 함께 노출되게 한다.
