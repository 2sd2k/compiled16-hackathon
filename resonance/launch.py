"""One-step launcher for Resonance on macOS or Windows (used by start-mac.command / start-windows.bat).

First run: creates .venv, installs requirements, and sets up music (asks for the team's Spotify
Client ID, or press Enter for the offline Jamendo music). Every run: starts the controller with
the default flow (Josh's simulated heart rate -> his adaptive layer -> music) and opens the
Developer / Demo Console in the browser. Standard library only.

Usage: python resonance/launch.py [--offline] [--live | --classic] [controller.py options]
  --live     live heart rate from a Garmin watch broadcasting over Bluetooth (Josh's receiver)
  --classic  original rule-based picks with the rise/rest simulator (no adaptive layer)
  any controller.py options replace the default flow
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
VENV = REPO / ".venv"
CONFIG = HERE / "spotify_config.json"
URL = "http://127.0.0.1:8765"


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def running():
    try:
        with urllib.request.urlopen(URL + "/state", timeout=1):
            return True
    except Exception:
        return False


def ensure_env():
    if not venv_python().exists():
        print("First run: setting up Python environment (one minute)...")
        venv.create(VENV, with_pip=True)
    py = str(venv_python())
    if subprocess.run([py, "-c", "import pygame, numpy, bleak"], capture_output=True).returncode != 0:
        print("Installing requirements...")
        r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                            "-r", str(HERE / "requirements.txt")])
        if r.returncode != 0:
            sys.exit("Install failed. Python 3.12 is the tested version: https://www.python.org/downloads/")
    return py


def spotify_ready():
    try:
        return bool(json.loads(CONFIG.read_text()).get("client_id", "").strip())
    except (OSError, ValueError):
        return False


def choose_music(py, offline):
    if not offline and spotify_ready():
        return "spotify"
    if not offline:
        print("\nMusic: Resonance plays calm Spotify songs through the Spotify app (needs Spotify Premium,")
        print("the Spotify desktop app, and the team's Spotify Client ID).")
        cid = input("Paste the Spotify Client ID, or press Enter to use offline music: ").strip()
        if cid:
            CONFIG.write_text(json.dumps({"client_id": cid}, indent=2))
            print(f"Saved to {CONFIG.name} (not shared to Git). Click 'Connect Spotify' in the console once.")
            return "spotify"
    print("Using offline music (Jamendo). Downloading the calm tracks if needed (~90 MB the first time)...")
    subprocess.run([py, str(HERE / "fetch_audio.py")])
    return "jamendo"


def open_console_when_ready():
    for _ in range(60):
        if running():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


def adaptive_flow(music, patient="sim-patient-01"):
    """Default flow: simulated heart rate -> Josh's adaptive layer -> controller plays the music."""
    return ["--source", "sim", "--sim-response", "--demo-approve-calm", "--patient-id", patient,
            "--adaptive-state", str(REPO / "data" / "raw" / f"adaptive-{music}-{patient}.json")]


def main():
    sys.stdout.reconfigure(line_buffering=True)  # keep our messages in order with the controller's
    args = sys.argv[1:]
    offline, classic, live = "--offline" in args, "--classic" in args, "--live" in args
    args = [a for a in args if a not in ("--offline", "--classic", "--live")]
    if running():
        print(f"Resonance is already running. Opening {URL}")
        webbrowser.open(URL)
        return
    py = ensure_env()
    music = choose_music(py, offline)
    if live:
        flow, what = ["--source", "garmin"], ", LIVE Garmin heart rate (start Broadcast Heart Rate on the watch)"
    elif classic or args:
        flow, what = [], ""
    else:
        flow, what = adaptive_flow(music), ", adaptive layer on simulated heart rate"
    print(f"\nStarting Resonance ({music} music{what}).")
    print(f"The console opens at {URL}. Keep this window open; close it or press q here to stop.\n")
    threading.Thread(target=open_console_when_ready, daemon=True).start()
    os.chdir(REPO)
    sys.exit(subprocess.call([py, str(HERE / "controller.py"), "--music", music, *flow, *args]))


if __name__ == "__main__":
    main()
