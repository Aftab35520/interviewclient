'''
Desktop Audio Transcriber + Azure OpenAI Q&A
Combined Single Window Layout:
  • AI Response panel takes ~78% of window (right side, wide for reading answers)
  • Transcription field is compact (left side, just for monitoring that it's working)
  • Auto mode: sends to AI automatically after 3s of silence (original behavior)
  • Manual mode: transcript only accumulates — you send to AI whenever you want
  • A "Send to AI" button is always available so you have full control either way
  • Luxurious dark/gold theme
  • Always-on-top and semi-transparent
'''

import tkinter as tk
from tkinter import scrolledtext, ttk
import threading
import queue
import datetime
import os
import struct
import sys
import wave
import tempfile
import time
import base64
import io
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
from PIL import ImageGrab
if sys.platform == 'win32' or os.name == 'nt':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('aftaro.interviewhelper.desktopapp.1.0')
    except Exception:
        pass
APP_VERSION = '1.0.0'

def get_resource_path(relative_path: str) -> str:
    '''Get absolute path to resource, works for dev and PyInstaller onedir/onefile bundle.'''

    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        p1 = os.path.join(exe_dir, relative_path)
        if os.path.exists(p1):
            return p1
        p2 = os.path.join(exe_dir, '_internal', relative_path)
        if os.path.exists(p2):
            return p2
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            p3 = os.path.join(meipass, relative_path)
            if os.path.exists(p3):
                return p3
        return p1
    base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

_app_icon_photo = None

def set_window_icon(win):
    '''Set window icon bitmap and photo image across Tk windows.'''

    global _app_icon_photo
    try:
        ico_file = get_resource_path('aftaro.ico')
        if os.path.exists(ico_file):
            pass
        try:
            win.iconbitmap(ico_file)
        except Exception:
            pass
        else:
            png_file = get_resource_path('aftaroLogo.png')
            if os.path.exists(png_file) and _app_icon_photo is None:
                from PIL import Image, ImageTk
                im = Image.open(png_file)
                _app_icon_photo = ImageTk.PhotoImage(im)
            if _app_icon_photo is not None:
                win.iconphoto(True, _app_icon_photo)
    except Exception as e:
        print(f'[Icon] Failed to set window icon: {e}')

try:
    import auth as _auth
    AUTH_AVAILABLE = True
except Exception as _auth_import_err:
    print(f'[Auth] Module not available: {_auth_import_err}')
    AUTH_AVAILABLE = False
    _auth = None
BACKEND_URL = os.environ.get('INTERVIEW_BACKEND', 'https://interviewserver-aef9.onrender.com/')
BUY_URL = os.environ.get('INTERVIEW_BUY_URL', 'https://www.aftaro.com/products/interviewassistent/payment')
if AUTH_AVAILABLE and _auth is not None:
    _auth.set_backend_url(BACKEND_URL)
try:
    import socketio as sio_lib
    SIO_AVAILABLE = True
except ImportError:
    SIO_AVAILABLE = False

class UpdateRequiredScreen(tk.Tk):
    '''Screen displayed when backend specifies that this app version must update.'''

    def __init__(self, version_info: dict):
        super().__init__()
        set_window_icon(self)
        self.title('Aftaro — Update Required')
        self.configure(bg='#08090C')
        self.geometry('540x440')
        self.resizable(False, False)
        if sys.platform == 'win32' or os.name == 'nt':
            self.eval('tk::PlaceWindow . center')
        latest_ver = version_info.get('latest_version', '1.0.0')
        dl_url = version_info.get('download_url', 'https://www.aftaro.com')
        center = tk.Frame(self, bg='#08090C')
        center.pack(expand=True, fill='both', padx=30, pady=30)
        logo_path = get_resource_path('aftaroLogo.png')
        if os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                logo_img = Image.open(logo_path).resize((64, 64), Image.Resampling.LANCZOS)
                self._logo_photo = ImageTk.PhotoImage(logo_img)
                tk.Label(center, image=self._logo_photo, bg='#08090C').pack(pady=(0, 10))
            except Exception:
                tk.Label(center, text='🚀', font=('Segoe UI', 36), bg='#08090C', fg='#2dd4bf').pack(pady=(0, 10))
        else:
            tk.Label(center, text='🚀', font=('Segoe UI', 36), bg='#08090C', fg='#2dd4bf').pack(pady=(0, 10))
        tk.Label(center, text='Update Required', font=('Segoe UI', 20, 'bold'), bg='#08090C', fg='#f1f5f9').pack(pady=(0, 8))
        tk.Label(center, text=f'A new version of Aftaro (v{latest_ver}) is available.\nYour installed version (v{APP_VERSION}) is outdated.\n\nPlease download and install the update to continue.', font=('Segoe UI', 10), bg='#08090C', fg='#94a3b8', justify='center').pack(pady=(0, 24))
        btn_row = tk.Frame(center, bg='#08090C')
        btn_row.pack(fill='x')
        def _open_dl():
            import webbrowser
            webbrowser.open(dl_url)

        tk.Button(btn_row, text='⬇  Download Update', font=('Segoe UI', 11, 'bold'), bg='#2dd4bf', fg='#08090C', activebackground='#5eead4', activeforeground='#08090C', relief='flat', cursor='hand2', padx=20, pady=10, command=_open_dl).pack(side='left', expand=True, fill='x', padx=(0, 8))
        tk.Button(btn_row, text='Exit', font=('Segoe UI', 11), bg='#111318', fg='#94a3b8', relief='flat', cursor='hand2', padx=15, pady=10, command=self.destroy).pack(side='right', padx=(8, 0))


class SocketIOClient:
    '''
    Thin wrapper around python-socketio that:
    - Connects to the backend server (auto-reconnects on drop)
    - Exposes send_query() which streams response_chunk events back
      via on_chunk / on_done / on_error callbacks (called from a daemon thread)
    '''

    def __init__(self, server_url: str):
        self._url = server_url
        self._sio = sio_lib.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=2, reconnection_delay_max=10)
        self._lock = threading.Lock()
        self._pending = {}
        self._connected = False
        @self._sio.on('connect')
        def _on_connect():
            self._connected = True
            print(f'[Backend] Connected → {self._url}')

        @self._sio.on('disconnect')
        def _on_disconnect():
            self._connected = False
            print('[Backend] Disconnected — will reconnect automatically')

        @self._sio.on('response_chunk')
        def _on_chunk(data):
            chunk = data.get('chunk', '')
            with self._lock:
                cb = self._pending.get('on_chunk')
            if cb:
                if chunk:
                    cb(chunk)

        @self._sio.on('response_done')
        def _on_done(data):
            with self._lock:
                cb = self._pending.pop('on_done', None)
                self._pending.pop('on_chunk', None)
                self._pending.pop('on_error', None)
            if cb:
                cb()

        @self._sio.on('response_error')
        def _on_error(data):
            err = data.get('error', 'Unknown error')
            with self._lock:
                cb = self._pending.pop('on_error', None)
                self._pending.pop('on_chunk', None)
                self._pending.pop('on_done', None)
            if cb:
                cb(err)

        threading.Thread(target=self._connect_loop, daemon=True).start()

    def _connect_loop(self):
        '''Try to connect, then keep the sio event loop alive.'''

        while True:
            try:
                self._sio.connect(self._url, transports=['polling', 'websocket'])
                self._sio.wait()
            except Exception as e:
                print(f'[Backend] Connection failed ({e}), retrying in 3s…')
                time.sleep(3)

    def send_query(self, query: str, image_b64: str='', google_id: str='', device_token: str='', on_chunk=None, on_done=None, on_error=None):
        '''Emit a query to the backend from a daemon thread (non-blocking).'''

        if not self._connected:
            if on_error:
                on_error('Not connected to backend server. Retrying…')
            return
        with self._lock:
            self._pending['on_chunk'] = on_chunk
            self._pending['on_done'] = on_done
            self._pending['on_error'] = on_error
        payload = {'query': query}
        if image_b64:
            payload['image'] = image_b64
        if google_id:
            payload['google_id'] = google_id
        if device_token:
            payload['device_token'] = device_token
        def _emit():
            try:
                self._sio.emit('query', payload)
                return
            except Exception as e:
                print(f'[Backend] emit error: {e}')
                with self._lock:
                    cb = self._pending.pop('on_error', None)
                    self._pending.pop('on_chunk', None)
                    self._pending.pop('on_done', None)
                if not None:
                    pass
                if cb:
                    cb(str(e))
            if cb:
                cb(str(e))
                e = None
                del e
                return
            e = None
            del e

        threading.Thread(target=_emit, daemon=True).start()


WDA_MONITOR = 1
WDA_EXCLUDEFROMCAPTURE = 17

def protect_window_from_capture(hwnd):
    try:
        user32 = ctypes.windll.user32
        result = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if result:
            return True
        return user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR) != 0
    except Exception:
        return False

WS_EX_TOOLWINDOW = 128
WS_EX_APPWINDOW = 262144

