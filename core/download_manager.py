# core/download_manager.py
"""
Main download manager - coordinates JDownloader and qBittorrent
"""

import hashlib
import re
from pathlib import Path
from typing import Optional, Dict

class DownloadManager:
    def __init__(self, download_dir: Path, jd_service, qb_service):
        self.download_dir = download_dir
        self.jd_service = jd_service
        self.qb_service = qb_service
        
        self.active_downloads = {}
        self.completed_files = {}
        self.file_map = {}  # ID -> filepath mapping
    
    def get_file_hash(self, url: str) -> str:
        """Extract unique file ID from URL"""
        # For 1fichier, use the file ID directly
        if '1fichier.com' in url.lower():
            match = re.search(r'\?([a-z0-9]+)', url)
            if match:
                fichier_id = match.group(1)
                print(f"🆔 1fichier ID: {fichier_id}")
                return fichier_id
        
        # For other services, use hash of URL
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    
    def is_magnet(self, url: str) -> bool:
        """Check if URL is magnet link"""
        return url.startswith('magnet:')
    
    def is_torrent(self, url: str) -> bool:
        """Check if URL is torrent file"""
        return url.endswith('.torrent') or 'torrent' in url.lower()
    
    def find_existing_file(self, file_id: str) -> Optional[Path]:
        """Find existing file by ID (strict matching)"""
        # 1. Check mapping
        if file_id in self.file_map:
            filepath = Path(self.file_map[file_id])
            if filepath.exists():
                print(f"✅ Archivo en mapeo: {filepath.name}")
                return filepath
        
        # 2. Search files starting with ID
        pattern = f"{file_id}_*"
        matches = list(self.download_dir.glob(pattern))
        if matches:
            filepath = matches[0]
            print(f"✅ Archivo encontrado (ID exacto): {filepath.name}")
            self.file_map[file_id] = str(filepath)
            return filepath
        
        # 3. Search recursively
        for filepath in self.download_dir.rglob(f"{file_id}_*"):
            if filepath.is_file() and filepath.suffix in ['.mkv', '.mp4', '.avi']:
                print(f"✅ Archivo en subcarpeta (ID exacto): {filepath}")
                self.file_map[file_id] = str(filepath)
                return filepath
        
        # Not found
        print(f"❌ No se encontró archivo con ID: {file_id}")
        return None
    
    def add_torrent(self, url: str) -> Optional[Dict]:
        """Add torrent via qBittorrent"""
        if not self.qb_service or not self.qb_service.connected:
            print("⚠️ qBittorrent no disponible")
            return None
        
        return self.qb_service.add_torrent(url, str(self.download_dir))
    
    def add_to_jdownloader(self, url: str, file_id: str) -> Optional[Dict]:
        """Add direct download via JDownloader"""
        if not self.jd_service or not self.jd_service.connected:
            print("⚠️ JDownloader no disponible")
            return None
        
        # Check if already exists
        existing = self.find_existing_file(file_id)
        if existing:
            return {
                'filename': existing.name,
                'filepath': str(existing),
                'filesize': existing.stat().st_size,
                'from_cache': True
            }
        
        # Add to JDownloader
        result = self.jd_service.add_link(url, file_id, str(self.download_dir))
        
        if result:
            self.file_map[file_id] = result['filepath']
        
        return result
    
    def get_download_status(self, torrent_hash: str) -> Optional[Dict]:
        """Get torrent download status"""
        if not self.qb_service or not self.qb_service.connected:
            return None
        
        return self.qb_service.get_status(torrent_hash)
    
    def cleanup_cache(self):
        """Clean old files if cache exceeds limit"""
        from config.settings import Config
        
        total_size = sum(
            f.stat().st_size 
            for f in self.download_dir.glob('*') 
            if f.is_file()
        )
        total_gb = total_size / (1024**3)
        
        if total_gb > Config.MAX_CACHE_SIZE_GB:
            print(f"🧹 Limpiando caché ({total_gb:.2f}GB > {Config.MAX_CACHE_SIZE_GB}GB)")
            
            # Sort by modification time (oldest first)
            files = sorted(
                self.download_dir.glob('*'),
                key=lambda f: f.stat().st_mtime
            )
            
            # Delete oldest half
            for old_file in files[:len(files)//2]:
                try:
                    old_file.unlink()
                    print(f"🗑️ Eliminado: {old_file.name}")
                except:
                    pass