import subprocess
from pathlib import Path
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, error
from datetime import datetime

# Ruta de tu carpeta de música descargada
carpeta = Path(r"D:\Ruben\Musica\Youtube\RevivalDanceHits")

# Buscar archivos a convertir (.webm y .m4a)
archivos_audio = list(carpeta.glob("*.webm")) + list(carpeta.glob("*.m4a"))
print(f"🎧 Archivos a convertir encontrados: {len(archivos_audio)}")

def añadir_metadatos(mp3_path, artista, titulo, album, año, url):
    try:
        audio = EasyID3(mp3_path)
    except error:
        audio = EasyID3()
    audio["artist"] = artista
    audio["title"] = titulo
    audio["album"] = album
    audio["date"] = año
    audio["website"] = url
    audio.save(mp3_path)

for archivo in archivos_audio:
    mp3_destino = archivo.with_suffix(".mp3")
    print(f"🔄 Convirtiendo: {archivo.name} → {mp3_destino.name}")

    comando = [
        "ffmpeg",
        "-y",
        "-i", str(archivo),
        "-vn",
        "-ab", "320k",
        "-ar", "44100",
        "-loglevel", "error",
        str(mp3_destino)
    ]

    try:
        subprocess.run(comando, check=True)
        añadir_metadatos(
            mp3_path=mp3_destino,
            artista="Deivit Watios",
            titulo=mp3_destino.stem,
            album="YouTube Mix",
            año=str(datetime.now().year),
            url="https://www.youtube.com"
        )

        # 🧹 Eliminar el archivo original (.webm o .m4a)
        print(f"🗑 Eliminando original: {archivo.name}")
        archivo.unlink()

        # 🧽 Eliminar miniaturas relacionadas (.webp y .png)
        for ext in [".webp", ".png"]:
            thumb = archivo.with_suffix(ext)
            if thumb.exists():
                print(f"🗑 Eliminando miniatura: {thumb.name}")
                thumb.unlink()

    except subprocess.CalledProcessError:
        print(f"❌ Error al convertir: {archivo.name}")

print("\n✅ Conversión y limpieza completadas.")
