from debrid_downloader import DownloadManager
from config import *

from flask import Flask, jsonify, request, Response, send_file
from urllib.parse import unquote, quote
from pathlib import Path
import threading
import time
import ssl

app = Flask(__name__)
dm = DownloadManager()

# ============= ENDPOINTS API =============
@app.before_request
def log_request():
    print(f"\n{'='*60}")
    print(f"🔵 {request.method} {request.path}")
    print(f"Args: {dict(request.args)}")
    print(f"{'='*60}\n")


@app.route('/v4/link/unlock', methods=['GET'])
def unlock_link():
    url = request.args.get('link')
    if not url:
        return jsonify({'status': 'error', 'error': 'No link provided'}), 400
    
    print(f"🔓 Desbloqueando: {url}")
    
    file_id = dm.get_file_hash(url)
    print(f"🆔 File ID: {file_id}")
    
    stream_url = None
    filename = None
    filesize = 0

    # Torrent/Magnet
    if dm.is_magnet(url) or dm.is_torrent(url):
        print("🧲 Detectado: Torrent/Magnet")
        torrent_info = dm.add_torrent(url)
        
        if not torrent_info:
            return jsonify({'status': 'error', 'error': 'Failed to add torrent'}), 500
        
        stream_url = f"http://localhost:{STREAM_PORT}/stream/{torrent_info['hash']}"
        filename = torrent_info['name']
        filesize = torrent_info['size']
        
    else:
        print("📦 Detectado: Descarga directa")
        
        # VERIFICAR CACHÉ PRIMERO
        existing = dm.find_existing_file(file_id)
        if existing:
            print(f"🎯 Usando archivo en caché")
            filename = existing.name
            filesize = existing.stat().st_size
            stream_url = f"http://localhost:{STREAM_PORT}/file/{quote(filename)}"
        
        # Intentar con JDownloader
        elif USE_JDOWNLOADER and jd_device:
            print("📥 Intentando con JDownloader...")
            jd_info = dm.add_to_jdownloader(url, file_id)
            
            if jd_info:
                filename = jd_info['filename']
                filesize = jd_info.get('filesize', 0)
                stream_url = f"http://localhost:{STREAM_PORT}/file/{quote(filename)}"
                
                if jd_info.get('from_cache'):
                    print(f"🎯 Archivo ya estaba en caché")
                else:
                    print(f"✅ JDownloader procesando")
            else:
                print("⚠️ JDownloader falló. Fallback a descarga manual.")
        
        # Fallback: descarga manual
        if not stream_url:
            print("⬇️ Descarga directa manual...")
            filepath, filename, filesize = dm.download_direct(url, file_id)
            
            if not filepath:
                return jsonify({'status': 'error', 'error': filename}), 500
            
            stream_url = f"http://localhost:{STREAM_PORT}/file/{quote(filename)}"

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
    return jsonify({
        'status': 'success',
        'data': {
            'user': {
                'username': 'LocalDebrid',
                'email': 'local@debrid.fake',
                'isPremium': True,
                'premiumUntil': int(time.time()) + 31536000,
                'lang': 'es',
                'preferedDomain': 'alldebrid.com'
            }
        }
    })

@app.route('/v4/user/history/delete', methods=['GET'])
def delete_history():
    return jsonify({'status': 'success', 'data': {'message': 'OK'}})

