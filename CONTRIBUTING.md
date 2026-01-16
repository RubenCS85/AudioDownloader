# Contributing to AudioDownloader

Thanks for your interest in contributing! �

## How to contribute
1. Fork the repository
2. Create a branch from `main`:
   - `feature/<short-name>` for new features
   - `fix/<short-name>` for bugfixes
3. Make your changes with clear, small commits
4. Open a Pull Request (PR) with:
   - What you changed
   - Why you changed it
   - How to test it

## Development setup
- Python 3.10+ recommended
- External tools required by the app:
  - `yt-dlp`
  - `ffmpeg` (needed for mp3 and/or loudnorm)
  - `deno` (if using `--js-runtimes deno`)

Run:
```bash
python main.py
```

## Important: do not commit secrets
This project may use a `cookies.txt` file for YouTube authentication.
- **Never commit `cookies.txt`**
- Make sure it is ignored by Git (`.gitignore`)

If you accidentally committed it, delete it from Git history as soon as possible and rotate the cookies.

## Code style
- Keep code readable and documented
- Prefer small, focused functions
- Avoid hardcoding paths when possible (use config / UI)

## Reporting issues
When reporting a bug, please include:
- Windows version
- `yt-dlp --version`, `ffmpeg -version`, `deno --version`
- Steps to reproduce
- Relevant log output (remove personal data)
