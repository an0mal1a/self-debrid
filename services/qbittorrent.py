from typing import Optional, Dict
import qbittorrentapi
import time
import re

class QBittorrentService:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.client = None
        self.connected = False
        
        try:
            self.client = qbittorrentapi.Client(
                host=host,
                port=port,
                username=username,
                password=password
            )
            self.client.auth_log_in()
            self.connected = True
            print("✅ qBittorrent: Connected")
        except Exception as e:
            print(f"⚠️ qBittorrent: Not available - {e}")
    
    def add_torrent(self, url: str, save_path: str) -> Optional[Dict]:
        """Add torrent and return largest file info"""
        if not self.connected:
            return None
        
        try:
            # Extract hash if magnet
            torrent_hash = None
            if url.startswith('magnet:'):
                match = re.search(r'btih:([a-fA-F0-9]{40})', url)
                if match:
                    torrent_hash = match.group(1).lower()
            
            # Add torrent
            self.client.torrents_add(urls=url, save_path=save_path)
            time.sleep(2)
            
            # Find torrent
            torrents = self.client.torrents_info()
            if torrent_hash:
                torrent = next((t for t in torrents if t.hash.lower() == torrent_hash), None)
            else:
                torrent = max(torrents, key=lambda t: t.added_on) if torrents else None
            
            if not torrent:
                return None
            
            # Get largest file
            files = torrent.files or self.client.torrents_files(torrent.hash)
            largest_file = max(files, key=lambda f: f.size)
            
            # Prioritize only largest file
            for i, f in enumerate(files):
                priority = 7 if i == largest_file.id else 0
                self.client.torrents_file_priority(
                    torrent_hash=torrent.hash,
                    file_ids=i,
                    priority=priority
                )
            
            return {
                'hash': torrent.hash,
                'name': largest_file.name,
                'size': largest_file.size,
                'path': f"{save_path}/{largest_file.name}",
                'progress': torrent.progress * 100,
                'status': torrent.state
            }
            
        except Exception as e:
            print(f"❌ qBittorrent error: {e}")
            return None
    
    def get_status(self, torrent_hash: str) -> Optional[Dict]:
        """Get torrent download status"""
        if not self.connected:
            return None
        
        try:
            torrents = self.client.torrents_info(torrent_hashes=torrent_hash)
            if torrents:
                t = torrents[0]
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