# YouTubeChannelAudioDownloader

GUI (Tkinter) para descargar audio de YouTube usando yt-dlp, con soporte de:
- canal/playlist
- historial (download-archive)
- cancelación robusta en Windows
- normalización loudnorm (ffmpeg)
- metadatos artista - título
- opción embed thumbnail

## Requisitos
- Windows recomendado
- yt-dlp instalado (por defecto en `C:\yt-dlp\yt-dlp.exe`)
- ffmpeg en PATH (necesario para mp3 y/o loudnorm)
- deno en PATH (si usas `--js-runtimes deno`)
- `cookies.txt` (NO se sube al repo)

## Ejecutar
```bash
python main.py
