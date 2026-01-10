# api/streaming.py
"""
Streaming server for video files (completed or downloading)
"""

from flask import Flask, Response, send_file, request, jsonify
from urllib.parse import unquote
from pathlib import Path
import time

from config.settings import Config

def create_stream_app(download_manager):
    """Create Flask app for streaming"""
    app = Flask('stream_server')
    
    @app.route('/stream/<torrent_hash>')
    def stream_torrent(torrent_hash):
        """Stream torrent file (for qBittorrent)"""
        qb_service = download_manager.qb_service
        
        if not qb_service or not qb_service.connected:
            return "qBittorrent no disponible", 503
        
        try:
            torrent = qb_service.client.torrents_info(torrent_hashes=torrent_hash)[0]
            files = qb_service.client.torrents_files(torrent_hash)
            largest_file = max(files, key=lambda f: f.size)
            
            filepath = Config.DOWNLOAD_DIR / largest_file.name
            
            # Wait for file to start downloading
            wait_time = 0
            while not filepath.exists() and wait_time < 30:
                time.sleep(1)
                wait_time += 1
            
            if not filepath.exists():
                return "Archivo aún no disponible", 202
            
            return _serve_file_with_range(filepath, largest_file.size)
            
        except Exception as e:
            print(f"❌ Error streaming torrent: {e}")
            return str(e), 500
    
    @app.route('/file/<path:filename>')
    def serve_file(filename):
        """Serve direct download file (completed or downloading)"""
        try:
            filename = unquote(filename)
            print(f"\n{'='*60}")
            print(f"📂 Solicitando: {filename}")
            print(f"{'='*60}")
            
            # Extract file ID
            file_id = filename.split('_')[0] if '_' in filename else None
            
            if file_id:
                print(f"🆔 ID a buscar: {file_id}")
            else:
                print(f"⚠️ No se detectó ID en el nombre")
            
            filepath = None
            is_downloading = False
            
            direct_path = Config.DOWNLOAD_DIR / filename
            if direct_path.exists():
                filepath = direct_path
                print(f"✅ Encontrado directo: {filepath.name}")
            
            if not filepath and file_id:
                print(f"🔍 Buscando archivos con ID: {file_id}_")
                
                patterns = [
                    f"{file_id}_*.mkv",
                    f"{file_id}_*.mp4",
                    f"{file_id}_*.mkv.part",
                    f"{file_id}_*.mp4.part",
                    f"{file_id}_*",
                ]
                
                for pattern in patterns:
                    matches = list(Config.DOWNLOAD_DIR.rglob(pattern))
                    if matches:
                        filepath = matches[0]
                        is_downloading = filepath.suffix == '.part'
                        print(f"✅ Encontrado: {filepath.name}")
                        if is_downloading:
                            print(f"   ⏬ Archivo en descarga activa")
                        break
            
            elif not filepath:
                base_name = Path(filename).name
                print(f"🔍 Buscando por nombre: {base_name}")
                
                for item in Config.DOWNLOAD_DIR.rglob(base_name):
                    if item.is_file():
                        filepath = item
                        print(f"✅ Encontrado: {filepath}")
                        break
            
            if not filepath and file_id:
                print(f"⏳ Esperando archivo con ID '{file_id}'...")
                filepath = _wait_for_file(file_id, timeout=60)
                if filepath:
                    is_downloading = filepath.suffix == '.part'
            
            # File not found
            if not filepath or not filepath.exists():
                print(f"❌ Archivo NO ENCONTRADO")
                print(f"   ID: {file_id}")
                print(f"   Filename: {filename}")
                
                if file_id:
                    _list_available_files(file_id)
                
                return "Archivo no encontrado. La descarga puede no haber iniciado.", 404
            
            # Wait for minimum size
            current_size = filepath.stat().st_size
            min_size = 5 * 1024 * 1024  # 5MB
            
            if current_size < min_size:
                print(f"⏳ Archivo pequeño ({current_size / (1024*1024):.2f} MB)")
                current_size = _wait_for_size(filepath, min_size, timeout=30)
                
                if current_size < min_size:
                    return "Descarga iniciando, espera...", 202
            
            print(f"✅ SIRVIENDO: {filepath.name}")
            print(f"   Tamaño: {current_size / (1024*1024):.2f} MB")
            if is_downloading:
                print(f"   🔴 STREAMING EN VIVO")
            
            # Verify not HTML
            if _is_html_file(filepath):
                print(f"❌ Archivo es HTML!")
                return "Error: Archivo es HTML", 500
            
            # Serve with range support
            return _serve_file_with_range(
                filepath,
                10 * 1024 * 1024 * 1024 if is_downloading else None,  # 10GB for downloading
                is_downloading
            )
            
        except Exception as e:
            print(f"❌ ERROR en serve_file: {e}")
            import traceback
            traceback.print_exc()
            return f"Error interno: {str(e)}", 500
    
    @app.route('/status/<torrent_hash>')
    def torrent_status(torrent_hash):
        """Get torrent download status"""
        status = download_manager.get_download_status(torrent_hash)
        return jsonify(status if status else {'error': 'Not found'})
    
    # Helper functions
    def _wait_for_file(file_id, timeout=60):
        """Wait for file to appear"""
        wait_time = 0
        while wait_time < timeout:
            time.sleep(2)
            wait_time += 2
            
            for pattern in [f"{file_id}_*.mkv", f"{file_id}_*.mp4", 
                          f"{file_id}_*.part", f"{file_id}_*"]:
                matches = list(Config.DOWNLOAD_DIR.rglob(pattern))
                if matches:
                    print(f"✅ Archivo detectado: {matches[0].name}")
                    return matches[0]
            
            if wait_time % 10 == 0:
                print(f"   Esperando... {wait_time}s / {timeout}s")
        
        return None
    
    def _wait_for_size(filepath, min_size, timeout=30):
        """Wait for file to reach minimum size"""
        wait_time = 0
        current_size = filepath.stat().st_size if filepath.exists() else 0
        
        while current_size < min_size and wait_time < timeout:
            time.sleep(2)
            wait_time += 2
            if filepath.exists():
                current_size = filepath.stat().st_size
                if wait_time % 10 == 0:
                    print(f"   Tamaño: {current_size / (1024*1024):.2f} MB")
        
        return current_size
    
    def _is_html_file(filepath):
        """Check if file is HTML"""
        try:
            with open(filepath, 'rb') as f:
                first_bytes = f.read(512)
                return b'<!DOCTYPE' in first_bytes or b'<html' in first_bytes
        except:
            return False
    
    def _list_available_files(file_id):
        """List available files with ID"""
        print(f"\n   Archivos disponibles con ID '{file_id}':")
        found_any = False
        for item in Config.DOWNLOAD_DIR.rglob("*"):
            if item.is_file() and file_id in item.name:
                print(f"      - {item.name}")
                found_any = True
        
        if not found_any:
            print(f"      (ninguno - descarga no iniciada)")
    
    def _serve_file_with_range(filepath, file_size=None, is_downloading=False):
        """Serve file with HTTP Range support"""
        if file_size is None:
            file_size = filepath.stat().st_size
        
        range_header = request.headers.get('Range')
        
        if range_header:
            print(f"📊 Range request: {range_header}")
            byte_range = range_header.replace('bytes=', '').split('-')
            start = int(byte_range[0]) if byte_range[0] else 0
            end_requested = int(byte_range[1]) if byte_range[1] else None
            
            # Limit end for downloading files
            if is_downloading:
                current_size = filepath.stat().st_size if filepath.exists() else 0
                end = min(end_requested if end_requested else file_size, current_size - 1)
                if end < start:
                    end = start
            else:
                end = end_requested if end_requested else file_size - 1
            
            def generate():
                bytes_sent = 0
                with open(filepath, 'rb') as f:
                    f.seek(start)
                    remaining = end - start + 1
                    
                    while remaining > 0:
                        # Check file size for downloading files
                        if is_downloading and filepath.exists():
                            current_file_size = filepath.stat().st_size
                            current_position = f.tell()
                            
                            # Wait for more data
                            if current_position >= current_file_size:
                                print(f"   ⏸️ Esperando datos... ({current_position / (1024*1024):.1f} MB)")
                                time.sleep(1)
                                continue
                        
                        chunk_size = min(1024 * 1024, remaining)  # 1MB chunks
                        data = f.read(chunk_size)
                        
                        if not data:
                            if is_downloading:
                                time.sleep(0.5)
                                continue
                            else:
                                break
                        
                        remaining -= len(data)
                        bytes_sent += len(data)
                        yield data
                
                print(f"   ✅ Enviados {bytes_sent / (1024*1024):.2f} MB")
            
            return Response(
                generate(),
                206,
                {
                    'Content-Type': 'video/mp4',
                    'Content-Range': f'bytes {start}-{end}/{file_size}',
                    'Accept-Ranges': 'bytes',
                    'Content-Length': end - start + 1
                }
            )
        else:
            print(f"📦 Request completo (sin Range)")
            if is_downloading:
                print(f"   ⚠️ Cliente no soporta Range")
            return send_file(filepath, mimetype='video/mp4')
    
    return app