# ============= SERVIDOR DE STREAMING =============
def stream_app():
    """Servidor separado para streaming"""
    stream = Flask('stream_server')
    
    @stream.route('/stream/<torrent_hash>')
    def stream_torrent(torrent_hash):
        """Stream de torrent en progreso"""
        if not qbt_client:
            return "qBittorrent no disponible", 503
        
        try:
            torrent = qbt_client.torrents_info(torrent_hashes=torrent_hash)[0]
            files = qbt_client.torrents_files(torrent_hash)
            largest_file = max(files, key=lambda f: f.size)
            
            filepath = DOWNLOAD_DIR / largest_file.name
            
            wait_time = 0
            while not filepath.exists() and wait_time < 30:
                time.sleep(1)
                wait_time += 1
            
            if not filepath.exists():
                return "Archivo aún no disponible", 202
            
            range_header = request.headers.get('Range')
            file_size = filepath.stat().st_size if filepath.exists() else largest_file.size
            
            if range_header:
                byte_range = range_header.replace('bytes=', '').split('-')
                start = int(byte_range[0]) if byte_range[0] else 0
                end = int(byte_range[1]) if byte_range[1] else file_size - 1
                
                def generate():
                    with open(filepath, 'rb') as f:
                        f.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            data = f.read(chunk_size)
                            if not data:
                                time.sleep(0.1)
                                continue
                            remaining -= len(data)
                            yield data
                
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
                return send_file(filepath, mimetype='video/mp4')
                
        except Exception as e:
            print(f"❌ Error streaming: {e}")
            return str(e), 500
    
    @stream.route('/file/<path:filename>')
    def serve_file(filename):
        """Servir archivo descargado o EN DESCARGA con soporte Range"""
        try:
            filename = unquote(filename)
            print(f"\n{'='*60}")
            print(f"📂 Solicitando: {filename}")
            print(f"{'='*60}")
            
            # Extraer el ID del filename (antes del primer _)
            file_id = filename.split('_')[0] if '_' in filename else None
            
            if file_id:
                print(f"🆔 ID a buscar: {file_id}")
            else:
                print(f"⚠️ No se detectó ID en el nombre, buscando por nombre completo")
            
            filepath = None
            is_downloading = False
            
            # 1. Buscar directo por nombre completo
            direct_path = DOWNLOAD_DIR / filename
            if direct_path.exists():
                filepath = direct_path
                print(f"✅ Encontrado directo: {filepath.name}")
            
            # 2. Si no existe y tenemos ID, buscar archivos con ese ID
            if not filepath and file_id:
                print(f"🔍 Buscando archivos que empiecen con: {file_id}_")
                
                # Patrones a buscar (en orden de prioridad)
                search_patterns = [
                    f"{file_id}_*.mkv",      # Archivo completo
                    f"{file_id}_*.mp4",      # Archivo completo MP4
                    f"{file_id}_*.mkv.part", # En descarga con .part
                    f"{file_id}_*.mp4.part", # En descarga MP4 con .part
                    f"{file_id}_*",          # Cualquier cosa con el ID
                ]
                
                for pattern in search_patterns:
                    # Buscar en raíz
                    matches = list(DOWNLOAD_DIR.glob(pattern))
                    if matches:
                        filepath = matches[0]
                        is_downloading = filepath.suffix == '.part'
                        print(f"✅ Encontrado en raíz: {filepath.name}")
                        if is_downloading:
                            print(f"   ⏬ Archivo en descarga activa")
                        break
                    
                    # Buscar recursivamente
                    if not filepath:
                        matches = list(DOWNLOAD_DIR.rglob(pattern))
                        if matches:
                            filepath = matches[0]
                            is_downloading = filepath.suffix == '.part'
                            print(f"✅ Encontrado en subcarpeta: {filepath}")
                            if is_downloading:
                                print(f"   ⏬ Archivo en descarga activa")
                            break
                    
                    if filepath:
                        break
            
            # 3. Si no hay ID, buscar por nombre exacto
            elif not filepath:
                base_name = Path(filename).name
                print(f"🔍 Buscando por nombre exacto: {base_name}")
                
                for item in DOWNLOAD_DIR.rglob(base_name):
                    if item.is_file():
                        filepath = item
                        print(f"✅ Encontrado: {filepath}")
                        break
            
            # 4. Esperar si no existe (solo si tenemos el ID correcto)
            if not filepath and file_id:
                print(f"⏳ Archivo con ID '{file_id}' no encontrado, esperando descarga...")
                wait_time = 0
                max_wait = 60  # 1 minuto
                
                while wait_time < max_wait:
                    time.sleep(2)
                    wait_time += 2
                    
                    # Buscar archivos con el ID (incluyendo .part)
                    for pattern in [f"{file_id}_*.mkv", f"{file_id}_*.mp4", 
                                   f"{file_id}_*.part", f"{file_id}_*"]:
                        matches = list(DOWNLOAD_DIR.rglob(pattern))
                        if matches:
                            filepath = matches[0]
                            is_downloading = filepath.suffix == '.part' or filepath.stat().st_size < 10 * 1024 * 1024
                            print(f"✅ Archivo detectado: {filepath.name}")
                            if is_downloading:
                                print(f"   ⏬ Streaming iniciará desde descarga")
                            break
                    
                    if filepath:
                        break
                    
                    if wait_time % 10 == 0:
                        print(f"   Esperando... {wait_time}s / {max_wait}s")
            
            # NO HAY FALLBACK - Si no encuentra el archivo correcto, error 404
            if not filepath or not filepath.exists():
                print(f"❌ Archivo NO ENCONTRADO")
                print(f"   ID buscado: {file_id}")
                print(f"   Filename: {filename}")
                
                if file_id:
                    print(f"\n   Archivos disponibles con ID '{file_id}':")
                    found_any = False
                    for item in DOWNLOAD_DIR.rglob("*"):
                        if item.is_file() and file_id in item.name:
                            print(f"      - {item.name}")
                            found_any = True
                    
                    if not found_any:
                        print(f"      (ninguno - descarga no iniciada)")
                
                return "Archivo no encontrado. La descarga puede no haber iniciado aún.", 404
            
            # Verificar tamaño mínimo para streaming
            current_size = filepath.stat().st_size
            min_size_for_stream = 5 * 1024 * 1024  # 5MB mínimo
            
            if current_size < min_size_for_stream:
                print(f"⏳ Archivo muy pequeño aún ({current_size / (1024*1024):.2f} MB)")
                print(f"   Esperando al menos {min_size_for_stream / (1024*1024):.0f} MB...")
                
                wait_time = 0
                while current_size < min_size_for_stream and wait_time < 30:
                    time.sleep(2)
                    wait_time += 2
                    if filepath.exists():
                        current_size = filepath.stat().st_size
                        if wait_time % 10 == 0:
                            print(f"   Tamaño actual: {current_size / (1024*1024):.2f} MB")
                
                if current_size < min_size_for_stream:
                    return "Descarga iniciando, espera unos segundos...", 202
            
            print(f"✅ SIRVIENDO: {filepath.name}")
            print(f"   Ruta: {filepath}")
            print(f"   Tamaño actual: {current_size / (1024*1024):.2f} MB")
            if is_downloading:
                print(f"   🔴 STREAMING EN VIVO (archivo descargando)")
            
            # Verificar que no sea HTML
            try:
                with open(filepath, 'rb') as f:
                    first_bytes = f.read(512)
                    if b'<!DOCTYPE' in first_bytes or b'<html' in first_bytes:
                        print(f"❌ Archivo es HTML!")
                        return "Error: Archivo es HTML (error de descarga)", 500
            except Exception as e:
                print(f"⚠️ Error verificando archivo: {e}")
            
            # Para archivos en descarga, usar tamaño grande para permitir crecimiento
            if is_downloading:
                file_size = 10 * 1024 * 1024 * 1024  # 10GB (máximo teórico)
                print(f"   📏 Usando tamaño dinámico para descarga activa")
            else:
                file_size = filepath.stat().st_size
            
            range_header = request.headers.get('Range')
            
            if range_header:
                print(f"📊 Range request: {range_header}")
                byte_range = range_header.replace('bytes=', '').split('-')
                start = int(byte_range[0]) if byte_range[0] else 0
                end_requested = int(byte_range[1]) if byte_range[1] else None
                
                # Para archivos en descarga, limitar end al tamaño actual
                if is_downloading:
                    current_actual_size = filepath.stat().st_size if filepath.exists() else 0
                    end = min(end_requested if end_requested else file_size, current_actual_size - 1)
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
                            # Verificar tamaño actual del archivo
                            if is_downloading and filepath.exists():
                                current_file_size = filepath.stat().st_size
                                current_position = f.tell()
                                
                                # Si alcanzamos el final, esperar más datos
                                if current_position >= current_file_size:
                                    print(f"   ⏸️ Esperando más datos... ({current_position / (1024*1024):.1f} MB)")
                                    time.sleep(1)
                                    continue
                            
                            chunk_size = min(1024 * 1024, remaining)  # 1MB chunks
                            data = f.read(chunk_size)
                            
                            if not data:
                                if is_downloading:
                                    # Para archivos en descarga, esperar más datos
                                    time.sleep(0.5)
                                    continue
                                else:
                                    # Archivo completo, no hay más datos
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
                    print(f"   ⚠️ Advertencia: cliente no soporta Range, streaming puede fallar")
                return send_file(filepath, mimetype='video/mp4')
                
        except Exception as e:
            print(f"❌ ERROR en serve_file: {e}")
            import traceback
            traceback.print_exc()
            return f"Error interno: {str(e)}", 500
    
    @stream.route('/status/<torrent_hash>')
    def torrent_status(torrent_hash):
        """Estado de descarga"""
        status = dm.get_download_status(torrent_hash)
        return jsonify(status if status else {'error': 'Not found'})
    
    stream.run(host='0.0.0.0', port=STREAM_PORT, threaded=True)

# ============= MAIN =============

if __name__ == '__main__':
    stream_thread = threading.Thread(target=stream_app, daemon=True)
    stream_thread.start()
    
    def periodic_cleanup():
        while True:
            time.sleep(3600)
            dm.cleanup_cache()
    
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain('certs/cert.pem', 'certs/key.pem')
    
    print("\n" + "="*60)
    print("🚀 SISTEMA ALLDEBRID FAKE - MODO PRODUCCIÓN")
    print("="*60)
    print(f"📡 API: https://0.0.0.0:443")
    print(f"🎬 Stream: http://0.0.0.0:{STREAM_PORT}")
    print(f"💾 Caché: {DOWNLOAD_DIR}")
    print(f"🔧 qBittorrent: {'✅ Conectado' if qbt_client else '❌ Desconectado'}")
    print(f"📥 JDownloader: {'✅ Conectado' if jd_device else '❌ Desconectado'}")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=443, ssl_context=context, debug=False, threaded=True)