'''
auth.py — Secure Client-Side Authentication for Interview Helper

Architecture & Security:
  - NO database credentials (MongoDB) are embedded in this client app.
  - NO Google OAuth Client Secret is embedded in this client app.
  - All token verification, user persistence, and credit management are
    handled securely by the backend server via REST endpoints.
  - Compiling this app into an executable will NOT expose any database
    credentials, API secrets, or master tokens.
'''

import json
import os
import threading
import urllib.parse
import webbrowser
import http.server
import secrets
import uuid
import requests
_DEFAULT_BACKEND = os.environ.get('INTERVIEW_BACKEND', 'https://interviewserver-aef9.onrender.com/')
_BACKEND_URL = _DEFAULT_BACKEND.rstrip('/')

def set_backend_url(url: str):
    '''Update the backend URL used for all auth & data requests.'''

    global _BACKEND_URL
    if url:
        _BACKEND_URL = url.rstrip('/')
        print(f'[Auth] Backend URL set to: {_BACKEND_URL}')

def get_backend_url() -> str:
    return _BACKEND_URL

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '796706026429-g2ca07gcuigifick01f6d4th7g096t5l.apps.googleusercontent.com')
REDIRECT_URI = 'http://localhost:8765'
SCOPES = 'openid email profile'
_AUTH_URL = 'https://accounts.google.com/o/oauth2/auth'
APP_VERSION = '1.0.0'
_SESSION_FILE = os.path.join(os.path.expanduser('~'), '.interviewhelper_session.json')