def hide_from_taskbar(tk_window):
    if os.name != 'nt':
        return
    try:
        hwnd = get_window_handle(tk_window)
        if not hwnd:
            return
    except Exception as e:
        print(f'[Taskbar] {e}')
    try:
        try:
            get_win_long = ctypes.windll.user32.GetWindowLongPtrW
            set_win_long = ctypes.windll.user32.SetWindowLongPtrW
        except AttributeError:
            get_win_long = ctypes.windll.user32.GetWindowLongW
            set_win_long = ctypes.windll.user32.SetWindowLongW
    except Exception as e:
        print(f'[Taskbar] {e}')
    ex_style = get_win_long(hwnd, GWL_EXSTYLE)
    ex_style = (ex_style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    set_win_long(hwnd, GWL_EXSTYLE, ex_style)

def get_window_handle(tk_window):
    try:
        return ctypes.windll.user32.GetParent(tk_window.winfo_id()) or tk_window.winfo_id()
    except Exception:
        return

def _is_loopback_name(device_name: str) -> bool:
    name = (device_name or '').lower()
    return any((k in name for k in ('loopback', 'monitor', 'stereo mix', 'what u hear', 'blackhole', 'soundflower', 'aggregate', 'capture')))

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 32
WS_EX_LAYERED = 524288

def set_click_through(hwnd, enable: bool):
    if not hwnd or os.name != 'nt':
        return
    try:
        try:
            set_win_long = ctypes.windll.user32.SetWindowLongPtrW
            get_win_long = ctypes.windll.user32.GetWindowLongPtrW
        except AttributeError:
            set_win_long = ctypes.windll.user32.SetWindowLongW
            get_win_long = ctypes.windll.user32.GetWindowLongW
    except Exception as e:
        print(f'Error setting click-through: {e}')
    ex_style = get_win_long(hwnd, GWL_EXSTYLE)
    if enable:
        ex_style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        ex_style &= ~WS_EX_TRANSPARENT
    set_win_long(hwnd, GWL_EXSTYLE, ex_style)

import numpy as np
try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    sr = None
    SR_AVAILABLE = False
try:
    try:
        import speech_recognition.audio as _sr_audio
        _orig_get_flac = _sr_audio.get_flac_converter
        def _patched_get_flac():
            try:
                return _orig_get_flac()
            except OSError:
                base_path = os.path.dirname(os.path.abspath(_sr_audio.__file__))
                flac_win = os.path.join(base_path, 'flac-win32.exe')
                if os.path.exists(flac_win):
                    return flac_win
                raise

        _sr_audio.get_flac_converter = _patched_get_flac
    except Exception as _e:
        print(f'[Audio] FLAC patch note: {_e}')
except ImportError:
    sr = None
    SR_AVAILABLE = False
try:
    import pyaudiowpatch as pyaudio
    PA_AVAILABLE = True
except ImportError:
    try:
        import pyaudio
        PA_AVAILABLE = True
    except ImportError:
        pyaudio = None
        PA_AVAILABLE = False
try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    sd = None
    SD_AVAILABLE = False
SC_AVAILABLE = False
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SECONDS = 1.5
OVERLAP_SEC = 0.3
SILENCE_TRIGGER_SEC = 3.0
RECOG_WORKERS = 2
RECOG_PAUSE_SEC = 0.8
RECOG_ENERGY_THRES = 250
WINDOW_ALPHA = 0.86
ALWAYS_ON_TOP = True

class Theme:
    BG_BASE = '#0a0a12'
    BG_SURFACE = '#111119'
    BG_CARD = '#191926'
    BG_INPUT = '#0d0d15'
    BG_AI = '#14101e'
    PURPLE = '#8a6fd6'
    PURPLE_H = '#a68ceb'
    PURPLE_DIM = '#3c2e6b'
    GOLD = '#cda45e'
    GOLD_H = '#e7c17e'
    GOLD_DIM = '#4a3c22'
    TEAL = '#25a882'
    TEAL_H = '#5cd6ac'
    RED = '#a3324a'
    RED_H = '#e0526e'
    AMBER = '#c99a2e'
    TEXT_PRI = '#f3efff'
    TEXT_SEC = '#b2a9d6'
    TEXT_MUTED = '#6c6590'
    TEXT_AI = '#fdfaf3'
    TEXT_BLUE = '#a9c9ff'
    TEXT_GOLD = '#e9cd93'
    BORDER = '#262038'
    BORDER_STR = '#453a6e'
    FONT_UI = 'Segoe UI'
    FONT_MONO = 'Consolas'

class AudioTranscriber:
    def __init__(self, event_q: queue.Queue):
        self.event_q = event_q
        self._stop = threading.Event()
        self.recognizer = sr.Recognizer() if SR_AVAILABLE else None
        self._lock = threading.Lock()
        self._last_speech_time = time.time()
        self._has_pending = False
        self._silence_fired = False
        self._seq_counter = 0
        self._emit_next = 0
        self._result_buf = {}
        self._order_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=RECOG_WORKERS)
        self._configure_recognizer()

    def start(self):
        self._stop.clear()
        with self._lock:
            self._last_speech_time = time.time()
            self._has_pending = False
            self._silence_fired = False
        with self._order_lock:
            self._seq_counter = 0
            self._emit_next = 0
            self._result_buf = {}
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._watch_silence, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _configure_recognizer(self):
        if not self.recognizer:
            return
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.energy_threshold = RECOG_ENERGY_THRES
        self.recognizer.pause_threshold = RECOG_PAUSE_SEC
        self.recognizer.operation_timeout = 8

    def reset_segment(self):
        with self._lock:
            self._last_speech_time = time.time()
            self._has_pending = False
            self._silence_fired = False

    def _watch_silence(self):
        while not self._stop.is_set():
            with self._lock:
                elapsed = time.time() - self._last_speech_time
                has_pending = self._has_pending
                silence_fired = self._silence_fired
            if elapsed >= SILENCE_TRIGGER_SEC and has_pending and not silence_fired:
                with self._lock:
                    self._silence_fired = True
                self.event_q.put(('silence',))
            time.sleep(0.15)

    def _run(self):
        if not SR_AVAILABLE:
            self.event_q.put(('error', 'SpeechRecognition not installed: pip install SpeechRecognition'))
            return
        if PA_AVAILABLE:
            self._run_wasapi_loopback()
            return
        if SD_AVAILABLE:
            self._run_sounddevice()
            return
        self._run_mic_fallback()

    def _run_wasapi_loopback(self):
        pa = pyaudio.PyAudio()
        stream = None
        loopback = None
        try:
            if hasattr(pa, 'get_default_wasapi_loopback'):
                pass
            try:
                loopback = pa.get_default_wasapi_loopback()
            except Exception as e:
                print(f'[Audio] get_default_wasapi_loopback note: {e}')
            else:
                if loopback is None and hasattr(pa, 'get_loopback_device_info_generator'):
                    pass
        except Exception as exc:
            print(f'[Audio] Loopback capture exception: {exc}')
            self.event_q.put(('status', f'Loopback error: {exc} — switching to microphone'))
            self._run_mic_fallback()
        if loopback is None:
            try:
                for i in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(i)
                    name = info.get('name', '').lower()
                    if not info.get('maxInputChannels', 0) > 0:
                        continue
                    if not any((k in name for k in ('loopback', 'stereo mix', 'what u hear'))):
                        continue
                    loopback = info
                    break
            except Exception:
                pass
        if loopback is None:
            self.event_q.put(('status', 'No loopback found — using microphone'))
            self._run_mic_fallback()
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            try:
                pa.terminate()
            except Exception:
                pass
            return
        try:
            dev_idx = loopback['index']
            dev_name = loopback['name']
            native_rate = int(loopback.get('defaultSampleRate', 48000))
            channels = int(loopback.get('maxInputChannels', 2))
            if channels <= 0:
                channels = 2
            self.event_q.put(('status', f'Listening to System Audio ({dev_name})…'))
            print(f'[Audio] Capturing loopback: {dev_name} (rate: {native_rate}, channels: {channels})')
            stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=native_rate, input=True, input_device_index=dev_idx, frames_per_buffer=1024)
            chunk_target_samples = int(SAMPLE_RATE * CHUNK_SECONDS)
            overlap_target_samples = int(SAMPLE_RATE * OVERLAP_SEC)
            sample_buffer = []
            while not self._stop.is_set():
                raw = stream.read(1024, exception_on_overflow=False)
                if not raw:
                    time.sleep(0.01)
                    continue
                samples = np.frombuffer(raw, dtype=np.int16)
                if channels > 1:
                    mono = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
                else:
                    mono = samples
                if native_rate != SAMPLE_RATE:
                    num_target = int(len(mono) * SAMPLE_RATE / native_rate)
                    if num_target > 0:
                        resampled = np.interp(np.linspace(0, len(mono), num_target, endpoint=False), np.arange(len(mono)), mono).astype(np.int16)
                    else:
                        resampled = mono
                else:
                    resampled = mono
                sample_buffer.extend(resampled.tolist())
                if len(sample_buffer) >= chunk_target_samples:
                    chunk = sample_buffer[:chunk_target_samples]
                    sample_buffer = sample_buffer[chunk_target_samples - overlap_target_samples:]
                    max_vol = max((abs(x) for x in chunk))
                    if max_vol > 80:
                        self._dispatch(chunk, is_int16=True)
        except Exception as exc:
            print(f'[Audio] Loopback capture exception: {exc}')
            self.event_q.put(('status', f'Loopback error: {exc} — switching to microphone'))
            self._run_mic_fallback()
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        try:
            pa.terminate()
        except Exception:
            return

    def _run_sounddevice(self):
        try:
            idx = None
            for i, info in enumerate(sd.query_devices()):
                if not info['max_input_channels'] > 0:
                    continue
                if not _is_loopback_name(info['name']):
                    continue
                idx = i
                break
            if idx is None:
                default_device = sd.default.device
                if isinstance(default_device, (list, tuple)) and default_device[0] is not None:
                    idx = default_device[0]
                elif isinstance(default_device, int) and default_device >= 0:
                    idx = default_device
            if idx is None:
                self._run_mic_fallback()
                return
            info = sd.query_devices(idx)
            self.event_q.put(('status', f'Listening via: {info['name']}'))
            chunk_frames = int(SAMPLE_RATE * CHUNK_SECONDS)
            overlap_frames = int(SAMPLE_RATE * OVERLAP_SEC)
            buf = []
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, device=idx, blocksize=1024, dtype='int16') as stream:
                while not self._stop.is_set():
                    data, _ = stream.read(1024)
                    buf.extend(data[:, 0].tolist())
                    if len(buf) >= chunk_frames:
                        self._dispatch(buf[:chunk_frames], is_int16=True)
                        buf = buf[chunk_frames - overlap_frames:]
        except Exception as e:
            self._run_mic_fallback()
        try:
            return
        except Exception as e:
            self._run_mic_fallback()

    def _run_mic_fallback(self):
        '''Fallback: capture from the default microphone using SpeechRecognition.'''

        if not SR_AVAILABLE:
            self.event_q.put(('error', 'SpeechRecognition not installed: pip install SpeechRecognition'))
            return
        self.event_q.put(('status', 'Using default microphone'))
        try:
            r = self.recognizer
            mic = sr.Microphone()
            with mic as source:
                r.adjust_for_ambient_noise(source, duration=0.5)
            while not self._stop.is_set():
                pass
            try:
                with mic as source:
                    audio = r.listen(source, timeout=5, phrase_time_limit=15)
                with self._order_lock:
                    seq = self._seq_counter
                    self._seq_counter += 1
                self._pool.submit(self._recognize_sr_audio, seq, audio)
            except sr.WaitTimeoutError:
                pass
            except Exception as e:
                self.event_q.put(('status', f'Mic error: {e}'))
            else:
                if not self._stop.is_set():
                    pass
        except Exception as e:
            self.event_q.put(('error', f'Microphone fallback failed: {e}'))

    def _recognize_sr_audio(self, seq: int, audio):
        '''Recognize a SpeechRecognition Audio object (for mic fallback).'''

        try:
            text = self.recognizer.recognize_google(audio, language='en-US')
        except sr.UnknownValueError:
            text = None
        except sr.RequestError as e:
            self.event_q.put(('status', f'Google API error: {e}'))
            text = None
        except Exception as e:
            self.event_q.put(('status', f'Recognition error: {e}'))
            text = None
        while True:
            with self._order_lock:
                self._result_buf[seq] = text
                while self._emit_next in self._result_buf:
                    t = self._result_buf.pop(self._emit_next)
                    self._emit_next += 1
                    if t:
                        with self._lock:
                            self._last_speech_time = time.time()
                            self._has_pending = True
                            self._silence_fired = False
                        self.event_q.put(('word', t))
            return

    def _dispatch(self, samples, is_int16=True):
        with self._order_lock:
            seq = self._seq_counter
            self._seq_counter += 1
        self._pool.submit(self._recognize_and_store, seq, samples, is_int16)

    def _recognize_and_store(self, seq: int, samples, is_int16: bool):
        result = self._recognize(samples, is_int16)
        with self._order_lock:
            self._result_buf[seq] = result
            while self._emit_next in self._result_buf:
                text = self._result_buf.pop(self._emit_next)
                self._emit_next += 1
                if text:
                    with self._lock:
                        self._last_speech_time = time.time()
                        self._has_pending = True
                        self._silence_fired = False
                    self.event_q.put(('word', text))

    def _recognize(self, samples, is_int16=True):
        arr = np.asarray(samples, dtype=np.int16)
        if len(arr) == 0 or np.abs(arr).max() < 80:
            return
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp = f.name
            with wave.open(tmp, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(arr.tobytes())
            with sr.AudioFile(tmp) as src:
                audio = self.recognizer.record(src)
            text = self.recognizer.recognize_google(audio, language='en-US')
        except sr.UnknownValueError:
            if tmp:
                try:
                    os.unlink(tmp)
                    return
                    return
                except Exception:
                    return
            return
        except sr.RequestError as e:
            self.event_q.put(('status', f'Google API: {e}'))
        except Exception as e:
            self.event_q.put(('status', f'{e}'))
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                return
            return text


def build_screen_analysis_prompt(user_instruction: str='', screen_context: str='') -> str:
    instruction = (user_instruction or '').strip()
    extra = ''
    if instruction:
        extra = f' The user instruction is: {instruction}'
    return "You are analyzing a screenshot of the user's screen. If the screen contains a question, solve it clearly and directly. If it is a coding question, provide the answer with clean code and a short explanation. If the screenshot contains no clear question, summarize the visible content briefly." + extra + ' Keep the answer concise, helpful, and aligned with the requested language or tone.' + (' ' + screen_context if screen_context else '')

def _pill_btn(parent, text, bg, bg_hover, command=None, state='normal', fg=None, font_size=10, **kw):
    T = Theme
    bg = kw.pop('bg', bg)
    fg = kw.pop('fg', fg or T.TEXT_PRI)
    font_size = kw.pop('font_size', font_size)
    btn = tk.Label(parent, text=text, bg=bg, fg=fg, font=(T.FONT_UI, font_size, 'bold'), padx=12, pady=6, cursor='hand2', relief='flat', **kw)
    def _enter(_):
        if btn._state == 'normal':
            btn.config(bg=bg_hover)

    def _leave(_):
        if btn._state == 'normal':
            btn.config(bg=bg)
            return
        btn.config(bg=T.BG_CARD)

    btn._state = state
    btn._bg = bg
    btn._bg_h = bg_hover
    btn._fg = fg
    def _click(_):
        if btn._state == 'normal':
            if command:
                command()

    btn.bind('<Enter>', _enter)
    btn.bind('<Leave>', _leave)
    btn.bind('<Button-1>', _click)
    def set_state(s):
        btn._state = s
        if s == 'disabled':
            btn.config(bg=T.BG_CARD, fg=T.TEXT_MUTED, cursor='arrow')
            return
        btn.config(bg=btn._bg, fg=btn._fg, cursor='hand2')

    btn.set_state = set_state
    set_state(state)
    return btn

def _icon_btn(parent, text, command=None, **kw):
    T = Theme
    bg = kw.pop('bg', T.BG_CARD)
    fg = kw.pop('fg', T.TEXT_SEC)
    btn = tk.Label(parent, text=text, bg=bg, fg=fg, font=(T.FONT_UI, 9), padx=12, pady=6, cursor='hand2', relief='flat', **kw)
    btn.bind('<Enter>', (lambda _: btn.config(bg=T.BORDER_STR, fg=T.TEXT_PRI)))
    btn.bind('<Leave>', (lambda _: btn.config(bg=T.BG_CARD, fg=T.TEXT_SEC)))
    btn.bind('<Button-1>', (lambda _: None))
    return btn

def _section_label(parent, text):
    T = Theme
    f = tk.Frame(parent, bg=T.BG_SURFACE)
    tk.Label(f, text=text, bg=T.BG_SURFACE, fg=T.TEXT_GOLD, font=(T.FONT_UI, 8, 'bold'), padx=0, pady=0).pack(side='left')
    tk.Frame(f, bg=T.BORDER, height=1).pack(side='left', fill='x', expand=True, padx=(8, 0))
    return f

def _scrolled_text(parent, bg, fg, height=8, font_family=None, font_size=11):
    T = Theme
    box = scrolledtext.ScrolledText(parent, wrap=tk.WORD, font=(font_family or T.FONT_MONO, font_size), bg=bg, fg=fg, insertbackground=fg, relief='flat', bd=0, padx=12, pady=10, state='disabled', height=height, selectbackground=T.PURPLE_DIM, selectforeground=T.TEXT_PRI)
    box.vbar.config(bg=T.BG_CARD, troughcolor=T.BG_SURFACE, activebackground=T.BORDER_STR, width=6, relief='flat', bd=0)
    return box

import json as _json
_PROFILE_PATH = os.path.join(os.path.expanduser('~'), '.interviewhelper_profile.json')

def _load_profile() -> dict:
    try:
        with open(_PROFILE_PATH, 'r', encoding='utf-8') as f:
            return _json.load(f)
    except Exception:
        return {}

def _save_profile(data: dict):
    try:
        with open(_PROFILE_PATH, 'w', encoding='utf-8') as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f'[Profile] Could not save: {exc}')

