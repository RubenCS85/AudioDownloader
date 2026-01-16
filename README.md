# AudioDL

**AudioDL** es una aplicación de descarga de audio **profesional, modular y escalable**, diseñada para soportar múltiples plataformas mediante un sistema de *providers* enchufables.

Actualmente soporta **YouTube** como fuente descargable y está preparada arquitectónicamente para integrar **Spotify, Apple Music, Mixcloud y otros servicios**.

---

## ✨ Características principales

- � **Arquitectura basada en providers**
- � **Pipeline desacoplado**
- �️ **Interfaz gráfica (Tkinter)**
- � **CLI incluida**
- ⚙️ **Configuración flexible**
- � **Escalable y mantenible**

---

## � Estructura del proyecto

```
AudioDownloader/
├─ configs/
│  └─ default.yaml
├─ scripts/
│  └─ dev_run_tkinter.py
├─ src/
│  └─ audiodl/
│     ├─ core/
│     ├─ providers/
│     ├─ ui/
│     └─ __main__.py
├─ pyproject.toml
└─ README.md
```

---

## ▶️ Uso rápido

### UI (desarrollo)

```bash
python scripts/dev_run_tkinter.py
```

### CLI

```bash
audiodl "https://www.youtube.com/watch?v=XXXX"
```

---

## ⚙️ Configuración

Archivo base:

```
configs/default.yaml
```

Variables de entorno:

```
AUDIODL_DOWNLOAD_DIR
AUDIODL_TMP_DIR
AUDIODL_FFMPEG_PATH
AUDIODL_COOKIES_PATH
AUDIODL_AUDIO_FORMAT
AUDIODL_AUDIO_QUALITY
AUDIODL_OVERWRITE
AUDIODL_LOG_LEVEL
```

---

## � Providers

### Implementado
- YouTube (yt-dlp)

### En preparación
- Spotify (metadata + bridge)
- Apple Music (metadata + bridge)
- Mixcloud

---

## � Licencia

MIT

---

## � Autor

Ruben  
OPTIMA IBERICA  
ruben@optimaiberica.es
