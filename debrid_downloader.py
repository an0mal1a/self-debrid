import threading
import requests
import hashlib
import time
import re

from config import *

# ============= GESTOR DE DESCARGAS =============
class DownloadManager:
    def __init__(self):
        self.active_downloads = {}
        self.completed_files = {}
        self.file_map = {}  # Mapeo ID -> archivo real
    
    def get_file_hash(self, url):
        """Extrae ID único del archivo (no de la URL completa)"""
        # Para 1fichier, usar el ID del archivo directamente
        if '1fichier.com' in url.lower():
            match = re.search(r'\?([a-z0-9]+)', url)
            if match:
                fichier_id = match.group(1)
                print(f"🆔 1fichier ID: {fichier_id}")
                return fichier_id
        
        # Para otros servicios, usar hash de la URL
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    
    def find_existing_file(self, file_id):
        """Busca archivo existente SOLO si el file_id coincide exactamente"""
        # 1. Buscar en el mapeo
        if file_id in self.file_map:
            filepath = Path(self.file_map[file_id])
            if filepath.exists():
                print(f"✅ Archivo en mapeo: {filepath.name}")
                return filepath
        
        # 2. Buscar archivos que empiezan EXACTAMENTE con el ID
        pattern = f"{file_id}_*"
        matches = list(DOWNLOAD_DIR.glob(pattern))
        if matches:
            filepath = matches[0]
            print(f"✅ Archivo encontrado (ID exacto): {filepath.name}")
            self.file_map[file_id] = str(filepath)
            return filepath
        
        # 3. Buscar recursivamente archivos con el ID en el nombre
        for filepath in DOWNLOAD_DIR.rglob(f"{file_id}_*"):
            if filepath.is_file() and filepath.suffix in ['.mkv', '.mp4', '.avi']:
                print(f"✅ Archivo en subcarpeta (ID exacto): {filepath}")
                self.file_map[file_id] = str(filepath)
                return filepath
        
        # Si no encuentra nada con el ID exacto, devolver None
        print(f"❌ No se encontró archivo con ID: {file_id}")
        return None
    
    def is_magnet(self, url):
        return url.startswith('magnet:')
    
    def is_torrent(self, url):
        return url.endswith('.torrent') or 'torrent' in url.lower()
    
    def extract_hash_from_magnet(self, magnet):
        """Extrae hash de magnet link"""
        match = re.search(r'btih:([a-fA-F0-9]{40})', magnet)
        if match:
            return match.group(1).lower()
        return None
    
    def add_torrent(self, url):
        """Añade torrent a qBittorrent y retorna info del archivo"""
        if not qbt_client:
            return None
        
        try:
            if self.is_magnet(url):
                torrent_hash = self.extract_hash_from_magnet(url)
                qbt_client.torrents_add(urls=url, save_path=str(DOWNLOAD_DIR))
            else:
                qbt_client.torrents_add(urls=url, save_path=str(DOWNLOAD_DIR))
                torrent_hash = None
            
            time.sleep(2)
            torrents = qbt_client.torrents_info()
            
            if torrent_hash:
                torrent = next((t for t in torrents if t.hash.lower() == torrent_hash), None)
            else:
                torrent = max(torrents, key=lambda t: t.added_on) if torrents else None
            
            if not torrent:
                return None
            
            files = torrent.files
            if not files:
                time.sleep(3)
                files = qbt_client.torrents_files(torrent.hash)
            
            largest_file = max(files, key=lambda f: f.size)
            
            for i, f in enumerate(files):
                if i == largest_file.id:
                    qbt_client.torrents_file_priority(
                        torrent_hash=torrent.hash,
                        file_ids=i,
                        priority=7
                    )
                else:
                    qbt_client.torrents_file_priority(
                        torrent_hash=torrent.hash,
                        file_ids=i,
                        priority=0
                    )
            
            file_path = DOWNLOAD_DIR / largest_file.name
            
            return {
                'hash': torrent.hash,
                'name': largest_file.name,
                'size': largest_file.size,
                'path': str(file_path),
                'progress': torrent.progress * 100,
                'status': torrent.state
            }
            
        except Exception as e:
            print(f"❌ Error añadiendo torrent: {e}")
            return None
    
    def add_to_jdownloader(self, url, file_id):
        """Añade URL a JDownloader usando el file_id como identificador"""
        if not jd_device:
            print("⚠️ JDownloader no configurado o no disponible.")
            return None
        
        package_name = f"KODI_{file_id}"
        
        # VERIFICAR SI YA EXISTE
        existing = self.find_existing_file(file_id)
        if existing:
            return {
                'filename': existing.name,
                'filepath': str(existing),
                'filesize': existing.stat().st_size,
                'from_cache': True
            }
        
        try:
            print(f"📥 Añadiendo a JDownloader: {url}")
            print(f"📦 Paquete: {package_name}")
            
            if jd_device == "local":
                print("⚠️ Modo JDownloader local activo")
                print(f"   Configurar para descargar en: {DOWNLOAD_DIR}")
                print(f"   Y renombrar como: {file_id}_{{nombre}}")
                return {
                    'filename': f'{file_id}.unknown',
                    'filepath': str(DOWNLOAD_DIR / f'{file_id}.unknown'),
                    'filesize': 0
                }
            else:
                linkgrabber = jd_device.linkgrabber
                downloads = jd_device.downloads
                
                print("➕ Añadiendo link a Linkgrabber...")
                linkgrabber.add_links([{
                    "autostart": False,
                    "links": url,
                    "packageName": package_name,
                    "destinationFolder": str(DOWNLOAD_DIR),
                    "overwritePackagizerRules": True
                }])
                
                print("⏳ Esperando procesamiento en Linkgrabber...")
                max_retries = 15
                current_package = None
                
                for i in range(max_retries):
                    time.sleep(2)
                    packages = linkgrabber.query_packages()
                    for pkg in packages:
                        if package_name in pkg.get('name', ''):
                            current_package = pkg
                            break
                    
                    if current_package:
                        print(f"✅ Paquete encontrado: {current_package.get('name')}")
                        break
                    print(f"   Intento {i+1}/{max_retries}...")
                else:
                    print(f"❌ Timeout en Linkgrabber")
                    return None
                
                # Obtener info del archivo
                links = linkgrabber.query_links([{
                    "packageUUIDs": [current_package['uuid']]
                }])
                
                if not links:
                    print("⚠️ No hay archivos en el paquete")
                    return None
                
                link = links[0]
                original_filename = link.get('name', 'unknown')
                filesize = link.get('bytesTotal', 0)
                
                # Renombrar con el ID
                new_filename = f"{file_id}_{original_filename}"
                print(f"📝 Renombrando: {original_filename}")
                print(f"   → {new_filename}")
                
                try:
                    linkgrabber.rename_link(link['uuid'], new_filename)
                except Exception as e:
                    print(f"⚠️ No se pudo renombrar: {e}")
                    new_filename = original_filename
                
                # IMPORTANTE: Configurar descarga directa (sin carpeta)
                try:
                    linkgrabber.set_download_directory(
                        str(DOWNLOAD_DIR),
                        [current_package['uuid']]
                    )
                except:
                    pass
                
                filepath = DOWNLOAD_DIR / new_filename
                
                # Mover a descargas
                print("📤 Moviendo a descargas...")
                linkgrabber.move_to_downloadlist([current_package['uuid']], [])
                
                # Esperar en downloads
                print("⏳ Esperando en descargas...")
                for i in range(max_retries):
                    time.sleep(2)
                    packages = downloads.query_packages()
                    for pkg in packages:
                        if package_name in pkg.get('name', ''):
                            print(f"✅ Paquete en descargas")
                            break
                    else:
                        print(f"   Intento {i+1}/{max_retries}...")
                        continue
                    break
                else:
                    print(f"⚠️ Timeout en descargas (puede estar descargando)")
                
                print(f"📄 Archivo: {new_filename}")
                print(f"📦 Tamaño: {filesize / (1024*1024):.2f} MB")
                
                # Iniciar descarga
                print("▶️ Iniciando descarga...")
                try:
                    downloads.force_download([link['uuid']], [current_package['uuid']])
                    print("✅ Descarga iniciada")
                except:
                    try:
                        jd_device.downloadcontroller.start()
                        print("✅ Descarga iniciada (alternativo)")
                    except:
                        print("⚠️ Descarga en cola")
                
                # Guardar en mapeo
                self.file_map[file_id] = str(filepath)
                
                return {
                    'uuid': link['uuid'],
                    'filename': new_filename,
                    'filepath': str(filepath),
                    'filesize': filesize,
                    'package_uuid': current_package['uuid']
                }
                
        except Exception as e:
            print(f"❌ Error con JDownloader: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def download_direct(self, url, file_id):
        """Descarga directa con API de 1fichier usando file_id"""
        
        # VERIFICAR SI YA EXISTE
        existing = self.find_existing_file(file_id)
        if existing:
            return str(existing), existing.name, existing.stat().st_size
        
        try:
            # Extraer FILE_ID de 1fichier
            match = re.search(r'\?([a-z0-9]+)', url)
            if not match:
                error_msg = "No se pudo extraer FILE_ID de la URL"
                print(f"❌ {error_msg}")
                return None, error_msg, 0
            
            fichier_id = match.group(1)
            info_url = f"https://1f.stpn.eu.org/api/info/{fichier_id}"
            
            print(f"ℹ️ Obteniendo info: {info_url}")
            headers = {'User-Agent': 'curl/7.68.0', 'Accept': '*/*'}
            
            info_response = requests.get(info_url, headers=headers, timeout=15)
            info_response.raise_for_status()
            info_data = info_response.json()

            if not info_data.get("ok"):
                error_msg = f"API error: {info_data.get('error', 'Unknown')}"
                print(f"❌ {error_msg}")
                return None, error_msg, 0

            original_filename = info_data.get("filename", "unknown.file")
            filesize = int(info_data.get("filesize", 0))
            
            # Nombre con ID como prefijo
            filename = f"{file_id}_{original_filename}"
            filepath = DOWNLOAD_DIR / filename
            
            print(f"✅ Info: {original_filename} ({filesize / (1024*1024):.2f} MB)")
            print(f"📝 Guardando como: {filename}")

            if filepath.exists():
                print(f"↪️ Archivo ya existe en caché")
                self.file_map[file_id] = str(filepath)
                return str(filepath), filename, filesize

            # Descargar en background
            def _download():
                try:
                    download_url = f"https://1f.stpn.eu.org/api/download/{fichier_id}"
                    print(f"⬇️ Descargando: {download_url}")
                    
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    
                    with requests.get(download_url, headers=headers, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        total_size = int(r.headers.get('content-length', filesize))
                        
                        with open(filepath, 'wb') as f:
                            downloaded = 0
                            last_log = 0
                            for chunk in r.iter_content(chunk_size=1024*1024):
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size > 0:
                                    progress = (downloaded / total_size) * 100
                                    if progress - last_log >= 10:
                                        print(f"📊 Progreso: {progress:.1f}%")
                                        last_log = progress

                    print(f"✅ Descarga completada: {filename}")
                    self.file_map[file_id] = str(filepath)
                    self.completed_files[file_id] = str(filepath)
                except Exception as e:
                    print(f"❌ Error descargando: {e}")
                    if filepath.exists():
                        filepath.unlink()
            
            thread = threading.Thread(target=_download, daemon=True)
            thread.start()
            self.active_downloads[file_id] = thread
            
            return str(filepath), filename, filesize
            
        except Exception as e:
            error_msg = f"Error procesando link: {e}"
            print(f"❌ {error_msg}")
            import traceback
            traceback.print_exc()
            return None, error_msg, 0
    
    def get_download_status(self, torrent_hash):
        """Obtiene estado de un torrent"""
        if not qbt_client:
            return None
        
        try:
            torrent = qbt_client.torrents_info(torrent_hashes=torrent_hash)
            if torrent:
                t = torrent[0]
                return {
                    'progress': t.progress * 100,
                    'downloaded': t.downloaded,
                    'size': t.size,
                    'speed': t.dlspeed,
                    'eta': t.eta,
                    'state': t.state
                }
        except:
            pass
        return None
    
    def cleanup_cache(self):
        """Limpia caché antigua si supera el límite"""
        total_size = sum(f.stat().st_size for f in DOWNLOAD_DIR.glob('*') if f.is_file())
        total_gb = total_size / (1024**3)
        
        if total_gb > MAX_CACHE_SIZE_GB:
            print(f"🧹 Limpiando caché ({total_gb:.2f}GB > {MAX_CACHE_SIZE_GB}GB)")
            files = sorted(DOWNLOAD_DIR.glob('*'), key=lambda f: f.stat().st_mtime)
            
            for old_file in files[:len(files)//2]:
                try:
                    old_file.unlink()
                    print(f"🗑️ Eliminado: {old_file.name}")
                except:
                    pass