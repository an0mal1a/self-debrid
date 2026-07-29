"""
REST API endpoints compatible with AllDebrid API
"""

from flask import Flask, jsonify, render_template_string, request
from urllib.parse import quote, urlencode
import time

from config.settings import Config
from core.pin_auth import PIN_TTL_SECONDS, PinAuthManager


SUPPORTED_HOSTS = {
    '1fichier': {
        'name': '1fichier',
        'type': 'free',
        'domains': ['1fichier.com'],
        'regexp': r'https?://(?:www\.)?1fichier\.com/\?\w+',
        'status': True,
    },
    'mediafire': {
        'name': 'mediafire',
        'type': 'free',
        'domains': ['mediafire.com'],
        'regexp': r'https?://(?:www\.)?mediafire\.com/.+',
        'status': True,
    },
    'mega': {
        'name': 'mega',
        'type': 'free',
        'domains': ['mega.nz', 'mega.co.nz'],
        'regexp': r'https?://(?:www\.)?mega\.(?:nz|co\.nz)/.+',
        'status': True,
    },
    'rapidgator': {
        'name': 'rapidgator',
        'type': 'free',
        'domains': ['rapidgator.net'],
        'regexp': r'https?://(?:www\.)?rapidgator\.net/.+',
        'status': True,
    },
    'uploaded': {
        'name': 'uploaded',
        'type': 'free',
        'domains': ['uploaded.net', 'ul.to'],
        'regexp': r'https?://(?:www\.)?(?:uploaded\.net|ul\.to)/.+',
        'status': True,
    },
}