def check_app_version(current_version: str=APP_VERSION) -> dict:
    '''
    Check current app version against backend /api/version.
    Returns dict with min_version, latest_version, download_url, and force_update boolean.
    '''

    try:
        url = f'{_BACKEND_URL}/api/version'
        resp = requests.get(url, params={'version': current_version}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {'status': 'update_required' if data.get('force_update') else 'ok', 'min_version': data.get('min_version', '1.0.0'), 'latest_version': data.get('latest_version', '1.0.0'), 'download_url': data.get('download_url', 'https://www.aftaro.com/download'), 'force_update': bool(data.get('force_update', False)), 'message': data.get('message', '')}
    except Exception as e:
        print(f'[Version] Check failed: {e}')
    return {'status': 'ok', 'force_update': False, 'latest_version': APP_VERSION, 'download_url': 'https://www.aftaro.com'}

def load_session() -> dict:
    try:
        with open(_SESSION_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_session(info: dict):
    try:
        with open(_SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
        print(f'[Auth] Session saved for {info.get('email', '?')}')
    except Exception as e:
        print(f'[Auth] Could not save session: {e}')

def clear_session():
    try:
        if os.path.exists(_SESSION_FILE):
            os.unlink(_SESSION_FILE)
        print('[Auth] Session cleared.')
        return
    except Exception as e:
        print(f'[Auth] Could not clear session: {e}')

def validate_session(google_id: str, device_token: str) -> tuple:
    '''
    Validate local session token with the backend server.
    Returns (status, user_doc):
      "valid"      - token matches, user logged in on this device
      "kicked"     - another device logged in, this device is kicked out
      "not_found"  - user not in DB (deleted), clear and re-login
      "db_error"   - server/DB unreachable, use cached session data
    '''

    try:
        url = f'{_BACKEND_URL}/api/auth/validate-session'
        resp = requests.post(url, json={'google_id': google_id, 'device_token': device_token}, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            status = data.get('status', 'db_error')
            user = data.get('user')
            return status, user
        if resp.status_code == 400:
            return ('not_found', None)
    except Exception as e:
        print(f'[Auth] validate_session network exception: {e}')
    return ('db_error', None)

def find_user(google_id: str) -> dict | None:
    '''Fetch user profile document from backend.'''

    try:
        url = f'{_BACKEND_URL}/api/user/profile/{google_id}'
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            return resp.json().get('profile', {})
    except Exception as e:
        print(f'[Auth] find_user network exception: {e}')

def save_profile(google_id: str, profile: dict):
    '''Save user candidate profile to backend.'''

    try:
        url = f'{_BACKEND_URL}/api/user/profile'
        requests.post(url, json={'google_id': google_id, 'profile': profile}, timeout=8)
        print('[Auth] Profile saved to backend')
        return
    except Exception as e:
        print(f'[Auth] save_profile network exception: {e}')

def load_profile(google_id: str) -> dict:
    '''Retrieve saved profile from backend.'''

    profile = find_user(google_id)
    if profile:
        return profile
    return {}

def _build_auth_url(state: str, force_account_select: bool=False) -> str:
    params = {'client_id': GOOGLE_CLIENT_ID, 'redirect_uri': REDIRECT_URI, 'response_type': 'code', 'scope': SCOPES, 'state': state, 'access_type': 'offline'}
    if force_account_select:
        params['prompt'] = 'select_account'
    return _AUTH_URL + '?' + urllib.parse.urlencode(params)

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict = {}
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if 'code' in qs:
            _CallbackHandler.result['code'] = qs['code'][0]
            html = b'<!DOCTYPE html><html><head><title>Interview Helper</title>\n<style>*{margin:0;padding:0;box-sizing:border-box;}\nbody{background:#0a0a12;color:#f3efff;font-family:\'Segoe UI\',sans-serif;\ndisplay:flex;align-items:center;justify-content:center;height:100vh;}\n.card{background:#191926;border:1px solid #453a6e;border-radius:18px;\npadding:48px 56px;text-align:center;}\n.icon{font-size:56px;margin-bottom:16px;}\nh1{color:#cda45e;font-size:22px;margin-bottom:8px;}\np{color:#b2a9d6;font-size:14px;line-height:1.6;}</style></head>\n<body><div class="card"><div class="icon">&#x2705;</div>\n<h1>Login Successful!</h1>\n<p>You can close this tab and return to Interview Helper.</p>\n</div></body></html>'
        else:
            err = qs.get('error', ['unknown'])[0]
            _CallbackHandler.result['error'] = err
            html = b'<!DOCTYPE html><html><body style="background:#0a0a12;color:#e0526e;\nfont-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;">\n<div style="text-align:center;padding:40px;background:#191926;border-radius:16px;">\n<h1>Login failed</h1><p>Please close this tab and try again.</p></div></body></html>'
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *args):
        pass


def do_google_login(on_success=None, on_error=None, on_status=None, force_account_select: bool=False):
    """
    Google OAuth2 login flow:
      1. Opens user's browser to Google Sign-In.
      2. Captures auth 'code' locally on localhost:8765.
      3. Sends auth 'code' to backend server.
      4. Backend uses GOOGLE_CLIENT_SECRET to exchange token and fetch profile.
      5. Backend persists user in MongoDB and returns user document.
    """

    state = secrets.token_hex(8)
    _CallbackHandler.result = {}
    existing_session = load_session()
    device_token = existing_session.get('device_token') or str(uuid.uuid4())
    def _run():
        srv = None
        try:
            if on_status:
                on_status('Starting local auth listener…')
            srv = http.server.HTTPServer(('localhost', 8765), _CallbackHandler)
            srv.timeout = 180
            if on_status:
                on_status('Opening browser — sign in with Google…')
            webbrowser.open(_build_auth_url(state, force_account_select=force_account_select))
            srv.handle_request()
            srv.server_close()
            srv = None
            if 'error' in _CallbackHandler.result:
                raise RuntimeError(f'Google refused login: {_CallbackHandler.result['error']}')
            if 'code' not in _CallbackHandler.result:
                raise RuntimeError('No authorization code received (browser closed?)')
            code = _CallbackHandler.result['code']
            if on_status:
                on_status('Authenticating securely with backend…')
            auth_url = f'{_BACKEND_URL}/api/auth/google'
            resp = requests.post(auth_url, json={'code': code, 'redirect_uri': REDIRECT_URI, 'device_token': device_token}, timeout=20)
            try:
                res_data = resp.json()
            except Exception as exc:
                if srv:
                    try:
                        try:
                            srv.server_close()
                        except Exception:
                            res_data = {}
                    except:
                        pass
                print(f'[Auth] Login exception: {exc}')
                if on_error:
                    on_error(str(exc))
            else:
                if not resp.ok or not res_data.get('success'):
                    err_msg = res_data.get('message') or f'Server response error (status {resp.status_code})'
                    raise RuntimeError(err_msg)
                user_doc = res_data.get('user', {})
                save_session({'google_id': user_doc.get('google_id', ''), 'email': user_doc.get('email', ''), 'name': user_doc.get('name', ''), 'picture': user_doc.get('picture', ''), 'device_token': device_token})
                if on_success:
                    on_success(user_doc)
        except Exception as exc:
            if srv:
                try:
                    srv.server_close()
                except:
                    pass
            print(f'[Auth] Login exception: {exc}')
            if on_error:
                on_error(str(exc))

    threading.Thread(target=_run, daemon=True).start()

def ensure_credit_field(google_id: str):
    '''Backend handles this automatically during auth.'''

def get_credit_seconds(google_id: str) -> int:
    '''Return remaining credit in seconds from backend.'''

    try:
        url = f'{_BACKEND_URL}/api/user/credits/{google_id}'
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            return int(resp.json().get('credit_seconds', 0))
    except Exception as e:
        print(f'[Auth] get_credit_seconds error: {e}')
    return 0

def set_credit_seconds(google_id: str, seconds: int):
    '''Sync remaining credit seconds to backend.'''

    try:
        url = f'{_BACKEND_URL}/api/user/credits/sync'
        requests.post(url, json={'google_id': google_id, 'credit_seconds': max(0, int(seconds))}, timeout=8)
        return
    except Exception as e:
        print(f'[Auth] set_credit_seconds error: {e}')

def redeem_coupon(google_id: str, code: str) -> tuple:
    '''Redeem coupon code on the backend.'''

    try:
        url = f'{_BACKEND_URL}/api/user/redeem-coupon'
        resp = requests.post(url, json={'google_id': google_id, 'code': code}, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and data.get('success'):
            return True, data.get('message', 'Coupon redeemed successfully!')
        return False, data.get('message', 'Coupon redemption failed.')
    except Exception as e:
        print(f'[Auth] redeem_coupon error: {e}')
        return False, f'Redemption failed: {e}'

