from dotenv import load_dotenv
from pathlib import Path
import qbittorrentapi
import os

load_dotenv()

# qBittorrent config
QB_HOST=os.getenv("QB_HOST", "localhost")
QB_PORT=int(os.getenv("QB_PORT", 8080))
QB_USER=os.getenv("QB_USER", "admin")
QB_PASS=os.getenv("QB_PASS", "adminadmin")
STREAM_PORT=8081
MAX_CACHE_SIZE_GB=50

# JDownloader config
JDOWNLOADER_EMAIL=os.getenv("JDOWNLOADER_EMAIL")
JDOWNLOADER_PASSWORD=os.getenv("JDOWNLOADER_PASSWORD")
JDOWNLOADER_DEVICE=os.getenv("JDOWNLOADER_DEVICE")

USE_YTDLP = False
USE_JDOWNLOADER = True
USE_BITTORRENT = False

# ============= CONFIGURACIÓN =============
if os.name != 'posix':
    DOWNLOAD_DIR = Path("J:/DebridCache")
else:
    DOWNLOAD_DIR = Path("/Users/an0mal1a/.cache/selfDebrid")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ============= CLIENTE QBITTORRENT =============
if USE_BITTORRENT:
    try:
        qbt_client = qbittorrentapi.Client(
            host=QB_HOST,
            port=QB_PORT,
            username=QB_USER,
            password=QB_PASS
        )
        qbt_client.auth_log_in()
        print("✅ Conectado a qBittorrent")
    except Exception as e:
        print(f"⚠️ qBittorrent no disponible: {e}")
        qbt_client = None
else: 
    qbt_client = None

# ============= CLIENTE JDOWNLOADER =============
jd_device = None
if USE_JDOWNLOADER:
    try:
        import myjdapi
        
        jd = myjdapi.Myjdapi()
        jd.set_app_key("KODI_DEBRID_APP")
        
        if JDOWNLOADER_EMAIL and JDOWNLOADER_PASSWORD:
            jd.connect(JDOWNLOADER_EMAIL, JDOWNLOADER_PASSWORD)
            jd.update_devices()
            jd_device = jd.get_device(JDOWNLOADER_DEVICE)
            print(f"✅ Conectado a JDownloader (MyJDownloader)")
        else:
            print("⚠️ Para usar JDownloader remoto, configura email/password")
            jd_device = "local"
        
    except ImportError:
        print("⚠️ myjdapi no instalado. Instala con: pip install myjdapi")
        jd_device = None
    except Exception as e:
        print(f"⚠️ JDownloader no disponible: {e}")
        jd_device = None
else:
    jd_device = None