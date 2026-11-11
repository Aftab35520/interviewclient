# Aftaro desktop frontend recovery

This folder contains the desktop application recovered from `Aftaro-Setup.exe`.

## Files

- `main.py` — decompiled main Tkinter application. It contains the login, profile setup, audio transcription, and interview assistant screens.
- `auth.py` — decompiled authentication and account API client.
- `main.pyc`, `auth.pyc` — original Python 3.12 bytecode modules extracted from the bundled executable.
- `aftaro.ico`, `aftaroLogo.png` — original application branding assets.

## What the installer contained

The installer was an Inno Setup 6.7.0 package. Its payload was `Aftaro.exe`, `aftaro.ico`, and `aftaroLogo.png`. `Aftaro.exe` is a PyInstaller one-file application containing Python 3.12 bytecode. The desktop UI is native Tkinter, not Electron or a web frontend.

The recovered UI describes a dark purple and gold floating window with a compact transcript pane and a larger AI response pane. It supports automatic submission after silence and manual submission, Google OAuth login, profile setup, screen analysis, credit display, and always-on-top / capture-protection controls.

## Recovery limits

`main.py` and `auth.py` were decompiled from bytecode. They parse as Python source, but decompilation cannot restore the original comments, formatting, or guarantee exact source equivalence. The decompiler reported incomplete reconstruction for `main.py`; keep the `.pyc` files and the original installer as the authoritative artifacts. No original build configuration or dependency lockfile was included in the installer.

The app uses Python 3.12. Its bundled modules indicate Tkinter, Pillow, Requests, python-socketio, SpeechRecognition, sounddevice, PyAudio, and NumPy, among other transitive dependencies. The code refers to the backend URL `https://interviewserver-aef9.onrender.com/` and the Aftaro payment/download pages.

## Run from source

A project-local virtual environment was created at `.venv` and populated with the app's external runtime libraries. It uses the available Python 3.13 interpreter; the original bundled bytecode was built with Python 3.12, while the decompiled source is running under 3.13.

To start the UI again from this folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe .\main.py
```

`requirements.txt` records the installed dependency versions. The recovered source has small fixes for two invalid expressions emitted by the bytecode decompiler so the UI callbacks can run.
