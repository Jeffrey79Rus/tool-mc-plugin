# yt-dlp 참고

- 공식 저장소: https://github.com/yt-dlp/yt-dlp (README에 전체 옵션 설명)
- 지원 사이트 목록: https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md
  — YouTube, Vimeo, Twitter/X, Instagram, TikTok, Facebook, 네이버TV, 카카오TV, SBS·KBS·MBC 일부, Bilibili, Twitch VOD, SoundCloud 등
- 설치: `pip install -U yt-dlp` (PyPI). 단일 실행 파일은 GitHub Releases에서 내려받을 수도 있다.
- 라이선스: Unlicense(퍼블릭 도메인). 도구 자체는 자유롭게 쓸 수 있으나, 내려받는 콘텐츠의 권리는 별개다.

## download.py가 내부에서 쓰는 yt-dlp 설정

| 모드 | format 문자열 | 후처리 |
|---|---|---|
| audio | `bestaudio[ext=m4a]/bestaudio/best` | FFmpegExtractAudio → m4a(기본) |
| video | `bv*[height<=H][ext=mp4]+ba[ext=m4a]/bv*[height<=H]+ba/b` | mp4로 합치기 |
| subs | 내려받기 생략, `writesubtitles`/`writeautomaticsub`, vtt 우선 | 자체 파서로 txt/srt/md/json 변환 |

공통: `noplaylist`(기본), `retries 5`, 파일명 `%(title).80s [%(id)s].%(ext)s`, Windows 호환 파일명.

## yt-dlp를 직접 쓸 때 자주 쓰는 원본 명령

```bash
yt-dlp -F "URL"                                   # 받을 수 있는 포맷 목록
yt-dlp -x --audio-format m4a "URL"                # 음성만
yt-dlp -f "bv*[height<=1080]+ba/b" --merge-output-format mp4 "URL"
yt-dlp --write-subs --write-auto-subs --sub-langs "ko,en" --skip-download "URL"
yt-dlp --download-sections "*00:10:00-00:25:00" --force-keyframes-at-cuts -x "URL"
yt-dlp --cookies-from-browser chrome "URL"        # 로그인 필요
yt-dlp -U                                          # 실행 파일 자체 업데이트 (pip 설치면 pip install -U yt-dlp)
```

## 자주 보는 오류

| 메시지 | 뜻 | 대처 |
|---|---|---|
| `Sign in to confirm you're not a bot` | 유튜브 봇 차단 | 최신판으로 갱신 → 사용자 PC에서 `--cookies-from-browser` |
| `Unable to extract ...` | 사이트 구조 변경 | `pip install -U yt-dlp` |
| `Requested format is not available` | 해당 화질 없음 | `info`로 확인 후 `--max-height` 낮추기 |
| `Tunnel connection failed: 403` | 작업 환경 네트워크 정책이 막음 | 허용 도메인 추가 또는 로컬에서 실행 |
| `ffmpeg not found` | ffmpeg 미설치 | `brew install ffmpeg` / `apt-get install ffmpeg` |
