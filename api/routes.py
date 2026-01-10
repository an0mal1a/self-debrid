"""
REST API endpoints compatible with AllDebrid API
"""

from flask import Flask, jsonify, request
from urllib.parse import quote
import time

from config.settings import Config

def create_api_app(download_manager):
    """Create Flask app with API routes"""
    app = Flask(__name__)
    
    @app.before_request
    def log_request():
        print(f"\n{'='*60}")
        print(f"🔵 {request.method} {request.path}")
        print(f"Args: {dict(request.args)}")
        print(f"{'='*60}\n")
    
    @app.route('/v4/link/unlock', methods=['GET'])
    def unlock_link():
        """Main endpoint to unlock links (torrents or direct downloads)"""
        url = request.args.get('link')
        if not url:
            return jsonify({'status': 'error', 'error': 'No link provided'}), 400
        
        print(f"🔓 Desbloqueando: {url}")
        
        file_id = download_manager.get_file_hash(url)
        print(f"🆔 File ID: {file_id}")
        
        stream_url = None
        filename = None
        filesize = 0
        
        # Handle torrents/magnets
        if download_manager.is_magnet(url) or download_manager.is_torrent(url):
            print("🧲 Detectado: Torrent/Magnet")
            torrent_info = download_manager.add_torrent(url)
            
            if not torrent_info:
                return jsonify({'status': 'error', 'error': 'Failed to add torrent'}), 500
            
            stream_url = f"http://localhost:{Config.STREAM_PORT}/stream/{torrent_info['hash']}"
            filename = torrent_info['name']
            filesize = torrent_info['size']
        
        # Handle direct downloads
        else:
            print("📦 Detectado: Descarga directa")
            
            # Check cache first
            existing = download_manager.find_existing_file(file_id)
            if existing:
                print(f"🎯 Usando archivo en caché")
                filename = existing.name
                filesize = existing.stat().st_size
                stream_url = f"http://localhost:{Config.STREAM_PORT}/file/{quote(filename)}"
            
            # Try JDownloader
            else:
                print("📥 Intentando con JDownloader...")
                jd_info = download_manager.add_to_jdownloader(url, file_id)
                
                if jd_info:
                    filename = jd_info['filename']
                    filesize = jd_info.get('filesize', 0)
                    stream_url = f"http://localhost:{Config.STREAM_PORT}/file/{quote(filename)}"
                    
                    if jd_info.get('from_cache'):
                        print(f"🎯 Archivo ya estaba en caché")
                    else:
                        print(f"✅ JDownloader procesando")
                else:
                    print("⚠️ JDownloader falló")
                    return jsonify({'status': 'error', 'error': 'JDownloader failed'}), 500
        
        if not stream_url or not filename:
            return jsonify({'status': 'error', 'error': 'No se pudo procesar el enlace'}), 500
        
        print(f"✅ Stream URL: {stream_url}")
        print(f"📄 Filename: {filename}")
        print(f"📦 Size: {filesize / (1024*1024):.2f} MB")
        
        return jsonify({
            'status': 'success',
            'data': {
                'link': stream_url,
                'filename': filename,
                'filesize': filesize,
                'host': 'local',
                'streaming': [],
                'id': file_id
            }
        })
    
    @app.route('/v4/user', methods=['GET'])
    def user_info():
        """Return fake user info"""
        return jsonify({
            'status': 'success',
            'data': {
                'user': {
                    'username': 'SelfDebrid',
                    'email': 'self@debrid.local',
                    'isPremium': True,
                    'premiumUntil': int(time.time()) + 31536000,  # 1 year
                    'lang': 'en',
                    'preferedDomain': 'alldebrid.com'
                }
            }
        })
    
    @app.route('/v4/user/history/delete', methods=['GET'])
    def delete_history():
        """Fake endpoint for history deletion"""
        return jsonify({'status': 'success', 'data': {'message': 'OK'}})
    
    return app