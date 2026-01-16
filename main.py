# main.py
# GUI avanzada para descargar audio de YouTube con yt-dlp
# Mejoras aplicadas (respecto a tu versión):
# ✅ 1) Detección correcta de "saltados por historial" (archive) mediante flag leyendo output real
# ✅ 2) Cálculo correcto del progreso (base + progreso de ítem) sin inflar la barra
# ✅ 3) Cancelación robusta en Windows (grupo de procesos + CTRL_BREAK_EVENT; fallback kill)
# ✅ 4) Normalización: la UI dice "loudnorm" (no ReplayGain) para que sea correcto
# ✅ 5) Renombrado SOLO de archivos descargados en esta sesión (no toca toda la carpeta)
# ✅ 6) parse-metadata correcto usando regex (Artista - Título)
# ✅ 7) Preferencia M4A/Opus sin forzar conversión: selecciona ext y deja --audio-format best
# ✅ 8) TMP/TEMP forzados por proceso (env dedicado)
#
# Requisitos:
# - yt-dlp en C:\yt-dlp\yt-dlp.exe (ajusta YT_DLP si es necesario)
# - cookies.txt en la misma carpeta que main.py (o ajusta la ruta)
# - ffmpeg en PATH (necesario para mp3 y/o normalización)
# - Windows recomendado (winsound para beep; cancelación mejor soportada)

import os
import re
import sys
import hashlib
import subprocess
import threading
import queue
import signal
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from typing import Optional, Callable

try:
    import winsound
    _HAS_WINSOUND = True
except Exception:
    _HAS_WINSOUND = False


# === CONFIGURACIÓN ===
YT_DLP = r"C:\yt-dlp\yt-dlp.exe"
COOKIES = "cookies.txt"
DESTINO_DEFAULT = Path(r"D:\Musica\New")
TMP_DIR = Path(r"D:\Temp")
# Carpeta separada para logs/errores/historial dentro del destino
LOGS_DIRNAME = "_logs"

# === PERSISTENCIA DE CONFIG (última configuración marcada) ===
SETTINGS_FILE = Path(__file__).with_name("settings.json")

# Modos
MODO_MEJOR_AUDIO = "best"
MODO_MP3_V0 = "mp3_v0"
MODO_MP3_320 = "mp3_320"
MODO_M4A_PREF = "m4a_pref"
MODO_OPUS_PREF = "opus_pref"

DEFAULT_SETTINGS = {
    "destino": str(DESTINO_DEFAULT),
    "modo": MODO_MEJOR_AUDIO,
    "force": False,
    "normalizar": False,
    "parse_metadata": True,
    "quitar_emojis": False,
    "embed_thumbnail": False,
    "geometry": "1000x700",
}

def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        if SETTINGS_FILE.exists():
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update({k: loaded.get(k, v) for k, v in DEFAULT_SETTINGS.items()})
    except Exception:
        pass
    return data

def save_settings(data: dict) -> None:
    try:
        # Guardado "seguro" (escribe a tmp y reemplaza)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)
    except Exception:
        pass


# === UTILIDADES ===
def now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")

def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def url_hash(u: str) -> str:
    return hashlib.sha1(u.encode("utf-8")).hexdigest()[:10]

def logs_dir_for(destino: Path) -> Path:
    return destino / LOGS_DIRNAME

def es_playlist_o_canal(u: str) -> bool:
    p = urlparse(u)
    q = parse_qs(p.query)
    if "list" in q:
        return True
    path_low = p.path.lower()
    if any(seg in path_low for seg in ["/@", "/channel/", "/c/", "/user/"]) or path_low.endswith("/videos"):
        return True
    if "/playlist" in path_low:
        return True
    return False

def validar_url(u: str) -> tuple[bool, str]:
    if not u.startswith(("http://", "https://")):
        return False, "La URL debe empezar con http:// o https://"

    parsed = urlparse(u)
    dominios_validos = ["youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"]
    if not any(d in parsed.netloc for d in dominios_validos):
        return False, "La URL debe ser de YouTube"

    return True, "URL válida"

def cookies_invalidas() -> bool:
    try:
        with open(COOKIES, "r", encoding="utf-8") as f:
            contenido = f.read()
            return not any(k in contenido for k in ["SAPISID", "SID", "SSID", "LOGIN_INFO", "HSID"])
    except Exception:
        return True

def verificar_fecha_cookies(path: str, log_cb: Callable[[str], None]):
    try:
        dias = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).days
        if dias > 15:
            log_cb(f"⚠️ cookies.txt tiene {dias} días, podría estar caducado.")
    except Exception:
        pass

