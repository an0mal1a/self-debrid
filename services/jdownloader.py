import time
import threading
from typing import Optional, Dict


class JDownloaderService:
    SESSION_REFRESH_SECONDS = 30 * 60

    def __init__(self, email: str, password: str, device_name: str):
        self.email = email
        self.password = password
        self.device_name = device_name
        self.client = None
        self.device = None
        self.connected = False
        self._connected_at = None
        self._connection_lock = threading.RLock()
        
    def connect(self) -> bool:
        """Connect to MyJDownloader"""
        with self._connection_lock:
            return self._connect()

    def _connect(self) -> bool:
        """Create a new MyJDownloader session. Caller must hold the lock."""
        if not self.email or not self.password:
            print("⚠️ JDownloader: No credentials, using local mode")
            self.device = "local"
            self.connected = True
            self._connected_at = time.monotonic()
            return True
        
        try:
            import myjdapi
            
            jd = myjdapi.Myjdapi()
            jd.set_app_key("SELF_DEBRID_APP")
            jd.connect(self.email, self.password)
            jd.update_devices()
            
            device = jd.get_device(self.device_name)
            if device is None:
                raise RuntimeError(f"Device not found: {self.device_name}")

            self.client = jd
            self.device = device
            self.connected = True
            self._connected_at = time.monotonic()
            
            print(f"✅ JDownloader: Connected to {self.device_name}")
            return True
            
        except ImportError:
            print("⚠️ myjdapi not installed. Install: pip install myjdapi")
        except Exception as e:
            print(f"❌ JDownloader connection failed: {e}")

        self.client = None
        self.device = None
        self.connected = False
        self._connected_at = None
        return False

    def ensure_connected(self) -> bool:
        """Renew an expired session, or reconnect after a previous request failed."""
        with self._connection_lock:
            if self.device == "local" and self.connected:
                return True

            session_is_fresh = (
                self.connected
                and self.client is not None
                and self._connected_at is not None
                and time.monotonic() - self._connected_at < self.SESSION_REFRESH_SECONDS
            )
            if session_is_fresh:
                return True

            if self.connected:
                print("🔄 JDownloader: Refreshing session")
            else:
                print("🔄 JDownloader: Reconnecting")
            return self._connect()

    def _mark_disconnected(self):
        """Discard an unusable remote session so the next operation reconnects."""
        with self._connection_lock:
            if self.device != "local":
                self.client = None
                self.device = None
                self.connected = False
                self._connected_at = None
    
    def add_link(self, url: str, file_id: str, download_dir: str) -> Optional[Dict]:
        """Add link to JDownloader and return file info"""
        if not self.ensure_connected():
            return None
        
        package_name = f"SELF_DEBRID_{file_id}"
        
        try:
            if self.device == "local":
                print("⚠️ Local mode: Configure JDownloader manually")
                return {
                    'filename': f'{file_id}.unknown',
                    'filepath': f"{download_dir}/{file_id}.unknown",
                    'filesize': 0
                }
            
            linkgrabber = self.device.linkgrabber
            
            # Add link
            linkgrabber.add_links([{
                "autostart": False,
                "links": url,
                "packageName": package_name,
                "destinationFolder": download_dir,
                "overwritePackagizerRules": True
            }])
            
            # Wait for package
            print(f"⏳ Waiting for package: {package_name}")
            package = self._wait_for_package(linkgrabber, package_name, timeout=30)
            
            if not package:
                print("❌ Package not found in linkgrabber")
                return None
            
            # Get file info
            links = linkgrabber.query_links([{
                "packageUUIDs": [package['uuid']]
            }])
            
            if not links:
                return None
            
            link = links[0]
            original_filename = link.get('name', 'unknown')
            filesize = link.get('bytesTotal', 0)
            new_filename = f"{file_id}_{original_filename}"
            
            # Rename with ID
            try:
                linkgrabber.rename_link(link['uuid'], new_filename)
                print(f"📝 Renamed: {new_filename}")
            except:
                new_filename = original_filename
            
            # Move to downloads
            linkgrabber.move_to_downloadlist([package['uuid']], [])
            
            # Start download
            downloads = self.device.downloads
            time.sleep(2)
            
            try:
                downloads.force_download([link['uuid']], [package['uuid']])
                print("✅ Download started")
            except:
                try:
                    self.device.downloadcontroller.start()
                except:
                    pass
            
            return {
                'uuid': link['uuid'],
                'filename': new_filename,
                'filepath': f"{download_dir}/{new_filename}",
                'filesize': filesize,
                'package_uuid': package['uuid']
            }
            
        except Exception as e:
            print(f"❌ JDownloader error: {e}")
            import traceback
            traceback.print_exc()
            self._mark_disconnected()
            # Restore the session now so a transient MyJDownloader failure does
            # not require restarting the API before the next request can work.
            self.ensure_connected()
            return None
    
    def _wait_for_package(self, linkgrabber, package_name: str, timeout: int = 30):
        """Wait for package to appear in linkgrabber"""
        for i in range(timeout // 2):
            time.sleep(2)
            packages = linkgrabber.query_packages()
            for pkg in packages:
                if package_name in pkg.get('name', ''):
                    return pkg
        return None
