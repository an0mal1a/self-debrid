"""
Self-Debrid - Self-hosted debrid service
Supports torrents via qBittorrent and direct downloads via JDownloader
"""

import sys
import ssl
import time
import threading

from config.settings import Config
from services.jdownloader import JDownloaderService
from services.qbittorrent import QBittorrentService
from core.download_manager import DownloadManager
from api.routes import create_api_app
from api.streaming import create_stream_app

def main():
    """Main entry point"""
    print("\n" + "="*60)
    print("🚀 SELF-DEBRID")
    print("="*60)
    
    # Initialize services
    jd_service = None
    if Config.USE_JDOWNLOADER:
        jd_service = JDownloaderService(
            Config.JD_EMAIL,
            Config.JD_PASSWORD,
            Config.JD_DEVICE
        )
        jd_service.connect()
    
    qb_service = QBittorrentService(
        Config.QB_HOST,
        Config.QB_PORT,
        Config.QB_USER,
        Config.QB_PASS
    )
    
    # Initialize download manager
    download_manager = DownloadManager(
        Config.DOWNLOAD_DIR,
        jd_service,
        qb_service
    )
    
    # Create Flask apps
    api_app = create_api_app(download_manager)
    stream_app = create_stream_app(download_manager)
    
    # Start streaming server
    def run_stream_server():
        stream_app.run(
            host='0.0.0.0',
            port=Config.STREAM_PORT,
            threaded=True,
            debug=False
        )
    
    stream_thread = threading.Thread(target=run_stream_server, daemon=True)
    stream_thread.start()
    
    # Start cleanup thread
    def periodic_cleanup():
        while True:
            time.sleep(3600)  # Every hour
            download_manager.cleanup_cache()
    
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    
    # SSL context
    try:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(str(Config.CERT_PATH), str(Config.KEY_PATH))
    except FileNotFoundError:
        print("❌ SSL certificates not found!")
        print(f"   Expected: {Config.CERT_PATH} and {Config.KEY_PATH}")
        print("\n   Generate with:")
        print("   mkdir cert")
        print("   openssl req -x509 -newkey rsa:4096 -nodes \\")
        print("     -out cert/cert.pem -keyout cert/key.pem -days 365")
        return
    
    # Print status
    print(f"\n📡 API: https://0.0.0.0:{Config.API_PORT}")
    print(f"🎬 Stream: http://0.0.0.0:{Config.STREAM_PORT}")
    print(f"💾 Cache: {Config.DOWNLOAD_DIR}")
    print(f"🔧 qBittorrent: {'✅ Connected' if qb_service.connected else '❌ Disconnected'}")
    print(f"📥 JDownloader: {'✅ Connected' if jd_service and jd_service.connected else '❌ Disconnected'}")
    print("="*60 + "\n")

    if not qb_service.connected and not jd_service.connected:
        print("\n\nWe didn't find any download option, exiting...")
        sys.exit(1)
    
    # Run API server
    try:
        api_app.run(
            host='0.0.0.0',
            port=Config.API_PORT,
            ssl_context=ssl_context,
            debug=False,
            threaded=True
        )
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()