def limpiar_nombre_archivo(nombre: str, quitar_emojis: bool = False) -> str:
    if quitar_emojis:
        # Quitar emojis/símbolos fuera de rangos comunes (aprox.)
        nombre = re.sub(r'[^\x00-\x7F\u00C0-\u024F\u1E00-\u1EFF]+', '', nombre)

    safe = re.sub(r"[()\[\]<>]", "", nombre)
    safe = re.sub(r"\s{2,}", " ", safe).strip().replace("--", "-")
    return safe

def verificar_ffmpeg() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return True, result.stdout.split("\n")[0]
        return False, "ffmpeg no responde correctamente"
    except FileNotFoundError:
        return False, "ffmpeg no está en PATH"
    except Exception as e:
        return False, str(e)

def verificar_ytdlp(path: str) -> tuple[bool, str]:
    if not Path(path).exists():
        return False, f"No encontrado en {path}"
    try:
        result = subprocess.run(
            [path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, "yt-dlp no responde correctamente"
    except Exception as e:
        return False, str(e)


# === MOTOR DE DESCARGA ===
class DescargaManager:
    def __init__(self, config: dict, log_cb: Callable[[str], None], progress_cb: Callable[[float], None]):
        self.config = config
        self.log = log_cb
        self.progress = progress_cb

        self.proceso_actual: Optional[subprocess.Popen] = None
        self.cancelado = False

        self.destino = Path(config["destino"])
        self.logs_dir = logs_dir_for(self.destino)

        # Asegurar carpetas
        self.destino.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        # Migración sencilla (si antes estaban en la raíz del destino)
        self._migrar_logs_legacy()

        # Archivos "de sistema" ahora en carpeta aparte
        self.historial = self.logs_dir / "descargados.txt"
        self.log_errores = self.logs_dir / "errores.log"
        self.fallidos = self.logs_dir / "fallidos.txt"
        self.log_sesion = self.logs_dir / f"log_{now_stamp()}.txt"

        # Solo renombrar lo descargado en esta sesión:
        self.archivos_sesion: list[Path] = []

        self.stats = {"descargados": 0, "fallidos": 0, "saltados": 0}

    def _migrar_logs_legacy(self):
        """
        Si existían archivos legacy en la raíz del destino, los mueve a la carpeta de logs.
        No pisa si ya existen en destino nuevo.
        """
        legacy_names = ["descargados.txt", "errores.log", "fallidos.txt"]
        for name in legacy_names:
            old_path = self.destino / name
            new_path = self.logs_dir / name
            try:
                if old_path.exists() and old_path.is_file() and not new_path.exists():
                    old_path.replace(new_path)
            except Exception:
                # Si no puede mover (permisos/uso), lo dejamos como estaba.
                pass

    def cancelar(self):
        self.cancelado = True
        if not self.proceso_actual:
            return

        self.log("🛑 Cancelando descarga...")

        try:
            if os.name == "nt":
                # Intento limpio: CTRL+BREAK al grupo
                try:
                    self.proceso_actual.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    # Fallback si no existe o falla
                    self.proceso_actual.terminate()
            else:
                self.proceso_actual.terminate()

            self.proceso_actual.wait(timeout=5)

        except subprocess.TimeoutExpired:
            self.log("⚠️ Proceso no responde, forzando terminación...")
            try:
                self.proceso_actual.kill()
            except Exception:
                pass
        except Exception as e:
            self.log(f"❌ Error al cancelar: {e}")

    def log_to_file(self, msg: str):
        try:
            with open(self.log_sesion, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def ejecutar(self, url: str):
        self.cancelado = False
        self.stats = {"descargados": 0, "fallidos": 0, "saltados": 0}
        self.archivos_sesion = []

        # Reset barra
        self.progress(0.0)

        try:
            self._validar_entorno()
            urls = self._preparar_urls(url)

            if not urls:
                self.log("ℹ️ No hay elementos para descargar")
                return

            self.log(f"📥 Total de elementos: {len(urls)}")

            for idx, u in enumerate(urls, 1):
                if self.cancelado:
                    self.log("❌ Descarga cancelada por el usuario")
                    break

                self.log(f"🎧 [{idx}/{len(urls)}] {u}")
                self._descargar_uno(u, idx, len(urls))

            if not self.cancelado:
                self._renombrar_archivos_sesion()
                self._limpiar_temporales()
                self._mostrar_resumen()
                self.progress(100.0)

            if _HAS_WINSOUND and not self.cancelado:
                try:
                    winsound.Beep(1000, 300)
                    winsound.Beep(1200, 300)
                except Exception:
                    pass

        except Exception as e:
            self.log(f"❌ ERROR: {e}")
            self._log_error(str(e))
            raise
        finally:
            self.proceso_actual = None

    def _validar_entorno(self):
        existe, msg = verificar_ytdlp(YT_DLP)
        if not existe:
            raise RuntimeError(f"yt-dlp: {msg}")
        self.log(f"✓ yt-dlp: {msg}")
        
        # ✅ Verificar Deno (JS runtime)
        try:
            r = subprocess.run(["deno", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "deno --version falló")
            self.log(f"✓ deno: {r.stdout.splitlines()[0] if r.stdout else 'OK'}")
        except Exception:
            raise RuntimeError("Deno no está disponible en PATH. Instálalo o reinicia la sesión para que se aplique el PATH.")

        if not Path(COOKIES).exists():
            raise RuntimeError(f"No se encontró {COOKIES}")

        if cookies_invalidas():
            raise RuntimeError("Las cookies parecen inválidas")

        verificar_fecha_cookies(COOKIES, self.log)
        self.log(f"✓ Cookies: {COOKIES}")

        modo = self.config["modo"]
        if modo in [MODO_MP3_V0, MODO_MP3_320] or self.config.get("normalizar", False):
            existe, msg = verificar_ffmpeg()
            if not existe:
                raise RuntimeError(f"ffmpeg necesario pero no disponible: {msg}")
            self.log(f"✓ ffmpeg: {msg}")

    def _preparar_urls(self, url: str) -> list[str]:
        if not es_playlist_o_canal(url):
            self.log("🎯 URL de vídeo individual")
            return [url]

        self.log("🌐 Detectado canal/playlist, generando lista...")
        # Guardar listas generadas también en carpeta de logs
        urls_file = self.logs_dir / f"urls_{url_hash(url)}.txt"

        if self.config.get("force", False) and urls_file.exists():
            try:
                urls_file.unlink()
                self.log("♻️ Lista regenerada (--force)")
            except Exception:
                pass

        if urls_file.exists():
            self.log(f"📋 Usando lista existente: {urls_file.name}")
        else:
            comando = [YT_DLP, "--flat-playlist", "--get-id", "--cookies", COOKIES, url]
            with open(urls_file, "w", encoding="utf-8") as f:
                proceso = subprocess.run(comando, stdout=f, stderr=subprocess.PIPE, text=True)
            if proceso.returncode != 0:
                raise RuntimeError(f"Error generando lista: {proceso.stderr.strip()}")

        with open(urls_file, "r", encoding="utf-8") as f:
            ids = [line.strip() for line in f if line.strip()]

        return [f"https://www.youtube.com/watch?v={i}" for i in ids]

    def _construir_comando(self, url: str) -> list[str]:
        modo = self.config["modo"]

        cmd = [
            YT_DLP,
            "--js-runtimes", "deno",
            "--cookies", COOKIES,
            "--output", str(self.destino / "%(title).120s.%(ext)s"),
            "--download-archive", str(self.historial),

            # ✅ MUY IMPORTANTE en Windows (evita caracteres conflictivos)
            "--windows-filenames",
            "--trim-filenames", "180",

            "--ignore-errors",
            "--no-abort-on-error",
            "--sleep-interval", "5",
            "--max-sleep-interval", "10",
            "--progress",
            "--newline",
            "--no-check-certificate",
            "--force-ipv4",
            "--no-mtime",
            "--print", "after_download:FILE:%(filepath)s",
            "--print", "after_move:FILE:%(filepath)s",
        ]

        # --- Metadatos ---
        if self.config.get("parse_metadata", False):
            cmd.extend([
                "--parse-metadata", r"title:(?P<artist>.+?)\s*-\s*(?P<title>.+)",
                "--add-metadata",
            ])
        else:
            cmd.append("--add-metadata")

        # ✅ Evitar que yt-dlp rellene Comment/PURL con la URL
        cmd.extend([
            "--parse-metadata", r":(?P<meta_comment>)",
            "--parse-metadata", r":(?P<meta_purl>)",
        ])

        # ✅ Quitar emojis ANTES del postprocesado
        if self.config.get("quitar_emojis", False):
            cmd.extend([
                "--replace-in-metadata", "title",
                r"[^\x00-\x7F\u00C0-\u024F\u1E00-\u1EFF]+",
                ""
            ])

        # --- Miniatura como cover (si está activado) ---
        if self.config.get("embed_thumbnail", False):
            cmd.extend(["--embed-thumbnail", "--convert-thumbnails", "jpg"])
            cmd.extend([
                "--postprocessor-args",
                "ThumbnailsConvertor+ffmpeg_o:-vf scale=500:500 -q:v 3"
            ])

        # --- Normalización loudnorm (si está activado) ---
        if self.config.get("normalizar", False):
            cmd.extend([
                "--postprocessor-args",
                "ExtractAudio+ffmpeg_o:-af loudnorm"
            ])


        # Selección de stream + postproceso
        if modo == MODO_MEJOR_AUDIO:
            cmd.extend(["-f", "251/140/139/bestaudio/best", "--extract-audio", "--audio-format", "best"])

        elif modo == MODO_M4A_PREF:
            # Preferir m4a, si no existe, caer a bestaudio. No forzar conversión: usar audio-format best.
            cmd.extend(["-f", "140/139/bestaudio[ext=m4a]/bestaudio/best", "--extract-audio", "--audio-format", "best"])

        elif modo == MODO_OPUS_PREF:
            cmd.extend(["-f", "251/bestaudio[ext=opus]/bestaudio/best", "--extract-audio", "--audio-format", "best"])

        elif modo == MODO_MP3_V0:
            cmd.extend(["-f", "251/140/139/bestaudio/best", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"])

        elif modo == MODO_MP3_320:
            cmd.extend(["-f", "251/140/139/bestaudio/best", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "320K"])

        else:
            raise RuntimeError(f"Modo desconocido: {modo}")

        cmd.append(url)
        return cmd

    def _descargar_uno(self, url: str, idx: int, total: int):
        cmd = self._construir_comando(url)

        # base de progreso por ítem
        base = (idx - 1) / total * 100
        self.progress(base)

        env = os.environ.copy()
        env["TMP"] = str(TMP_DIR)
        env["TEMP"] = str(TMP_DIR)

        creationflags = 0
        if os.name == "nt":
            # Importante para cancelar bien: crear grupo de procesos
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        skip_encontrado = False
        archivo_detectado_esta_descarga: Optional[Path] = None

        try:
            self.proceso_actual = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=env,
                creationflags=creationflags
            )

            stdout = self.proceso_actual.stdout
            if stdout:
                for line in stdout:
                    if self.cancelado:
                        break

                    line = line.rstrip("\n")
                    if not line.strip():
                        continue

                    self.log(line)
                    self.log_to_file(line)

                    low = line.lower()

                    # ✅ Saltado por archive (se ve en output, incluso con rc=0)
                    if (
                        "already been recorded in the archive" in low
                        or "already in archive" in low
                        or "has already been downloaded" in low
                    ):
                        skip_encontrado = True

                    # ✅ Capturar filepath final (por --print after_move:filepath)
                    # Suele ser una ruta absoluta o relativa. Si existe en disco, la guardamos.
                    # Evitamos false positives: que contenga separador y extensión típica.
                    # (yt-dlp imprime muchas cosas, pero esto reduce ruido)
                    if line.startswith("FILE:"):
                        try:
                            p = Path(line[5:].strip())
                            if p.exists() and p.is_file():
                                archivo_detectado_esta_descarga = p.resolve()
                        except Exception:
                            pass

                    # ✅ Progreso por % (arreglado)
                    if "[download]" in low:
                        m = re.search(r'(\d+\.?\d*)\s*%', line)
                        if m:
                            try:
                                pct_item = float(m.group(1))
                                item = (pct_item / 100.0) * (100.0 / total)
                                self.progress(base + item)
                            except Exception:
                                pass

            rc = self.proceso_actual.wait()

            if self.cancelado:
                self.stats["fallidos"] += 1
                self.log("❌ Cancelado durante descarga/procesado")
                return

            if skip_encontrado:
                self.stats["saltados"] += 1
                self.log("⏭️ Ya descargado previamente (archive)")
            elif rc == 0:
                self.stats["descargados"] += 1
                # Guardar el archivo de esta descarga para renombrar SOLO ese
                if archivo_detectado_esta_descarga:
                    self.archivos_sesion.append(archivo_detectado_esta_descarga)
            else:
                self.stats["fallidos"] += 1
                self.log(f"❌ Falló (rc={rc})")
                try:
                    with open(self.fallidos, "a", encoding="utf-8") as f:
                        f.write(url + "\n")
                except Exception:
                    pass

        except Exception as e:
            self.stats["fallidos"] += 1
            self.log(f"❌ Excepción: {e}")
            self._log_error(f"{url}: {e}")
        finally:
            self.proceso_actual = None

    def _renombrar_archivos_sesion(self):
        self.log("🔤 Renombrando SOLO archivos de esta sesión...")

        if not self.archivos_sesion:
            self.log("ℹ️ No hay archivos de sesión detectados para renombrar.")
            return

        quitar_emojis = self.config.get("quitar_emojis", False)

        # Deduplicar y solo los que sigan existiendo
        vistos = set()
        files = []
        for p in self.archivos_sesion:
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            if rp in vistos:
                continue
            vistos.add(rp)
            if rp.exists() and rp.is_file():
                files.append(rp)

        for audio in files:
            orig = audio.name
            safe = limpiar_nombre_archivo(orig, quitar_emojis)
            if safe == orig:
                continue

            try:
                nuevo = audio.with_name(safe)
                if nuevo.exists():
                    # evitar colisión simple
                    stem = nuevo.stem
                    suf = nuevo.suffix
                    nuevo = audio.with_name(f"{stem}_{now_stamp()}{suf}")
                audio.rename(nuevo)
                self.log(f"🔄 {orig} → {nuevo.name}")

                # actualizar lista para futuras operaciones si hiciera falta
                try:
                    idx = self.archivos_sesion.index(audio)
                    self.archivos_sesion[idx] = nuevo
                except Exception:
                    pass

            except Exception as e:
                self._log_error(f"Renombrar {orig}: {e}")

        self.log("✅ Renombrado de sesión completado.")

    def _limpiar_temporales(self):
        self.log("🧽 Limpiando temporales...")
        patterns = ["*.webp", "*.part", "*.temp", "*.ytdl", "*.tmp"]
        for pattern in patterns:
            for archivo in self.destino.glob(pattern):
                try:
                    archivo.unlink()
                    self.log(f"🗑 {archivo.name}")
                except Exception as e:
                    self._log_error(f"Eliminar {archivo.name}: {e}")
        self.log("✅ Limpieza completada.")

    def _mostrar_resumen(self):
        self.log("\n" + "=" * 50)
        self.log("📊 RESUMEN DE DESCARGA")
        self.log(f"  ✅ Descargados: {self.stats['descargados']}")
        self.log(f"  ⏭️ Saltados (archive): {self.stats['saltados']}")
        self.log(f"  ❌ Fallidos: {self.stats['fallidos']}")
        self.log("=" * 50)

        if self.fallidos.exists():
            self.log(f"⚠️ Ver fallidos en: {self.fallidos}")

        self.log(f"📄 Log completo: {self.log_sesion}")

    def _log_error(self, msg: str):
        try:
            with open(self.log_errores, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now()}] {msg}\n")
        except Exception:
            pass


# === GUI ===
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Audio Downloader Pro")
        self.geometry("1000x700")
        self.minsize(900, 650)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.progress_queue: queue.Queue[float] = queue.Queue()

        self.worker_thread: Optional[threading.Thread] = None
        self.manager: Optional[DescargaManager] = None

        # Variables
        self.url_var = tk.StringVar()
        self.destino_var = tk.StringVar(value=str(DESTINO_DEFAULT))
        self.mode_var = tk.StringVar(value=MODO_MEJOR_AUDIO)
        self.force_var = tk.BooleanVar(value=False)
        self.normalizar_var = tk.BooleanVar(value=False)
        self.parse_meta_var = tk.BooleanVar(value=True)
        self.quitar_emojis_var = tk.BooleanVar(value=False)
        self.embed_thumbnail_var = tk.BooleanVar(value=False)
        
        # Cargar última configuración guardada
        self._save_after_id = None
        s = load_settings()

        self.destino_var.set(s["destino"])
        self.mode_var.set(s["modo"])
        self.force_var.set(bool(s["force"]))
        self.normalizar_var.set(bool(s["normalizar"]))
        self.parse_meta_var.set(bool(s["parse_metadata"]))
        self.quitar_emojis_var.set(bool(s["quitar_emojis"]))
        self.embed_thumbnail_var.set(bool(s["embed_thumbnail"]))

        # Restaurar tamaño/posición ventana (si quieres)
        try:
            self.geometry(s.get("geometry", "1000x700"))
        except Exception:
            pass

        # Guardar automáticamente cuando cambie cualquier opción
        for v in [
            self.destino_var,
            self.mode_var,
            self.force_var,
            self.normalizar_var,
            self.parse_meta_var,
            self.quitar_emojis_var,
            self.embed_thumbnail_var,
        ]:
            v.trace_add("write", lambda *args: self._save_settings_debounced())

        # Guardar al cerrar
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._poll_queues()
        self._verificar_entorno_inicial()

    # --- Cola-safe callbacks para el manager ---
    def _log_from_worker(self, msg: str):
        self.log_queue.put(f"[{now_hms()}] {msg}" if not msg.startswith("[") else msg)

    def _progress_from_worker(self, value: float):
        try:
            v = max(0.0, min(100.0, float(value)))
        except Exception:
            v = 0.0
        self.progress_queue.put(v)
        
    def _collect_settings(self) -> dict:
        return {
            "destino": self.destino_var.get(),
            "modo": self.mode_var.get(),
            "force": bool(self.force_var.get()),
            "normalizar": bool(self.normalizar_var.get()),
            "parse_metadata": bool(self.parse_meta_var.get()),
            "quitar_emojis": bool(self.quitar_emojis_var.get()),
            "embed_thumbnail": bool(self.embed_thumbnail_var.get()),
            "geometry": self.geometry(),
        }

    def _save_settings_now(self):
        save_settings(self._collect_settings())

    def _save_settings_debounced(self):
        # Evita guardar 50 veces si el usuario hace clic rápido
        if self._save_after_id:
            try:
                self.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.after(250, self._save_settings_now)

    def _on_close(self):
        self._save_settings_now()
        self.destroy()

    # --- UI ---
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        tab_descarga = ttk.Frame(notebook)
        notebook.add(tab_descarga, text="Descarga")
        self._build_tab_descarga(tab_descarga)

        tab_historial = ttk.Frame(notebook)
        notebook.add(tab_historial, text="Historial")
        self._build_tab_historial(tab_historial)

        tab_config = ttk.Frame(notebook)
        notebook.add(tab_config, text="Configuración")
        self._build_tab_config(tab_config)

    def _build_tab_descarga(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # URL
        url_frame = ttk.LabelFrame(frame, text="URL", padding=10)
        url_frame.pack(fill="x", pady=(0, 10))

        url_entry = ttk.Entry(url_frame, textvariable=self.url_var, font=("", 10))
        url_entry.pack(fill="x")
        url_entry.focus_set()

        ttk.Label(
            url_frame,
            text="Introduce la URL de un vídeo, canal o playlist de YouTube",
            foreground="#666",
            font=("", 9)
        ).pack(anchor="w", pady=(5, 0))

        # Destino
        dest_frame = ttk.LabelFrame(frame, text="Carpeta de destino", padding=10)
        dest_frame.pack(fill="x", pady=(0, 10))

        dest_row = ttk.Frame(dest_frame)
        dest_row.pack(fill="x")

        ttk.Entry(dest_row, textvariable=self.destino_var, font=("", 9)).pack(side="left", fill="x", expand=True)

        ttk.Button(dest_row, text="Cambiar...", command=self.seleccionar_destino).pack(side="left", padx=(5, 0))
        ttk.Button(dest_row, text="Abrir carpeta", command=self.abrir_destino).pack(side="left", padx=(5, 0))

        # Modo
        modo_frame = ttk.LabelFrame(frame, text="Modo de descarga", padding=10)
        modo_frame.pack(fill="x", pady=(0, 10))

        ttk.Radiobutton(modo_frame, text="Mejor calidad posible (audio-only)", value=MODO_MEJOR_AUDIO, variable=self.mode_var).pack(anchor="w")
        ttk.Radiobutton(modo_frame, text="Preferir M4A (sin forzar conversión)", value=MODO_M4A_PREF, variable=self.mode_var).pack(anchor="w")
        ttk.Radiobutton(modo_frame, text="Preferir Opus (sin forzar conversión)", value=MODO_OPUS_PREF, variable=self.mode_var).pack(anchor="w")
        ttk.Radiobutton(modo_frame, text="MP3 VBR V0 (máxima calidad variable)", value=MODO_MP3_V0, variable=self.mode_var).pack(anchor="w")
        ttk.Radiobutton(modo_frame, text="MP3 CBR 320 kbps", value=MODO_MP3_320, variable=self.mode_var).pack(anchor="w")

        # Opciones
        opts_frame = ttk.LabelFrame(frame, text="Opciones", padding=10)
        opts_frame.pack(fill="x", pady=(0, 10))

        ttk.Checkbutton(opts_frame, text="Forzar regeneración de lista (canales/playlists)", variable=self.force_var).pack(anchor="w")
        ttk.Checkbutton(opts_frame, text="Normalizar volumen (loudnorm)", variable=self.normalizar_var).pack(anchor="w")
        ttk.Checkbutton(opts_frame, text="Parsear metadatos 'Artista - Título'", variable=self.parse_meta_var).pack(anchor="w")
        ttk.Checkbutton(opts_frame, text="Quitar emojis de nombres de archivo", variable=self.quitar_emojis_var).pack(anchor="w")
        ttk.Checkbutton(opts_frame, text="Incrustar miniatura como cover", variable=self.embed_thumbnail_var).pack(anchor="w")

        # Controles
        ctrl_frame = ttk.Frame(frame)
        ctrl_frame.pack(fill="x", pady=(0, 10))

        self.btn_descargar = ttk.Button(ctrl_frame, text="▶ Descargar", command=self.iniciar_descarga)
        self.btn_descargar.pack(side="left")

        self.btn_cancelar = ttk.Button(ctrl_frame, text="⏹ Cancelar", command=self.cancelar_descarga, state="disabled")
        self.btn_cancelar.pack(side="left", padx=(10, 0))

        ttk.Button(ctrl_frame, text="🗑 Limpiar log", command=self.limpiar_log).pack(side="left", padx=(10, 0))

        # Progreso
        self.progress_bar = ttk.Progressbar(frame, mode="determinate", maximum=100.0)
        self.progress_bar.pack(fill="x", pady=(0, 10))

        # Log
        log_frame = ttk.LabelFrame(frame, text="Log de descarga", padding=5)
        log_frame.pack(fill="both", expand=True)

        scroll = ttk.Scrollbar(log_frame)
        scroll.pack(side="right", fill="y")

        self.txt_log = tk.Text(log_frame, height=15, wrap="word", yscrollcommand=scroll.set, font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True)
        scroll.config(command=self.txt_log.yview)
        self.txt_log.configure(state="disabled")

    def _build_tab_historial(self, parent):
        frame = ttk.Frame(parent, padding=10)
        frame.pack(fill="both", expand=True)

        info_frame = ttk.Frame(frame)
        info_frame.pack(fill="x", pady=(0, 10))

        self.lbl_historial = ttk.Label(info_frame, text="Historial: 0", font=("", 10, "bold"))
        self.lbl_historial.pack(side="left")

        ttk.Button(info_frame, text="🔄 Actualizar", command=self.actualizar_historial).pack(side="right")
        ttk.Button(info_frame, text="🗑 Limpiar historial", command=self.limpiar_historial).pack(side="right", padx=(0, 10))

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)

        scroll = ttk.Scrollbar(list_frame)
        scroll.pack(side="right", fill="y")

        self.txt_historial = tk.Text(list_frame, wrap="none", yscrollcommand=scroll.set, font=("Consolas", 9))
        self.txt_historial.pack(fill="both", expand=True)
        scroll.config(command=self.txt_historial.yview)

        self.actualizar_historial()

    def _build_tab_config(self, parent):
        frame = ttk.Frame(parent, padding=10)
        frame.pack(fill="both", expand=True)

        rutas_frame = ttk.LabelFrame(frame, text="Rutas de herramientas", padding=10)
        rutas_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(rutas_frame, text=f"yt-dlp: {YT_DLP}", font=("", 9)).pack(anchor="w")
        ttk.Label(rutas_frame, text=f"Cookies: {Path(COOKIES).resolve()}", font=("", 9)).pack(anchor="w")
        ttk.Label(rutas_frame, text=f"Temporales: {TMP_DIR}", font=("", 9)).pack(anchor="w")
        ttk.Label(rutas_frame, text=f"Logs: {logs_dir_for(Path(self.destino_var.get() or str(DESTINO_DEFAULT))).resolve()}", font=("", 9)).pack(anchor="w")

        deps_frame = ttk.LabelFrame(frame, text="Estado de dependencias", padding=10)
        deps_frame.pack(fill="x", pady=(0, 10))

        self.txt_deps = tk.Text(deps_frame, height=8, wrap="word", font=("Consolas", 9))
        self.txt_deps.pack(fill="x")
        self.txt_deps.configure(state="disabled")

        ttk.Button(deps_frame, text="🔍 Verificar dependencias", command=self._verificar_entorno_inicial).pack(anchor="e", pady=(5, 0))

    def _verificar_entorno_inicial(self):
        self.txt_deps.configure(state="normal")
        self.txt_deps.delete("1.0", "end")

        existe, msg = verificar_ytdlp(YT_DLP)
        self.txt_deps.insert("end", f"{'✅' if existe else '❌'} yt-dlp: {msg}\n")

        existe, msg = verificar_ffmpeg()
        self.txt_deps.insert("end", f"{'✅' if existe else '⚠️'} ffmpeg: {msg}\n")
        
        # ✅ deno (JS runtime)
        try:
            r = subprocess.run(["deno", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            ok = (r.returncode == 0)
            ver = r.stdout.splitlines()[0] if (ok and r.stdout) else (r.stderr.strip() or "no responde")
            self.txt_deps.insert("end", f"{'✅' if ok else '❌'} deno: {ver}\n")
        except Exception as e:
            self.txt_deps.insert("end", f"❌ deno: {e}\n")

        if Path(COOKIES).exists():
            self.txt_deps.insert("end", f"{'✅' if not cookies_invalidas() else '⚠️'} cookies: {COOKIES}\n")
        else:
            self.txt_deps.insert("end", f"❌ cookies: no encontrado ({COOKIES})\n")

        self.txt_deps.configure(state="disabled")

    # --- Acciones ---
    def seleccionar_destino(self):
        folder = filedialog.askdirectory(initialdir=self.destino_var.get() or str(DESTINO_DEFAULT))
        if folder:
            self.destino_var.set(folder)
            self._save_settings_debounced()   # 👈 añadido
            self.actualizar_historial()

    def abrir_destino(self):
        path = Path(self.destino_var.get())
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{e}")

    def iniciar_descarga(self):
        url = self.url_var.get().strip()
        ok, msg = validar_url(url)
        if not ok:
            messagebox.showerror("URL inválida", msg)
            return

        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("En curso", "Ya hay una descarga en curso.")
            return

        config = {
            "destino": self.destino_var.get().strip() or str(DESTINO_DEFAULT),
            "modo": self.mode_var.get(),
            "force": bool(self.force_var.get()),
            "normalizar": bool(self.normalizar_var.get()),
            "parse_metadata": bool(self.parse_meta_var.get()),
            "quitar_emojis": bool(self.quitar_emojis_var.get()),
            "embed_thumbnail": bool(self.embed_thumbnail_var.get()),
        }
        
        self._save_settings_now()

        self.manager = DescargaManager(config=config, log_cb=self._log_from_worker, progress_cb=self._progress_from_worker)

        self.btn_descargar.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self._log_from_worker("===== INICIO =====")
        self._log_from_worker(f"URL: {url}")
        self._log_from_worker(f"Destino: {config['destino']}")
        self._log_from_worker(f"Modo: {config['modo']} | force={config['force']} | loudnorm={config['normalizar']}")

        def worker():
            try:
                self.manager.ejecutar(url)
            except Exception as e:
                self._log_from_worker(f"ERROR: {e}")
                messagebox.showerror("Error", str(e))
            finally:
                self._log_from_worker("===== FIN =====")
                self.btn_descargar.configure(state="normal")
                self.btn_cancelar.configure(state="disabled")
                self.actualizar_historial()

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def cancelar_descarga(self):
        if self.manager:
            self.manager.cancelar()

    def limpiar_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def actualizar_historial(self):
        destino = Path(self.destino_var.get().strip() or str(DESTINO_DEFAULT))
        # Historial ahora vive en carpeta de logs; fallback por compatibilidad
        hist = logs_dir_for(destino) / "descargados.txt"
        if not hist.exists():
            legacy = destino / "descargados.txt"
            if legacy.exists():
                hist = legacy

        self.txt_historial.delete("1.0", "end")
        count = 0
        if hist.exists():
            try:
                with open(hist, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
                count = len(lines)
                self.txt_historial.insert("end", "\n".join(lines))
            except Exception:
                pass
        self.lbl_historial.configure(text=f"Historial: {count}")

    def limpiar_historial(self):
        destino = Path(self.destino_var.get().strip() or str(DESTINO_DEFAULT))
        hist_new = logs_dir_for(destino) / "descargados.txt"
        hist_old = destino / "descargados.txt"

        if not (hist_new.exists() or hist_old.exists()):
            return

        if not messagebox.askyesno("Confirmar", "¿Seguro que quieres borrar el historial?"):
            return

        try:
            if hist_new.exists():
                hist_new.unlink()
            if hist_old.exists():
                hist_old.unlink()
            self.actualizar_historial()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo borrar el historial:\n{e}")


    # --- Polling de colas ---
    def _poll_queues(self):
        # Log queue
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.txt_log.configure(state="normal")
                self.txt_log.insert("end", msg + "\n")
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
        except queue.Empty:
            pass

        # Progress queue (solo el último valor si hay muchos)
        last = None
        try:
            while True:
                last = self.progress_queue.get_nowait()
        except queue.Empty:
            pass
        if last is not None:
            self.progress_bar["value"] = last

        self.after(100, self._poll_queues)


if __name__ == "__main__":
    app = App()
    app.mainloop()