class LoginScreen(tk.Tk):
    '''Full-screen Google sign-in window shown on first launch or after logout.'''

    T = Theme
    def __init__(self, kicked_message: str='', force_account_select: bool=False):
        super().__init__()
        T = self.T
        set_window_icon(self)
        self.title('Aftaro — Sign In')
        self.configure(bg=T.BG_BASE)
        self.resizable(True, True)
        if sys.platform == 'win32' or os.name == 'nt':
            try:
                self.state('zoomed')
            except Exception:
                sw, sh = self.winfo_screenwidth(), self.winfo_screenheight() - 60
                self.geometry(f'{sw}x{sh}+0+0')
        else:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f'{sw}x{sh}+0+0')
        self.deiconify()
        self.lift()
        self.focus_force()
        self._status_var = tk.StringVar(value='')
        self._login_result = None
        self._login_error = None
        self._logging_in = False
        self._dot_frame = 0
        self._kicked_msg = kicked_message
        self._force_account_select = force_account_select
        self.after(400, self._apply_capture_protection)
        self._build_ui()
        self._animate_bg()
        self._poll_result()

    def _apply_capture_protection(self):
        if os.name == 'nt':
            hwnd = get_window_handle(self)
            if hwnd:
                protect_window_from_capture(hwnd)

    def _animate_bg(self):
        '''Animate the pulsing dot in the header.'''

        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        T = self.T
        colors = [T.TEAL, T.TEAL_H, T.GOLD, T.GOLD_H, T.PURPLE, T.PURPLE_H]
        self._dot_frame = (self._dot_frame + 1) % len(colors)
        if hasattr(self, '_dot_lbl'):
            try:
                if self._dot_lbl.winfo_exists():
                    self._dot_lbl.config(fg=colors[self._dot_frame])
            except Exception:
                return
        self._anim_job = self.after(650, self._animate_bg)

    def _build_ui(self):
        T = self.T
        bg_frame = tk.Frame(self, bg=T.BG_BASE)
        bg_frame.place(relwidth=1, relheight=1)
        hbar = tk.Frame(self, bg=T.BG_SURFACE)
        hbar.pack(fill='x', side='top')
        hi = tk.Frame(hbar, bg=T.BG_SURFACE)
        hi.pack(fill='x', padx=30, pady=14)
        self._dot_lbl = tk.Label(hi, text='●', bg=T.BG_SURFACE, fg=T.TEAL, font=(T.FONT_UI, 12))
        self._dot_lbl.pack(side='left')
        tk.Label(hi, text='  🎯  Aftaro', bg=T.BG_SURFACE, fg=T.TEXT_PRI, font=(T.FONT_UI, 14, 'bold')).pack(side='left')
        tk.Label(hi, text='AI-powered live interview assistant', bg=T.BG_SURFACE, fg=T.TEXT_MUTED, font=(T.FONT_UI, 9)).pack(side='right')
        tk.Frame(self, bg=T.GOLD_DIM, height=2).pack(fill='x', side='top')
        center = tk.Frame(self, bg=T.BG_BASE)
        center.place(relx=0.5, rely=0.5, anchor='center')
        logo_path = get_resource_path('aftaroLogo.png')
        if os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                logo_img = Image.open(logo_path).resize((72, 72), Image.Resampling.LANCZOS)
                self._logo_photo = ImageTk.PhotoImage(logo_img)
                tk.Label(center, image=self._logo_photo, bg=T.BG_BASE).pack(pady=(0, 8))
            except Exception:
                tk.Label(center, text='🎯', font=(T.FONT_UI, 56), bg=T.BG_BASE, fg=T.GOLD).pack(pady=(0, 8))
        else:
            tk.Label(center, text='🎯', font=(T.FONT_UI, 56), bg=T.BG_BASE, fg=T.GOLD).pack(pady=(0, 8))
        tk.Label(center, text='Aftaro', font=(T.FONT_UI, 30, 'bold'), bg=T.BG_BASE, fg=T.TEXT_PRI).pack()
        tk.Label(center, text='Your AI-powered interview assistant', font=(T.FONT_UI, 12), bg=T.BG_BASE, fg=T.TEXT_MUTED).pack(pady=(4, 28))
        card = tk.Frame(center, bg=T.BG_SURFACE, padx=52, pady=40, highlightthickness=1, highlightbackground=T.BORDER_STR)
        card.pack()
        tk.Label(card, text='Sign in to get started', font=(T.FONT_UI, 16, 'bold'), bg=T.BG_SURFACE, fg=T.TEXT_PRI).pack(pady=(0, 6))
        tk.Label(card, text='Your profile and interview history are saved\nsecurely to your Google account.', font=(T.FONT_UI, 10), bg=T.BG_SURFACE, fg=T.TEXT_MUTED, justify='center').pack(pady=(0, 24))
        self._gbtn_frame = tk.Frame(card, bg='white', cursor='hand2', highlightthickness=1, highlightbackground='#dadce0')
        self._gbtn_frame.pack(fill='x', ipady=2, pady=(0, 18))
        gbtn_inner = tk.Frame(self._gbtn_frame, bg='white')
        gbtn_inner.pack(padx=16, pady=12)
        g_canvas = tk.Canvas(gbtn_inner, width=22, height=22, bg='white', highlightthickness=0, bd=0)
        g_canvas.pack(side='left', padx=(0, 12))
        g_canvas.create_text(11, 11, text='G', font=('Helvetica', 15, 'bold'), fill='#4285F4')
        self._gbtn_lbl = tk.Label(gbtn_inner, text='Sign in with Google', font=('Helvetica', 13), bg='white', fg='#3c4043')
        self._gbtn_lbl.pack(side='left')
        all_widgets = [self._gbtn_frame, gbtn_inner, g_canvas, self._gbtn_lbl]
        for w in all_widgets:
            w.bind('<Enter>', self._btn_enter)
            w.bind('<Leave>', self._btn_leave)
            w.bind('<Button-1>', self._btn_click)
        self._status_lbl = tk.Label(card, textvariable=self._status_var, font=(T.FONT_UI, 9), bg=T.BG_SURFACE, fg=T.TEXT_MUTED)
        self._status_lbl.pack()
        self._err_lbl = tk.Label(card, text='', font=(T.FONT_UI, 9), bg=T.BG_SURFACE, fg=T.RED_H, wraplength=320, justify='center')
        self._err_lbl.pack(pady=(4, 0))
        tk.Label(center, text='🔒  Secure login via Google OAuth2 · Data stored in your account only', font=(T.FONT_UI, 8), bg=T.BG_BASE, fg=T.TEXT_MUTED).pack(pady=(20, 0))
        if self._kicked_msg:
            tk.Label(center, text=self._kicked_msg, font=(T.FONT_UI, 9, 'bold'), bg=T.BG_BASE, fg=T.AMBER, wraplength=420, justify='center').pack(pady=(12, 0))

    def _btn_enter(self, e):
        if not self._logging_in:
            for w in self._gbtn_frame.winfo_children() + [self._gbtn_frame]:
                try:
                    w.config(bg='#f1f3f4')
                except:
                    continue

    def _btn_leave(self, e):
        for w in self._gbtn_frame.winfo_children() + [self._gbtn_frame]:
            try:
                w.config(bg='white')
            except:
                continue

    def _btn_click(self, e):
        if self._logging_in:
            return
        self._logging_in = True
        self._err_lbl.config(text='')
        self._gbtn_lbl.config(text='Opening browser…')
        self._status_var.set('')
        self._start_login()

    def _start_login(self):
        if not AUTH_AVAILABLE or _auth is None:
            self._login_error = 'Auth module not available.\nRun: pip install pymongo requests'
            self._logging_in = False
            return
        _auth.do_google_login(on_success=(lambda user: setattr(self, '_login_result', user)), on_error=(lambda err: setattr(self, '_login_error', err)), on_status=(lambda msg: self.after(0, (lambda m=msg: self._status_var.set(m)))), force_account_select=self._force_account_select)

    def _poll_result(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._login_result is not None:
            user = self._login_result
            self._login_result = None
            self._launch(user)
            return
        if self._login_error is not None:
            err = self._login_error
            self._login_error = None
            self._logging_in = False
            self._status_var.set('')
            try:
                self._gbtn_lbl.config(text='Sign in with Google')
                for w in self._gbtn_frame.winfo_children() + [self._gbtn_frame]:
                    w.config(bg='white')
            except Exception:
                pass
            self._err_lbl.config(text=f'❌  {err}')
        self._poll_job = self.after(200, self._poll_result)

    def destroy(self):
        try:
            if hasattr(self, '_anim_job') and self._anim_job:
                self.after_cancel(self._anim_job)
                self._anim_job = None
            if hasattr(self, '_poll_job') and self._poll_job:
                self.after_cancel(self._poll_job)
                self._poll_job = None
        except Exception:
            pass
        super().destroy()

    def _launch(self, google_user: dict):
        self.destroy()
        SetupScreen(google_user=google_user).mainloop()


class SetupScreen(tk.Tk):
    '''Onboarding window: collects candidate context, persists it between runs,
    then launches App(user_context=…).
    '''

    T = Theme
    _MAX_W = 820
    def __init__(self, google_user: dict):
        super().__init__()
        T = self.T
        set_window_icon(self)
        self.title('Aftaro — Setup')
        self.configure(bg=T.BG_BASE)
        self.resizable(True, True)
        self._anim_frame = 0
        self._google_user = google_user or {}
        self._profile = self._google_user.get('profile') or _load_profile()
        self._credit_seconds = 0
        if self._google_user:
            if AUTH_AVAILABLE and _auth is not None:
                gid = self._google_user.get('google_id', '')
                if gid:
                    _auth.ensure_credit_field(gid)
                    self._credit_seconds = _auth.get_credit_seconds(gid)
            if 'credit_seconds' in self._google_user and not self._credit_seconds:
                self._credit_seconds = int(self._google_user.get('credit_seconds', 0))
        if sys.platform == 'win32' or os.name == 'nt':
            try:
                self.state('zoomed')
            except Exception:
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight() - 70
                self.geometry(f'{sw}x{sh}+0+0')
        else:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.geometry(f'{sw}x{sh}+0+0')
        self.deiconify()
        self.lift()
        self.focus_force()
        self.after(400, self._apply_capture_protection)
        self.after(200, (lambda: hide_from_taskbar(self)))
        self._build_ui()
        self._animate_dot()
        self._refresh_credit_ui()

    def _apply_capture_protection(self):
        if os.name == 'nt':
            hwnd = get_window_handle(self)
            if hwnd:
                protect_window_from_capture(hwnd)

    def _build_ui(self):
        T = self.T
        hbar = tk.Frame(self, bg=T.BG_SURFACE)
        hbar.pack(fill='x', side='top')
        hi = tk.Frame(hbar, bg=T.BG_SURFACE)
        hi.pack(fill='x', padx=30, pady=14)
        self._dot = tk.Label(hi, text='●', bg=T.BG_SURFACE, fg=T.TEAL, font=(T.FONT_UI, 12))
        self._dot.pack(side='left')
        tk.Label(hi, text='  🎯  Aftaro', bg=T.BG_SURFACE, fg=T.TEXT_PRI, font=(T.FONT_UI, 14, 'bold')).pack(side='left')
        right_bar = tk.Frame(hi, bg=T.BG_SURFACE)
        right_bar.pack(side='right')
        uname = self._google_user.get('name', '') or self._google_user.get('email', '')
        initials = ''.join((p[0].upper() for p in uname.split()[:2])) if uname else '?'
        pill = tk.Frame(right_bar, bg=T.BG_CARD, highlightthickness=1, highlightbackground=T.BORDER, padx=10, pady=4)
        pill.pack(side='right', padx=(8, 0))
        tk.Label(pill, text=initials, bg=T.PURPLE, fg=T.TEXT_PRI, font=(T.FONT_UI, 9, 'bold'), width=2, anchor='center', padx=4, pady=3).pack(side='left', padx=(0, 6))
        tk.Label(pill, text=uname, bg=T.BG_CARD, fg=T.TEXT_PRI, font=(T.FONT_UI, 9)).pack(side='left', padx=(0, 10))
        logout_btn = tk.Label(pill, text='Sign out', bg=T.BG_CARD, fg=T.TEXT_MUTED, font=(T.FONT_UI, 8), cursor='hand2', padx=4)
        logout_btn.pack(side='left')
        logout_btn.bind('<Enter>', (lambda e: logout_btn.config(fg=T.RED_H)))
        logout_btn.bind('<Leave>', (lambda e: logout_btn.config(fg=T.TEXT_MUTED)))
        logout_btn.bind('<Button-1>', (lambda e: self._logout()))
        credit_fg = T.RED_H if self._credit_seconds < 300 else T.TEAL
        self._credit_lbl = tk.Label(right_bar, text=f'⏱  {self._fmt_credit(self._credit_seconds)} remaining', bg=T.BG_SURFACE, fg=credit_fg, font=(T.FONT_UI, 10, 'bold'), padx=14)
        self._credit_lbl.pack(side='right', padx=(0, 4))
        buy_btn = tk.Label(right_bar, text='💳  Buy Credits', bg=T.GOLD_DIM, fg=T.GOLD_H, font=(T.FONT_UI, 9, 'bold'), padx=12, pady=5, cursor='hand2', relief='flat')
        buy_btn.pack(side='right', padx=(0, 10))
        buy_btn.bind('<Enter>', (lambda e: buy_btn.config(bg=T.GOLD, fg=T.BG_BASE)))
        buy_btn.bind('<Leave>', (lambda e: buy_btn.config(bg=T.GOLD_DIM, fg=T.GOLD_H)))
        buy_btn.bind('<Button-1>', (lambda e: self._open_buy_page()))
        tk.Frame(self, bg=T.GOLD_DIM, height=2).pack(fill='x', side='top')
        tk.Frame(self, bg=T.GOLD_DIM, height=1).pack(fill='x', side='bottom')
        footer = tk.Frame(self, bg=T.BG_SURFACE, padx=30, pady=12)
        footer.pack(fill='x', side='bottom')
        self._val = tk.Label(footer, text='', bg=T.BG_SURFACE, fg=T.RED_H, font=(T.FONT_UI, 9))
        self._val.pack(pady=(0, 4))
        self._start = tk.Label(footer, text='🚀   Start Interview', bg=T.GOLD, fg=T.BG_BASE, font=(T.FONT_UI, 13, 'bold'), pady=12, cursor='hand2', relief='flat')
        self._start.pack(fill='x')
        self._start.bind('<Enter>', (lambda e: self._start.config(bg=T.GOLD_H)))
        self._start.bind('<Leave>', (lambda e: self._start.config(bg=T.GOLD)))
        self._start.bind('<Button-1>', (lambda e: self._submit()))
        outer = tk.Frame(self, bg=T.BG_BASE)
        outer.pack(fill='both', expand=True, side='top')
        vsb = tk.Scrollbar(outer, orient='vertical', width=6, bg=T.BG_CARD, troughcolor=T.BG_SURFACE, activebackground=T.BORDER_STR, bd=0, relief='flat')
        vsb.pack(side='right', fill='y')
        self._canvas = tk.Canvas(outer, bg=T.BG_BASE, highlightthickness=0, bd=0, yscrollcommand=vsb.set)
        self._canvas.pack(side='left', fill='both', expand=True)
        vsb.config(command=self._canvas.yview)
        self._body = tk.Frame(self._canvas, bg=T.BG_BASE)
        self._bwin = self._canvas.create_window((0, 0), window=self._body, anchor='nw')
        self._body.bind('<Configure>', self._on_body_resize)
        self._canvas.bind('<Configure>', self._on_canvas_resize)
        def _scroll(e):
            delta = e.delta
            if delta == 0:
                return
            if abs(delta) >= 120:
                units = int(-delta / 120)
            else:
                units = -1 if delta > 0 else 1
            self._canvas.yview_scroll(units, 'units')

        self._canvas.bind('<MouseWheel>', _scroll)
        self._body.bind('<MouseWheel>', _scroll)
        self._canvas.bind('<Button-4>', (lambda e: self._canvas.yview_scroll(-1, 'units')))
        self._canvas.bind('<Button-5>', (lambda e: self._canvas.yview_scroll(1, 'units')))
        self._build_form()

    def _on_body_resize(self, _e):
        self._canvas.configure(scrollregion=self._canvas.bbox('all'))

    def _on_canvas_resize(self, e):
        w = min(e.width, self._MAX_W)
        x = max(0, (e.width - w) // 2)
        self._canvas.itemconfig(self._bwin, width=w)
        self._canvas.coords(self._bwin, x, 0)

    def _section(self, icon: str, label: str) -> tk.Frame:
        T = self.T
        outer = tk.Frame(self._body, bg=T.BG_BASE)
        outer.pack(fill='x', padx=0, pady=(0, 18))
        row = tk.Frame(outer, bg=T.BG_BASE)
        row.pack(fill='x', pady=(0, 6))
        tk.Label(row, text=f'{icon}  {label}', bg=T.BG_BASE, fg=T.TEXT_GOLD, font=(T.FONT_UI, 8, 'bold')).pack(side='left')
        tk.Frame(row, bg=T.GOLD_DIM, height=1).pack(side='left', fill='x', expand=True, padx=(10, 0), pady=4)
        card = tk.Frame(outer, bg=T.BG_CARD, padx=18, pady=14, highlightthickness=1, highlightbackground=T.BORDER)
        card.pack(fill='x')
        return card

    def _field(self, parent, placeholder: str, height: int=1, label: str='', saved_key: str='') -> tk.Widget:
        '''Placeholder-aware field; auto-fills from saved profile.'''

        T = self.T
        saved_val = self._profile.get(saved_key, '') if saved_key else ''
        if label:
            tk.Label(parent, text=label, bg=T.BG_CARD, fg=T.TEXT_SEC, font=(T.FONT_UI, 8)).pack(anchor='w', pady=(6, 2))
        wrap = tk.Frame(parent, bg=T.BG_INPUT, highlightthickness=1, highlightbackground=T.BORDER)
        wrap.pack(fill='x', pady=(0, 4))
        if height == 1:
            w = tk.Entry(wrap, font=(T.FONT_UI, 10), bg=T.BG_INPUT, fg=T.TEXT_MUTED if not saved_val else T.TEXT_PRI, insertbackground=T.GOLD, relief='flat', bd=0, highlightthickness=0)
            w.pack(fill='x', ipady=8, padx=10)
            w._ph = placeholder
            if saved_val:
                w.insert(0, saved_val)
            else:
                w.insert(0, placeholder)
            def fi(e, w=w, ph=placeholder):
                if w.get() == ph:
                    w.delete(0, tk.END)
                    w.config(fg=T.TEXT_PRI)

            def fo(e, w=w, ph=placeholder):
                if not w.get():
                    w.insert(0, ph)
                    w.config(fg=T.TEXT_MUTED)

            w.bind('<FocusIn>', fi)
            w.bind('<FocusOut>', fo)
            w.bind('<MouseWheel>', (lambda e: self._canvas.event_generate('<MouseWheel>', delta=e.delta)))
            return w
        w = tk.Text(wrap, font=(T.FONT_UI, 10), bg=T.BG_INPUT, fg=T.TEXT_MUTED if not saved_val else T.TEXT_PRI, insertbackground=T.GOLD, relief='flat', bd=0, highlightthickness=0, height=height, wrap=tk.WORD, padx=10, pady=8)
        w.pack(fill='x')
        w._ph = placeholder
        if saved_val:
            w.insert('1.0', saved_val)
        else:
            w.insert('1.0', placeholder)
        def fi(e, w=w, ph=placeholder):
            if w.get('1.0', tk.END).strip() == ph:
                w.delete('1.0', tk.END)
                w.config(fg=T.TEXT_PRI)

        def fo(e, w=w, ph=placeholder):
            if not w.get('1.0', tk.END).strip():
                w.insert('1.0', ph)
                w.config(fg=T.TEXT_MUTED)

        w.bind('<FocusIn>', fi)
        w.bind('<FocusOut>', fo)
        w.bind('<MouseWheel>', (lambda e: self._canvas.event_generate('<MouseWheel>', delta=e.delta)))
        return w

    def _get(self, w) -> str:
        if isinstance(w, tk.Entry):
            v = w.get().strip()
        else:
            v = w.get('1.0', tk.END).strip()
        if v == getattr(w, '_ph', None):
            return ''
        return v

    def _build_form(self):
        T = self.T
        tk.Frame(self._body, bg=T.BG_BASE, height=24).pack(fill='x')
        sub = tk.Frame(self._body, bg=T.BG_BASE)
        sub.pack(fill='x', pady=(0, 20))
        tk.Label(sub, text='✨  Personalize your AI interview coach', bg=T.BG_BASE, fg=T.TEXT_SEC, font=(T.FONT_UI, 12, 'bold')).pack(anchor='w')
        hint_text = '✅  Previous data loaded — just click Start Interview!' if self._profile else 'Fill in your details — the AI will answer every question as you, using your background and resume. Your data is saved locally.'
        hint_color = T.TEAL if self._profile else T.TEXT_MUTED
        tk.Label(sub, text=hint_text, bg=T.BG_BASE, fg=hint_color, font=(T.FONT_UI, 9), wraplength=780, justify='left').pack(anchor='w', pady=(4, 0))
        c1 = self._section('👤', 'YOUR DETAILS')
        row = tk.Frame(c1, bg=T.BG_CARD)
        row.pack(fill='x', pady=(0, 4))
        left = tk.Frame(row, bg=T.BG_CARD)
        left.pack(side='left', fill='both', expand=True, padx=(0, 8))
        right = tk.Frame(row, bg=T.BG_CARD)
        right.pack(side='left', fill='both', expand=True)
        self._name_w = self._field(left, 'e.g. Arif Ansari', label='Your Name *', saved_key='name')
        self._bg_w = self._field(right, '5 yrs Python dev, built APIs, ML models…', height=3, label='Background / Experience', saved_key='background')
        c2 = self._section('🏢', 'INTERVIEW DETAILS')
        row2 = tk.Frame(c2, bg=T.BG_CARD)
        row2.pack(fill='x', pady=(0, 4))
        l2 = tk.Frame(row2, bg=T.BG_CARD)
        l2.pack(side='left', fill='both', expand=True, padx=(0, 8))
        r2 = tk.Frame(row2, bg=T.BG_CARD)
        r2.pack(side='left', fill='both', expand=True)
        self._company_w = self._field(l2, 'e.g. Google, OpenAI…', label='Company Name *', saved_key='company')
        self._role_w = self._field(r2, 'e.g. Senior Backend Engineer…', label='Role / Position *', saved_key='role')
        self._jd_w = self._field(c2, 'Paste key requirements from the job description…', height=4, label='Job Description / Company Details', saved_key='jd')
        c3 = self._section('📄', 'RESUME')
        self._resume_w = self._field(c3, 'Paste your resume or key highlights here…', height=8, label='Resume Text', saved_key='resume')
        up_row = tk.Frame(c3, bg=T.BG_CARD)
        up_row.pack(fill='x', pady=(6, 0))
        self._file_lbl = tk.Label(up_row, text='', bg=T.BG_CARD, fg=T.TEXT_MUTED, font=(T.FONT_UI, 8))
        self._file_lbl.pack(side='right', padx=6)
        up = tk.Label(up_row, text='📂  Upload .txt or .pdf', bg=T.BG_INPUT, fg=T.TEXT_SEC, font=(T.FONT_UI, 9), padx=12, pady=6, cursor='hand2', relief='flat')
        up.pack(side='left')
        up.bind('<Enter>', (lambda e: up.config(bg=T.BORDER_STR, fg=T.TEXT_PRI)))
        up.bind('<Leave>', (lambda e: up.config(bg=T.BG_INPUT, fg=T.TEXT_SEC)))
        up.bind('<Button-1>', (lambda e: self._upload()))
        tk.Frame(self._body, bg=T.BG_BASE, height=30).pack(fill='x')

    def _animate_dot(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        T = self.T
        palette = [T.TEAL, T.TEAL_H, T.GOLD, T.GOLD_H, T.PURPLE, T.PURPLE_H]
        self._anim_frame = (self._anim_frame + 1) % len(palette)
        if hasattr(self, '_dot'):
            try:
                if self._dot.winfo_exists():
                    self._dot.config(fg=palette[self._anim_frame])
            except Exception:
                return
        self._anim_job = self.after(650, self._animate_dot)

    def _upload(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(title='Select Resume', filetypes=[('Text', '*.txt'), ('PDF', '*.pdf'), ('All', '*.*')])
        if not path:
            return
        text = ''
        try:
            if path.lower().endswith('.pdf'):
                extracted = False
                for lib in ('PyPDF2', 'pdfplumber'):
                    try:
                        if lib == 'PyPDF2':
                            import PyPDF2
                            with open(path, 'rb') as f:
                                r = PyPDF2.PdfReader(f)
                                text = '\n'.join((p.extract_text() or '' for p in r.pages))
                        else:
                            import pdfplumber
                            with pdfplumber.open(path) as pdf:
                                text = '\n'.join((p.extract_text() or '' for p in pdf.pages))
                        extracted = True
                    except ImportError:
                        continue
                    break
                if not extracted:
                    self._val.config(text='PDF needs PyPDF2: pip install PyPDF2', fg=self.T.AMBER)
                    return
            else:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
        except Exception as exc:
            self._val.config(text=f'Cannot read file: {exc}', fg=self.T.RED_H)
        w = self._resume_w
        if isinstance(w, tk.Text):
            w.delete('1.0', tk.END)
            w.insert('1.0', text[:12000])
            w.config(fg=self.T.TEXT_PRI)
        import os as _os
        self._file_lbl.config(text=f'✓ {_os.path.basename(path)}', fg=self.T.TEAL)
        self._val.config(text='')

    @staticmethod
    def _fmt_credit(seconds: int) -> str:
        '''Format seconds as MM:SS.'''

        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f'{m}:{s:02d}'

    def _refresh_credit_ui(self):
        '''Update the time-remaining label and start-button state after a change.'''

        T = self.T
        secs = self._credit_seconds
        if hasattr(self, '_credit_lbl'):
            credit_fg = T.RED_H if secs < 600 else T.TEAL
            self._credit_lbl.config(text=f'⏱  {self._fmt_credit(secs)} remaining', fg=credit_fg)
        if hasattr(self, '_start'):
            self._start.config(bg=T.GOLD, fg=T.BG_BASE, cursor='hand2')
            self._start.bind('<Enter>', (lambda e: self._start.config(bg=T.GOLD_H)))
            self._start.bind('<Leave>', (lambda e: self._start.config(bg=T.GOLD)))
            self._start.bind('<Button-1>', (lambda e: self._submit()))

    def _tick_credit_lbl(self):
        '''Decrement _credit_seconds every second and refresh the header label.'''

        try:
            if not self.winfo_exists():
                return
        except Exception:
            pass
        else:
            return

    def _open_buy_page(self):
        '''Open the browser-based credit purchase page on aftaro.com.'''

        if not self._google_user:
            return
        import webbrowser
        gid = self._google_user.get('google_id', '')
        base = BUY_URL.rstrip('/')
        url = f'{base}/{gid}' if gid else base
        webbrowser.open(url)
        self._start_credit_poll()

    def _start_credit_poll(self):
        '''Poll MongoDB every 30 s to pick up credits purchased in the browser.'''

        try:
            if not self.winfo_exists():
                return
        except Exception:
            pass
        self._poll_credit_from_db()

    def _poll_credit_from_db(self):
        '''Fetch fresh credit from DB in a background thread, then schedule next poll.'''

        try:
            if not self.winfo_exists():
                return
        except Exception:
            pass
        else:
            return

    def _apply_polled_credit(self, new_secs: int):
        """Apply DB-fetched credit only if it's higher than current local value."""

        if new_secs > self._credit_seconds:
            self._credit_seconds = new_secs
            self._refresh_credit_ui()

    def destroy(self):
        try:
            if hasattr(self, '_anim_job') and self._anim_job:
                self.after_cancel(self._anim_job)
                self._anim_job = None
            if hasattr(self, '_tick_job') and self._tick_job:
                self.after_cancel(self._tick_job)
                self._tick_job = None
            if hasattr(self, '_poll_job') and self._poll_job:
                self.after_cancel(self._poll_job)
                self._poll_job = None
        except Exception:
            pass
        super().destroy()

    def _submit(self):
        T = self.T
        name = self._get(self._name_w)
        bg = self._get(self._bg_w)
        company = self._get(self._company_w)
        role = self._get(self._role_w)
        jd = self._get(self._jd_w)
        resume = self._get(self._resume_w)
        if not name:
            self._val.config(text='⚠  Please enter your name.', fg=T.RED_H)
            return
        if not company:
            self._val.config(text='⚠  Please enter the company name.', fg=T.RED_H)
            return
        if not role:
            self._val.config(text='⚠  Please enter the role / position.', fg=T.RED_H)
            return
        if self._credit_seconds <= 0:
            self._val.config(text='⚠  No interview credits remaining. Please buy credits to continue.', fg=T.RED_H)
            return
        ctx = dict(name=name, background=bg, company=company, role=role, jd=jd, resume=resume)
        _save_profile(ctx)
        print(f'[Profile] Saved locally to {_PROFILE_PATH}')
        if self._google_user and AUTH_AVAILABLE and _auth is not None:
            gid = self._google_user.get('google_id', '')
            if gid:
                _auth.save_profile(gid, ctx)
                self._google_user['profile'] = ctx
        self._val.config(text='✓ Saved — launching…', fg=T.TEAL)
        self._start.config(text='⏳  Starting…', bg=T.TEAL, fg=T.BG_BASE)
        self.after(200, (lambda: self._launch(ctx)))

    def _logout(self):
        '''Sign out: clear local session and return to the login screen.'''

        if AUTH_AVAILABLE and _auth is not None:
            _auth.clear_session()
        self.destroy()
        LoginScreen(force_account_select=True).mainloop()

    def _launch(self, ctx):
        self.destroy()
        App(user_context=ctx, credit_seconds=self._credit_seconds, google_user=self._google_user).mainloop()


class App(tk.Tk):
    T = Theme
    def __init__(self, user_context: dict | None=None, credit_seconds: int=0, google_user: dict | None=None):
        super().__init__()
        T = self.T
        set_window_icon(self)
        self.title('Aftaro — AI Assistant')
        self.geometry('1200x700')
        self.minsize(900, 500)
        self.configure(bg=T.BG_SURFACE)
        self.resizable(True, True)
        self.overrideredirect(True)
        self._drag_data = {'x': 0, 'y': 0}
        self._user_context = user_context or {}
        self._context_sent = False
        self._google_user = google_user
        if self._google_user:
            self._credit_seconds = max(0, int(credit_seconds))
        elif AUTH_AVAILABLE and _auth is not None:
            self._credit_seconds = _auth.get_guest_credit_seconds()
        else:
            self._credit_seconds = max(0, int(credit_seconds)) if credit_seconds > 0 else 600
        self._timer_running = False
        self._db_sync_counter = 0
        self.attributes('-topmost', ALWAYS_ON_TOP)
        self.attributes('-alpha', WINDOW_ALPHA)
        self._event_q = queue.Queue()
        self._transcriber = None
        self._listening = False
        self._llm_busy = False
        self._pending_txt = []
        self._line_started = False
        self._auto_mode = True
        self._click_through = False
        self._restore_win = None
        self._build_ui()
        self.bind('<Control-Alt-c>', (lambda e: self._toggle_click_through()))
        self.bind('<Control-Alt-C>', (lambda e: self._toggle_click_through()))
        self.after(300, self._apply_capture_protection)
        self.after(200, (lambda: hide_from_taskbar(self)))
        if SIO_AVAILABLE:
            self._sio_client = SocketIOClient(BACKEND_URL)
        else:
            self._sio_client = None
        if self._credit_seconds > 0:
            self._timer_running = True
            self._db_sync_counter = 0
            self.after(500, self._update_timer_label)
            self.after(1000, self._tick_timer)
        self.after(800, self._start)
        self._poll()

    def _apply_capture_protection(self):
        if os.name == 'nt':
            hwnd = get_window_handle(self)
            if hwnd:
                protect_window_from_capture(hwnd)

    def _build_ui(self):
        T = self.T
        titlebar = tk.Frame(self, bg=T.BG_SURFACE, height=34)
        titlebar.pack(fill='x')
        titlebar.pack_propagate(False)
        titlebar.bind('<ButtonPress-1>', self._start_drag)
        titlebar.bind('<B1-Motion>', self._move_window)
        tk.Label(titlebar, text='  🎧', bg=T.BG_SURFACE, fg=T.GOLD, font=(T.FONT_UI, 12), pady=6).pack(side='left')
        tk.Label(titlebar, text='Aftaro — AI Assistant', bg=T.BG_SURFACE, fg=T.TEXT_PRI, font=(T.FONT_UI, 10, 'bold'), pady=6, padx=4).pack(side='left')
        timer_fg = T.RED_H if self._credit_seconds < 120 else T.TEAL_H if self._google_user else T.AMBER
        label_prefix = '⏱ ' if self._google_user else '⏱ Trial: '
        self._timer_lbl = tk.Label(titlebar, text=f'{label_prefix}{self._fmt_time(self._credit_seconds)}', bg=T.BG_SURFACE, fg=timer_fg, font=(T.FONT_UI, 10, 'bold'), padx=10)
        self._timer_lbl.pack(side='left', padx=(12, 0))
        self._close_btn = _icon_btn(titlebar, text='✕', command=self.destroy, bg=T.BG_SURFACE, fg=T.TEXT_SEC)
        self._close_btn.pack(side='right', padx=(0, 8), pady=4)
        self._ct_btn = _pill_btn(titlebar, text='👻 Click-Through', bg=T.BG_CARD, bg_hover=T.PURPLE_DIM, fg=T.TEXT_SEC, font_size=8, command=self._toggle_click_through)
        self._ct_btn.pack(side='right', padx=(0, 6), pady=4)
        self._status_var = tk.StringVar(value='')
        self._live_dot = tk.Label(self, text='●', bg=T.BG_SURFACE, fg=T.TEXT_MUTED, font=(T.FONT_UI, 10))
        self._live_dot.pack_forget()
        tk.Frame(self, bg=T.BORDER, height=1).pack(fill='x')
        main_panel = tk.Frame(self, bg=T.BG_SURFACE)
        main_panel.pack(fill='both', expand=True)
        left_panel = tk.Frame(main_panel, bg=T.BG_SURFACE, width=260)
        left_panel.pack(side='left', fill='y', expand=False, padx=(8, 4), pady=8)
        left_panel.pack_propagate(False)
        right_panel = tk.Frame(main_panel, bg=T.BG_SURFACE)
        right_panel.pack(side='right', fill='both', expand=True, padx=(4, 8), pady=8)
        _section_label(left_panel, 'ASK A QUESTION').pack(fill='x', pady=(0, 6))
        ask_frame = tk.Frame(left_panel, bg=T.BG_CARD, pady=6, padx=6, highlightthickness=1, highlightbackground=T.BORDER, highlightcolor=T.GOLD)
        ask_frame.pack(fill='x', pady=(0, 10))
        self._question_entry = tk.Entry(ask_frame, font=(T.FONT_UI, 10), bg=T.BG_INPUT, fg=T.TEXT_PRI, insertbackground=T.TEXT_PRI, relief='flat', bd=0, highlightthickness=0)
        self._question_entry.pack(side='left', fill='x', expand=True, padx=(4, 8), ipady=5)
        self._question_entry.bind('<Return>', (lambda e: self._send_manual_question()))
        self._ask_btn = _pill_btn(ask_frame, text='Send', bg=T.PURPLE, bg_hover=T.PURPLE_H, command=self._send_manual_question)
        self._ask_btn.pack(side='right')
        ctrl_frame = tk.Frame(left_panel, bg=T.BG_SURFACE)
        ctrl_frame.pack(fill='x', pady=(0, 8))
        self._screen_btn = _pill_btn(ctrl_frame, text='📸 Analyze Screen', bg=T.TEAL, bg_hover=T.TEAL_H, command=self._analyze_current_screen)
        self._screen_btn.pack(fill='x', pady=(0, 6))
        btn_row = tk.Frame(ctrl_frame, bg=T.BG_SURFACE)
        btn_row.pack(fill='x')
        self._start_btn = _pill_btn(btn_row, text='▶  Start Listening', bg=T.PURPLE, bg_hover=T.PURPLE_H, command=self._start)
        self._start_btn.pack(side='left', padx=(0, 6))
        self._stop_btn = _pill_btn(btn_row, text='■  Stop Listening', bg=T.RED, bg_hover=T.RED_H, command=self._stop, state='disabled')
        self._stop_btn.pack(side='left', padx=(0, 6))
        self._stop_btn.pack_forget()
        _icon_btn(btn_row, 'Clear', command=self._clear).pack(side='left', padx=(4, 0))
        _section_label(left_panel, 'SEND TO AI').pack(fill='x', pady=(0, 6))
        mode_frame = tk.Frame(left_panel, bg=T.BG_CARD, highlightthickness=1, highlightbackground=T.BORDER)
        mode_frame.pack(fill='x', pady=(0, 6))
        self._auto_seg_btn = tk.Label(mode_frame, text='Auto (3s silence)', bg=T.PURPLE, fg=T.TEXT_PRI, font=(T.FONT_UI, 9, 'bold'), padx=8, pady=7, cursor='hand2')
        self._auto_seg_btn.pack(side='left', fill='x', expand=True)
        self._auto_seg_btn.bind('<Button-1>', (lambda e: self._set_auto_mode(True)))
        self._manual_seg_btn = tk.Label(mode_frame, text='Manual', bg=T.BG_CARD, fg=T.TEXT_SEC, font=(T.FONT_UI, 9, 'bold'), padx=8, pady=7, cursor='hand2')
        self._manual_seg_btn.pack(side='left', fill='x', expand=True)
        self._manual_seg_btn.bind('<Button-1>', (lambda e: self._set_auto_mode(False)))
        self._send_ai_btn = _pill_btn(left_panel, text='📤  Send to AI', bg=T.GOLD, bg_hover=T.GOLD_H, fg=T.BG_BASE, command=self._manual_send, state='disabled')
        self._send_ai_btn.pack(fill='x', pady=(0, 10))
        _section_label(left_panel, 'LIVE TRANSCRIPT (monitor)').pack(fill='x', pady=(0, 6))
        self._transcript = _scrolled_text(left_panel, bg=T.BG_INPUT, fg=T.TEXT_PRI, height=6, font_size=9)
        self._transcript.pack(fill='both', expand=True)
        self._transcript.tag_config('ts', foreground=T.TEXT_MUTED)
        self._transcript.tag_config('body', foreground=T.TEXT_PRI, font=(T.FONT_MONO, 9, 'bold'))
        tk.Label(left_panel, text='Opacity:', bg=T.BG_SURFACE, fg=T.TEXT_MUTED, font=(T.FONT_UI, 8)).pack(anchor='w', pady=(8, 2))
        self._ctrl_alpha_var = tk.DoubleVar(value=WINDOW_ALPHA)
        tk.Scale(left_panel, from_=0.3, to=1.0, resolution=0.01, orient='horizontal', variable=self._ctrl_alpha_var, command=(lambda v: self.attributes('-alpha', float(v))), bg=T.BG_SURFACE, fg=T.TEXT_SEC, troughcolor=T.BG_INPUT, activebackground=T.GOLD_H, highlightthickness=0, bd=0, length=200, showvalue=False).pack(fill='x', pady=(0, 8))
        self._left_ct_btn = _pill_btn(left_panel, text='👻 Make Screen Untouchable', bg=T.GOLD_DIM, bg_hover=T.GOLD, fg=T.GOLD_H, command=self._toggle_click_through)
        self._left_ct_btn.pack(fill='x', pady=(4, 8))
        ai_header = tk.Frame(right_panel, bg=T.BG_CARD)
        ai_header.pack(fill='x')
        self._ai_dot = tk.Label(ai_header, text='●', fg=T.TEXT_MUTED, bg=T.BG_CARD, font=(T.FONT_UI, 10))
        self._ai_dot.pack(side='left', padx=(14, 4), pady=8)
        self._ai_label_var = tk.StringVar(value='AI Response')
        tk.Label(ai_header, textvariable=self._ai_label_var, bg=T.BG_CARD, fg=T.TEXT_PRI, font=(T.FONT_UI, 12, 'bold'), pady=8, padx=0).pack(side='left')
        _icon_btn(ai_header, 'Clear AI', command=self._clear_ai).pack(side='right', padx=(0, 6), pady=4)
        _icon_btn(ai_header, 'Copy', command=self._copy_ai).pack(side='right', padx=(0, 6), pady=4)
        tk.Frame(right_panel, bg=T.GOLD_DIM, height=1).pack(fill='x')
        self._ai_box = _scrolled_text(right_panel, bg=T.BG_AI, fg=T.TEXT_AI, font_family=T.FONT_UI, font_size=12, height=15)
        self._ai_box.pack(fill='both', expand=True)
        self._ai_box.tag_config('sep', foreground=T.GOLD_DIM)
        self._ai_box.tag_config('answer', foreground=T.TEXT_AI, font=(T.FONT_UI, 12))
        self._ai_box.tag_config('error', foreground=T.RED_H, font=(T.FONT_UI, 11, 'bold'))
        self._ai_box.tag_config('user_q', foreground=T.TEXT_GOLD, font=(T.FONT_UI, 11, 'bold'))
        self._ai_box.tag_config('label', foreground=T.TEXT_MUTED, font=(T.FONT_UI, 9))
        self._ai_box.tag_config('code', foreground=T.TEXT_BLUE, font=(T.FONT_MONO, 11))
        self._ai_status_var = tk.StringVar(value='Waiting for input…')
        tk.Label(right_panel, textvariable=self._ai_status_var, bg=T.BG_CARD, fg=T.TEXT_MUTED, font=(T.FONT_UI, 8), anchor='w', padx=12, pady=4).pack(fill='x', side='bottom')
        tk.Frame(right_panel, bg=T.BORDER, height=1).pack(fill='x', side='bottom')
        missing = []
        if not SR_AVAILABLE:
            missing.append('SpeechRecognition')
        if not SC_AVAILABLE and not PA_AVAILABLE:
            missing.append('soundcard or pyaudio')
        if missing:
            tk.Label(self, text='  Missing: pip install ' + ' | pip install '.join(missing), bg=T.AMBER, fg=T.BG_BASE, font=(T.FONT_MONO, 9), pady=4, padx=10, anchor='w').pack(side='bottom', fill='x')
        self.after(180, self._focus_question_entry)

    def _set_auto_mode(self, is_auto: bool):
        T = self.T
        self._auto_mode = is_auto
        if is_auto:
            self._auto_seg_btn.config(bg=T.PURPLE, fg=T.TEXT_PRI)
            self._manual_seg_btn.config(bg=T.BG_CARD, fg=T.TEXT_SEC)
        else:
            self._auto_seg_btn.config(bg=T.BG_CARD, fg=T.TEXT_SEC)
            self._manual_seg_btn.config(bg=T.PURPLE, fg=T.TEXT_PRI)
        if self._listening:
            self._status_var.set('Listening (Auto — sends after 3s silence)…' if is_auto else 'Listening (Manual — click Send to AI)…')

    def _focus_question_entry(self):
        try:
            self._question_entry.focus_set()
            self._question_entry.icursor(tk.END)
            return
        except Exception:
            return

    def _update_send_btn(self):
        if self._pending_txt and not self._llm_busy:
            self._send_ai_btn.set_state('normal')
            return
        self._send_ai_btn.set_state('disabled')

    def _manual_send(self):
        if not self._pending_txt or self._llm_busy:
            return
        prompt = ' '.join(self._pending_txt)
        self._pending_txt = []
        self._clear_transcript()
        self._fire_llm(prompt)
        self._update_send_btn()

    def _clear_ai(self):
        self._ai_box.config(state='normal')
        self._ai_box.delete('1.0', tk.END)
        self._ai_box.config(state='disabled')
        self._ai_status_var.set('Cleared')

    def _copy_ai(self):
        self.clipboard_clear()
        self.clipboard_append(self._ai_box.get('1.0', tk.END).strip())
        self._ai_status_var.set('Copied to clipboard')
        self.after(2000, (lambda: self._ai_status_var.set('')))

    def _ai_append(self, text: str, tag: str='answer'):
        self._ai_box.config(state='normal')
        self._ai_box.insert(tk.END, text, tag)
        self._ai_box.see(tk.END)
        self._ai_box.config(state='disabled')

    def _ai_set_status(self, text: str):
        self._ai_status_var.set(text)

    def _ai_set_label(self, text: str):
        self._ai_label_var.set(text)

    def _ai_set_dot(self, color: str):
        self._ai_dot.config(fg=color)

    @staticmethod
    def _fmt_time(seconds: int) -> str:
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f'{m}:{s:02d}'

    def _update_timer_label(self):
        '''Refresh the title-bar timer display.'''

        if self._timer_lbl is None:
            return
        try:
            secs = self._credit_seconds
            timer_fg = self.T.RED_H if secs < 180 else self.T.TEAL_H
            self._timer_lbl.config(text=f'⏱ {self._fmt_time(secs)}', fg=timer_fg)
            return
        except Exception:
            return

    def _tick_timer(self):
        '''
        Decrement credit by 1 second locally.
        Every 15 s, flush current credit to MongoDB in a background thread.
        '''

        try:
            if not self.winfo_exists():
                return
        except Exception:
            pass
        else:
            return

    def _on_credit_expired(self):
        '''Called when user credit reaches 0.'''

        self._timer_running = False
        self._listening = False
        if self._transcriber:
            try:
                self._transcriber.stop()
            except Exception:
                pass
        self._sync_credit_to_storage()
        self._update_timer_label()
        msg = '\n\n⛔ ──────────────────────────────────────────────────\n⏱ INTERVIEW CREDITS EXPIRED.\nPlease top up your credits to continue using Aftaro.\n────────────────────────────────────────────────────\n'
        self._ai_append(msg, 'error')
        self._ai_set_label('Aftaro — Time Expired')
        self._ai_set_dot(self.T.RED_H)
        self._status_var.set('Session expired — Please buy credits')
        self._update_send_btn()

    def _sync_credit_to_storage(self):
        '''Write current credit to MongoDB in a fire-and-forget daemon thread.'''

        secs = self._credit_seconds
        if self._google_user:
            if AUTH_AVAILABLE:
                if _auth is not None:
                    gid = self._google_user.get('google_id', '')
                    if gid:
                        threading.Thread(target=(lambda: _auth.set_credit_seconds(gid, secs)), daemon=True).start()

    def destroy(self):
        '''Override to flush remaining credit and stop transcriber before closing.'''

        self._timer_running = False
        self._listening = False
        if self._transcriber:
            try:
                self._transcriber.stop()
            except Exception:
                pass
        try:
            if hasattr(self, '_tick_job') and self._tick_job:
                self.after_cancel(self._tick_job)
                self._tick_job = None
        except Exception:
            pass
        self._sync_credit_to_storage()
        super().destroy()

    def _start_drag(self, event):
        self._drag_data['x'] = event.x
        self._drag_data['y'] = event.y

    def _move_window(self, event):
        dx = event.x - self._drag_data['x']
        dy = event.y - self._drag_data['y']
        x = self.winfo_x() + dx
        y = self.winfo_y() + dy
        self.geometry(f'+{x}+{y}')

    def _toggle_click_through(self, enable=None):
        if enable is None:
            self._click_through = not self._click_through
        else:
            self._click_through = enable
        hwnd = get_window_handle(self) or self.winfo_id()
        set_click_through(hwnd, self._click_through)
        T = self.T
        if self._click_through:
            self._show_restore_bar()
            self._ct_btn.config(text='🔒 Untouchable Active', fg=T.GOLD)
            if hasattr(self, '_left_ct_btn'):
                self._left_ct_btn.config(text='🔒 Screen Untouchable (Click restore button)', bg=T.GOLD, fg=T.BG_BASE)
                return
            return
        self._hide_restore_bar()
        self._ct_btn.config(text='👻 Click-Through', fg=T.TEXT_SEC)
        if hasattr(self, '_left_ct_btn'):
            self._left_ct_btn.config(text='👻 Make Screen Untouchable', bg=T.GOLD_DIM, fg=T.GOLD_H)

    def _show_restore_bar(self):
        if self._restore_win and self._restore_win.winfo_exists():
            self._restore_win.deiconify()
            self._restore_win.update()
            hwnd = get_window_handle(self._restore_win)
            if hwnd and os.name == 'nt':
                protect_window_from_capture(hwnd)
            return
        T = self.T
        self._restore_win = tk.Toplevel(self)
        self._restore_win.overrideredirect(True)
        self._restore_win.attributes('-topmost', True)
        self._restore_win.attributes('-alpha', 0.95)
        self._restore_win.configure(bg=T.GOLD)
        w, h = 135, 30
        x = self.winfo_x() + self.winfo_width() - w - 15
        y = max(self.winfo_y() + 15, 15)
        self._restore_win.geometry(f'{w}x{h}+{x}+{y}')
        inner = tk.Frame(self._restore_win, bg=T.BG_BASE, padx=1, pady=1)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        lbl = tk.Label(inner, text='⋮⋮ 🔓 Restore', font=(T.FONT_UI, 9, 'bold'), bg=T.BG_SURFACE, fg=T.GOLD, cursor='fleur', relief='flat', bd=0)
        lbl.pack(fill='both', expand=True)
        drag_state = {'press_x': 0, 'press_y': 0, 'win_x': 0, 'win_y': 0, 'dragged': False}
        def on_press(e):
            drag_state['press_x'] = e.x_root
            drag_state['press_y'] = e.y_root
            drag_state['win_x'] = self._restore_win.winfo_x()
            drag_state['win_y'] = self._restore_win.winfo_y()
            drag_state['dragged'] = False
            lbl.config(bg=T.GOLD_DIM)

        def on_motion(e):
            dx = e.x_root - drag_state['press_x']
            dy = e.y_root - drag_state['press_y']
            if abs(dx) > 3 or abs(dy) > 3:
                drag_state['dragged'] = True
                nx = drag_state['win_x'] + dx
                ny = drag_state['win_y'] + dy
                self._restore_win.geometry(f'+{nx}+{ny}')

        def on_release(e):
            lbl.config(bg=T.BG_SURFACE)
            if not drag_state['dragged']:
                self._toggle_click_through(False)

        def on_enter(e):
            if not drag_state.get('dragged', False):
                lbl.config(bg=T.BG_CARD)

        def on_leave(e):
            if not drag_state.get('dragged', False):
                lbl.config(bg=T.BG_SURFACE)

        for widget in self._restore_win, inner, lbl:
            widget.bind('<ButtonPress-1>', on_press)
            widget.bind('<B1-Motion>', on_motion)
            widget.bind('<ButtonRelease-1>', on_release)
        lbl.bind('<Enter>', on_enter)
        lbl.bind('<Leave>', on_leave)
        self._restore_win.update()
        hwnd = get_window_handle(self._restore_win)
        if hwnd:
            if os.name == 'nt':
                protect_window_from_capture(hwnd)

    def _hide_restore_bar(self):
        if self._restore_win:
            if self._restore_win.winfo_exists():
                self._restore_win.withdraw()

    def _start(self):
        if self._listening:
            return
        if self._credit_seconds <= 0:
            self._on_credit_expired()
            return
        self._listening = True
        self._pending_txt = []
        self._line_started = False
        self._start_btn.pack_forget()
        self._stop_btn.pack(side='left', padx=(0, 6))
        self._stop_btn.set_state('normal')
        self._live_dot.config(fg=self.T.RED_H)
        self._status_var.set('Listening (Auto — sends after 3s silence)…' if self._auto_mode else 'Listening (Manual — click Send to AI)…')
        self._transcriber = AudioTranscriber(self._event_q)
        self._transcriber.start()
        self._update_send_btn()
        if self._google_user:
            if not self._timer_running:
                self._timer_running = True
                self._db_sync_counter = 0
                self._update_timer_label()
                self.after(1000, self._tick_timer)

    def _stop(self):
        if not self._listening:
            return
        self._listening = False
        if self._transcriber:
            self._transcriber.stop()
        self._stop_btn.pack_forget()
        self._start_btn.pack(side='left', padx=(0, 6))
        self._start_btn.set_state('normal')
        self._live_dot.config(fg=self.T.TEXT_MUTED)
        self._status_var.set('Stopped — press Start to resume')

    def _clear(self):
        self._transcript.config(state='normal')
        self._transcript.delete('1.0', tk.END)
        self._transcript.config(state='disabled')
        self._clear_ai()
        self._pending_txt = []
        self._line_started = False
        self._update_send_btn()

    def _send_manual_question(self):
        question = self._question_entry.get().strip()
        if not question:
            return
        self._question_entry.delete(0, tk.END)
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._ai_append(f'\n── {ts} ────────────────────────────────\n', 'sep')
        self._ai_append(f'You: {question}\n', 'user_q')
        self._ai_append('AI:  ', 'label')
        self._fire_llm_raw(question)

    def _append_words(self, words: str):
        self._transcript.config(state='normal')
        if not self._line_started:
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            if self._transcript.get('1.0', tk.END).strip():
                self._transcript.insert(tk.END, '\n')
            self._transcript.insert(tk.END, f'[{ts}] ', 'ts')
            self._line_started = True
        self._transcript.insert(tk.END, words + ' ', 'body')
        self._transcript.see(tk.END)
        self._transcript.config(state='disabled')

    def _clear_transcript(self):
        self._transcript.config(state='normal')
        self._transcript.delete('1.0', tk.END)
        self._transcript.config(state='disabled')
        self._line_started = False

    def _analyze_current_screen(self):
        if self._llm_busy:
            return
        if self._credit_seconds <= 0:
            self._on_credit_expired()
            return
        self._llm_busy = True
        self._update_send_btn()
        self._ai_set_label('AI Response  —  analyzing screen…')
        self._ai_set_dot(self.T.AMBER)
        self._ai_set_status('Capturing screen…')
        self._status_var.set('Capturing screen…')
        instruction = self._question_entry.get().strip()
        def _do_capture_and_send():
            try:
                try:
                    screenshot = ImageGrab.grab(all_screens=True)
                except TypeError:
                    screenshot = ImageGrab.grab()
                except Exception as exc:
                    raise RuntimeError(f'Screen capture failed: {exc}') from exc
            except Exception as exc:
                _on_error(str(exc))
            if screenshot is None:
                raise RuntimeError('Screen capture returned nothing.')
            MAX_W = 1280
            if screenshot.width > MAX_W:
                ratio = MAX_W / screenshot.width
                new_size = MAX_W, int(screenshot.height * ratio)
                screenshot = screenshot.resize(new_size)
            if screenshot.mode in ('RGBA', 'P'):
                screenshot = screenshot.convert('RGB')
            buffered = io.BytesIO()
            screenshot.save(buffered, format='JPEG', quality=75, optimize=True)
            encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
            prompt_text = build_screen_analysis_prompt(instruction or '')
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            self.after(0, (lambda: self._ai_append(f'\n── {ts} ────────────────────────────────\n', 'sep')))
            self.after(0, (lambda: self._ai_append('Screen Analysis\n', 'user_q')))
            self.after(0, (lambda: self._ai_append('AI:  ', 'label')))
            if SIO_AVAILABLE and self._sio_client is not None:
                gid = self._google_user.get('google_id', '') if self._google_user else ''
                dtoken = self._google_user.get('current_device_token', '') if self._google_user else ''
                self._sio_client.send_query(prompt_text, image_b64=encoded, google_id=gid, device_token=dtoken, on_chunk=_on_chunk, on_done=_on_done, on_error=_on_error)
                return
            try:
                _on_error('Backend not connected. Start the server and retry.')
            except Exception as exc:
                _on_error(str(exc))

        def _on_chunk(chunk: str):
            self.after(0, (lambda t=chunk: self._ai_append(t, 'answer')))

        def _on_done():
            self.after(0, (lambda: self._ai_append('\n', 'answer')))
            self.after(0, (lambda: self._ai_set_label('AI Response')))
            self.after(0, (lambda: self._ai_set_dot(self.T.TEAL)))
            self.after(0, (lambda: self._ai_set_status('Done')))
            self._llm_busy = False
            self.after(0, self._update_send_btn)

        def _on_error(err: str):
            self.after(0, (lambda: self._ai_append(f'Error: {err}\n', 'error')))
            self.after(0, (lambda: self._ai_set_label('AI Response  —  error')))
            self.after(0, (lambda: self._ai_set_dot(self.T.RED_H)))
            self.after(0, (lambda: self._ai_set_status('Screen capture failed')))
            self.after(0, (lambda: self._status_var.set('Screen capture failed')))
            self._llm_busy = False
            self.after(0, self._update_send_btn)

        threading.Thread(target=_do_capture_and_send, daemon=True).start()

    def _fire_llm(self, prompt: str):
        '''Called by silence trigger (auto mode) or manual Send to AI.'''

        if self._credit_seconds <= 0:
            self._on_credit_expired()
            return
        self._llm_busy = True
        self._update_send_btn()
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._ai_append(f'\n── {ts} ────────────────────────────────\n', 'sep')
        self._ai_append(f'Voice: {prompt[:80]}{'…' if len(prompt) > 80 else ''}\n', 'user_q')
        self._ai_append('AI:  ', 'label')
        self._fire_llm_raw(prompt, from_voice=True)

    def _fire_llm_raw(self, prompt: str, from_voice: bool=False, content=None):
        if self._credit_seconds <= 0:
            self._on_credit_expired()
            return
        T = self.T
        self._llm_busy = True
        self._ai_set_label('AI Response  —  thinking…')
        self._ai_set_dot(T.AMBER)
        self._ai_set_status('Sending to GPT-4o…')
        self._status_var.set('Sending to GPT-4o…')
        self._update_send_btn()
        _buf = ['']
        def _on_chunk(chunk: str):
            _buf[0] += chunk
            if len(_buf[0]) >= 15:
                text, _buf[0] = _buf[0], ''
                self.after(0, (lambda t=text: self._ai_append(t, 'answer')))

        def _on_done():
            if _buf[0]:
                self.after(0, (lambda t=_buf[0]: self._ai_append(t, 'answer')))
            self.after(0, (lambda: self._ai_append('\n', 'answer')))
            self.after(0, (lambda: self._ai_set_label('AI Response')))
            self.after(0, (lambda: self._ai_set_dot(T.TEAL)))
            self.after(0, (lambda: self._ai_set_status('Done')))
            self._llm_busy = False
            if self._transcriber:
                self._transcriber.reset_segment()
            self.after(0, self._update_send_btn)

        def _on_error(err: str):
            self.after(0, (lambda: self._ai_append(f'Error: {err}\n', 'error')))
            self.after(0, (lambda: self._ai_set_label('AI Response  —  error')))
            self.after(0, (lambda: self._ai_set_dot(T.RED_H)))
            self._llm_busy = False
            if self._transcriber:
                self._transcriber.reset_segment()
            self.after(0, self._update_send_btn)

        query_str = prompt
        image_b64 = ''
        if isinstance(content, list):
            for part in content:
                if part.get('type') == 'text':
                    query_str = part['text']
                else:
                    if part.get('type') != 'image_url':
                        continue
                    url = part.get('image_url', {}).get('url', '')
                    if ';base64,' not in url:
                        continue
                    image_b64 = url.split(';base64,', 1)[1]
        elif isinstance(content, str):
            query_str = content
        if not self._context_sent:
            if self._user_context and not image_b64:
                ctx = self._user_context
                lines = ['[SESSION CONTEXT — personalise every answer using this:]']
                if ctx.get('name'):
                    lines.append(f'Candidate name: {ctx['name']}')
                if ctx.get('background'):
                    lines.append(f'Background & experience: {ctx['background']}')
                if ctx.get('company'):
                    lines.append(f'Interviewing at: {ctx['company']}')
                if ctx.get('role'):
                    lines.append(f'Role applied for: {ctx['role']}')
                if ctx.get('jd'):
                    lines.append(f'Job description: {ctx['jd']}')
                if ctx.get('resume'):
                    lines.append(f'Resume:\n{ctx['resume'][:4000]}')
                lines.append('[END CONTEXT]')
                lines.append(f'\nFirst question: {query_str}')
                query_str = '\n'.join(lines)
                self._context_sent = True
        if SIO_AVAILABLE and self._sio_client is not None:
            gid = self._google_user.get('google_id', '') if self._google_user else ''
            dtoken = self._google_user.get('current_device_token', '') if self._google_user else ''
            self._sio_client.send_query(query_str, image_b64=image_b64, google_id=gid, device_token=dtoken, on_chunk=_on_chunk, on_done=_on_done, on_error=_on_error)
            return
        self.after(0, (lambda: _on_error("python-socketio not installed or backend not configured.\nRun: pip install 'python-socketio[client]'  and set INTERVIEW_BACKEND env var.")))

    def _poll(self):
        while not self._event_q.empty():
            ev = self._event_q.get()
            if ev[0] == 'word':
                self._pending_txt.append(ev[1])
                self._append_words(ev[1])
                self._update_send_btn()
            elif ev[0] == 'silence':
                if self._auto_mode and self._pending_txt and not self._llm_busy and self._listening:
                    prompt = ' '.join(self._pending_txt)
                    self._pending_txt = []
                    self._clear_transcript()
                    self._fire_llm(prompt)
                    self._update_send_btn()
            elif ev[0] == 'status':
                self._status_var.set(ev[1])
            elif ev[0] == 'error':
                self._stop()
                self._transcript.config(state='normal')
                self._transcript.insert(tk.END, f'\nERROR: {ev[1]}\n')
                self._transcript.config(state='disabled')
                self._status_var.set('Error — see transcript')
        self.after(100, self._poll)


if __name__ == '__main__':
    if AUTH_AVAILABLE and _auth is not None:
        try:
            ver_info = _auth.check_app_version(APP_VERSION)
            if ver_info.get('force_update', False):
                print(f'[Version] Update required: installed v{APP_VERSION}, min required: v{ver_info.get('min_version')}')
                UpdateRequiredScreen(ver_info).mainloop()
                sys.exit(0)
        except Exception as e:
            print(f'[Version] Startup version check failed: {e}')
    if AUTH_AVAILABLE and _auth is not None:
        session = _auth.load_session()
        if session and session.get('google_id'):
            google_id = session['google_id']
            device_token = session.get('device_token', '')
            print(f'[Auth] Checking session for {session.get('email', '?')}')
            status, user = _auth.validate_session(google_id, device_token)
            if status == 'valid':
                print('[Auth] Session valid — skipping login')
                SetupScreen(google_user=user).mainloop()
            elif status == 'kicked':
                print('[Auth] Kicked by another device login')
                _auth.clear_session()
                LoginScreen(kicked_message='⚠️  You were signed out because your account\nwas used to log in on another device.').mainloop()
            elif status == 'db_error':
                print('[Auth] DB unreachable — using cached session data')
                cached_user = {'google_id': google_id, 'email': session.get('email', ''), 'name': session.get('name', ''), 'picture': session.get('picture', ''), 'profile': _load_profile()}
                SetupScreen(google_user=cached_user).mainloop()
            else:
                print('[Auth] User not found in DB — showing login')
                _auth.clear_session()
                LoginScreen().mainloop()
        LoginScreen().mainloop()
    else:
        LoginScreen().mainloop()
# WARNING: Decompyle incomplete
