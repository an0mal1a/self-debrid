from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv()

class Config:
    # Paths
    DOWNLOAD_DIR = Path(os.getenv('DOWNLOAD_DIR', 'J:/DebridCache'))
    CERT_PATH = Path(os.getenv('CERT_PATH', 'cert/cert.pem'))
    KEY_PATH = Path(os.getenv('KEY_PATH', 'cert/key.pem'))
    
    # Network
    API_PORT = int(os.getenv('API_PORT', 443))
    STREAM_PORT = int(os.getenv('STREAM_PORT', 8081))
    
    # qBittorrent
    USE_BITTORRENT = os.getenv('USE_BITTORRENT', 'false').lower() == 'true'
    QB_HOST = os.getenv('QB_HOST')
    QB_PORT = int(os.getenv('QB_PORT'))
    QB_USER = os.getenv('QB_USER')
    QB_PASS = os.getenv('QB_PASS')
    
    # JDownloader
    USE_JDOWNLOADER = os.getenv('USE_JDOWNLOADER', 'true').lower() == 'true'
    JD_EMAIL = os.getenv('JD_EMAIL', '')
    JD_PASSWORD = os.getenv('JD_PASSWORD', '')
    JD_DEVICE = os.getenv('JD_DEVICE', 'JDownloader@device')
    
    # Cache
    MAX_CACHE_SIZE_GB = int(os.getenv('MAX_CACHE_SIZE_GB', 50))
    
    # Create directories
    DOWNLOAD_DIR.mkdir(exist_ok=True)


# # ============= CONFIGURACIÓN =============
# if os.name != 'posix':
#     DOWNLOAD_DIR = Path("J:/DebridCache")
# else:
#     DOWNLOAD_DIR = Path("/Users/an0mal1a/.cache/selfDebrid")
# DOWNLOAD_DIR.mkdir(exist_ok=True)

# # ============= CLIENTE QBITTORRENT =============
# if USE_BITTORRENT:
#     try:
#         qbt_client = qbittorrentapi.Client(
#             host=QB_HOST,
#             port=QB_PORT,
#             username=QB_USER,
#             password=QB_PASS
#         )
#         qbt_client.auth_log_in()
#         print("✅ Conectado a qBittorrent")
#     except Exception as e:
#         print(f"⚠️ qBittorrent no disponible: {e}")
#         qbt_client = None
# else: 
#     qbt_client = None

# # ============= CLIENTE JDOWNLOADER =============
# jd_device = None
# if USE_JDOWNLOADER:
#     try:
#         import myjdapi
        
#         jd = myjdapi.Myjdapi()
#         jd.set_app_key("KODI_DEBRID_APP")
        
#         if JDOWNLOADER_EMAIL and JDOWNLOADER_PASSWORD:
#             jd.connect(JDOWNLOADER_EMAIL, JDOWNLOADER_PASSWORD)
#             jd.update_devices()
#             jd_device = jd.get_device(JDOWNLOADER_DEVICE)
#             print(f"✅ Conectado a JDownloader (MyJDownloader)")
#         else:
#             print("⚠️ Para usar JDownloader remoto, configura email/password")
#             jd_device = "local"
        
#     except ImportError:
#         print("⚠️ myjdapi no instalado. Instala con: pip install myjdapi")
#         jd_device = None
#     except Exception as e:
#         print(f"⚠️ JDownloader no disponible: {e}")
#         jd_device = None
# else:
#     jd_device = None