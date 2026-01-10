import time
from typing import Optional, Dict

class JDownloaderService:
    def __init__(self, email: str, password: str, device_name: str):
        self.email = email
        self.password = password
        self.device_name = device_name
        self.device = None
        self.connected = False
        
    def connect(self) -> bool:
        """Connect to MyJDownloader"""
        if not self.email or not self.password:
            print("⚠️ JDownloader: No credentials, using local mode")
            self.device = "local"
            self.connected = True
            return True
        
        try:
            import myjdapi
            
            jd = myjdapi.Myjdapi()
            jd.set_app_key("SELF_DEBRID_APP")
            jd.connect(self.email, self.password)
            jd.update_devices()
            
            self.device = jd.get_device(self.device_name)
            self.connected = True
            
            print(f"✅ JDownloader: Connected to {self.device_name}")
            return True
            
        except ImportError:
            print("⚠️ myjdapi not installed. Install: pip install myjdapi")
            return False
        except Exception as e:
            print(f"❌ JDownloader connection failed: {e}")
            return False
    
    def add_link(self, url: str, file_id: str, download_dir: str) -> Optional[Dict]:
        """Add link to JDownloader and return file info"""
        if not self.connected:
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