PIN_PAGE_TEMPLATE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Self-Debrid · Vincular Kodi</title>
<style>body{font-family:system-ui,sans-serif;background:#101820;color:#eef4f7;margin:0;display:grid;min-height:100vh;place-items:center}.card{width:min(92vw,410px);padding:2rem;border-radius:14px;background:#1b2b34;box-sizing:border-box}h1{margin-top:0}input{box-sizing:border-box;width:100%;padding:.8rem;font-size:1.3rem;text-align:center;letter-spacing:.25rem;text-transform:uppercase}button{width:100%;margin-top:1rem;padding:.8rem;border:0;border-radius:6px;background:#1683a0;color:white;font-size:1rem}.ok{color:#7ee787}.error{color:#ff8a8a}.hint{color:#b6c5cc;font-size:.92rem}</style>
</head><body><main class="card"><h1>Vincular Kodi</h1>
{% if result == 'activated' %}<p class="ok">Código aceptado. Puedes volver a Kodi.</p>
{% elif result == 'expired' %}<p class="error">El código ha caducado. Genera uno nuevo en Kodi.</p>
{% elif result == 'invalid' %}<p class="error">No existe ningún código activo con ese valor.</p>{% endif %}
{% if result != 'activated' %}<p class="hint">Escribe el código de cuatro caracteres que muestra Kodi.</p><form method="post"><input name="pin" value="{{ pin }}" maxlength="4" autocomplete="one-time-code" required autofocus><button type="submit">Autorizar dispositivo</button></form>{% endif %}
</main></body></html>"""

def create_api_app(download_manager):
    """Create Flask app with API routes"""
    app = Flask(__name__)
    pin_auth = PinAuthManager()
    app.extensions['pin_auth'] = pin_auth
    
    @app.before_request
    def log_request():
        print(f"\n{'='*60}")
        print(f"🔵 {request.method} {request.path}")
        print(f"Args: {dict(request.args)}")
        print(f"{'='*60}\n")

    def pin_base_url():
        """Get the browser URL to show in Kodi without hard-coding a LAN address."""
        return Config.PIN_BASE_URL or request.url_root.rstrip('/')

    def pin_error(code, message):
        # AllDebrid API clients generally inspect this JSON object, not an HTTP
        # status code, so retain HTTP 200 for compatibility.
        return jsonify({
            'status': 'error',
            'error': {'code': code, 'message': message},
        })

    def hosts_payload(hosts_only=False):
        data = {'hosts': SUPPORTED_HOSTS}
        if not hosts_only:
            data.update({'streams': {}, 'redirectors': {}})
        return data

    @app.route('/v4/hosts', methods=['GET'])
    @app.route('/v4.1/hosts', methods=['GET'])
    def hosts():
        """Return self-hosted equivalents of AllDebrid's supported hosters."""
        return jsonify({
            'status': 'success',
            'data': hosts_payload(hosts_only='hostsOnly' in request.args),
        })

    @app.route('/v4/hosts/domains', methods=['GET'])
    def host_domains():
        return jsonify({
            'status': 'success',
            'data': {
                'hosts': [
                    domain
                    for host in SUPPORTED_HOSTS.values()
                    for domain in host['domains']
                ],
                'streams': [],
                'redirectors': [],
            },
        })

    @app.route('/v4/hosts/priority', methods=['GET'])
    def host_priority():
        return jsonify({
            'status': 'success',
            'data': {
                'hosts': {
                    name: index
                    for index, name in enumerate(SUPPORTED_HOSTS, start=1)
                },
            },
        })

    @app.route('/v4/pin/get', methods=['GET'])
    @app.route('/v4.1/pin/get', methods=['GET'])
    def pin_get():
        """Start the AllDebrid-compatible device PIN authorization flow."""
        pin_request = pin_auth.create()
        base_url = f"{pin_base_url()}/pin/"
        check_url = f"{pin_base_url()}/v4/pin/check?{urlencode({'check': pin_request.check, 'pin': pin_request.pin})}"
        data = {
            'pin': pin_request.pin,
            'check': pin_request.check,
            'expires_in': PIN_TTL_SECONDS,
            'user_url': f"{base_url}?{urlencode({'pin': pin_request.pin})}",
            'base_url': base_url,
        }
        # Older ResolveURL versions expect the deprecated v4 check_url field.
        if request.path.startswith('/v4/'):
            data['check_url'] = check_url
        return jsonify({'status': 'success', 'data': data})

    @app.route('/v4/pin/check', methods=['GET', 'POST'])
    @app.route('/v4.1/pin/check', methods=['GET', 'POST'])
    def pin_check():
        """Return the API key once the code has been approved in /pin/."""
        pin = request.values.get('pin', '')
        check_token = request.values.get('check', '')
        if not pin or not check_token:
            return pin_error('PIN_INVALID', 'The PIN or check token is missing.')

        result, pin_request = pin_auth.check(pin, check_token)
        if result == 'invalid':
            return pin_error('PIN_INVALID', 'The PIN or check token is invalid.')
        if result == 'expired':
            return pin_error('PIN_EXPIRED', 'The PIN has expired.')

        expires_in = max(0, int(pin_request.expires_at - time.time()))
        data = {'activated': pin_request.activated, 'expires_in': expires_in}
        if pin_request.activated:
            data['apikey'] = pin_request.apikey
        return jsonify({'status': 'success', 'data': data})

    @app.route('/pin', methods=['GET', 'POST'])
    @app.route('/pin/', methods=['GET', 'POST'])
    def pin_authorize_page():
        """Small local page where the code shown by Kodi is approved."""
        pin = request.values.get('pin', '').strip().upper()
        result = None
        if request.method == 'POST':
            result = pin_auth.activate(pin) if pin else 'invalid'
        return render_template_string(PIN_PAGE_TEMPLATE, pin=pin, result=result)
    
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
                    'isSubscribed': True,
                    'isTrial': False,
                    'premiumUntil': int(time.time()) + 31536000,  # 1 year
                    'lang': 'en',
                    'preferedDomain': 'alldebrid.com'
                }
            }
        })

    @app.route('/v4/user/hosts', methods=['GET'])
    @app.route('/v4.1/user/hosts', methods=['GET'])
    def user_hosts():
        return jsonify({
            'status': 'success',
            'data': hosts_payload(hosts_only='hostsOnly' in request.args),
        })
    
    @app.route('/v4/user/history/delete', methods=['GET'])
    def delete_history():
        """Fake endpoint for history deletion"""
        return jsonify({'status': 'success', 'data': {'message': 'OK'}})
    
    return app
