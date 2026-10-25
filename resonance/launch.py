"""One-step launcher for Resonance on macOS or Windows.

First run creates .venv, installs requirements, and downloads local Jamendo music.
Default: live Garmin input, with fixed ranking until a volunteer profile and audio
approvals are supplied. The optional console opens in the browser.

Usage: python resonance/launch.py [--live | --simulated | --classic] [controller.py options]
  --live       explicit live input; add --patient-id PERSON and --approve-calm to learn
  --simulated  simulated input and adaptive learning for testing without a watch
  --classic    simulated input with fixed ranking; --live --classic uses the watch
"""
import json
import argparse
import hashlib
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
    return running_state() is not None


def running_state():
    try:
        with urllib.request.urlopen(URL + "/state", timeout=1) as response:
            state = json.load(response)
            return state if isinstance(state, dict) else None
    except Exception:
        return None


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
    offline, classic = "--offline" in args, "--classic" in args
    explicit_live, simulated = "--live" in args, "--simulated" in args
    args = [a for a in args if a not in ("--offline", "--classic", "--live", "--simulated")]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--patient-id')
    parser.add_argument('--source')
    parser.add_argument('--music', default='jamendo')
    parser.add_argument('--adaptive-state')
    parser.add_argument('--external-selection', action='store_true')
    parser.add_argument('--approved-tracks')
    parser.add_argument('--approve-calm', action='store_true')
    parser.add_argument('--demo-approve-calm', action='store_true')
    parser.add_argument('--sim-response', action='store_true')
    parser.add_argument('--no-audio-effect', action='store_true')
    options, _ = parser.parse_known_args(args)
    source = options.source or ('sim' if simulated or (classic and not explicit_live) else 'garmin')
    live = source.split(':')[0] == 'garmin'
    if explicit_live and (not live or simulated):
        sys.exit('--live cannot be combined with a simulated or recorded source.')
    if simulated and source != 'sim':
        sys.exit('--simulated requires --source sim.')
    if offline and options.music != 'jamendo':
        sys.exit('--offline requires Jamendo playback.')
    if classic and (options.adaptive_state or options.external_selection):
        sys.exit('--classic cannot be combined with adaptive selection.')
    learning = not classic and (source == 'sim' or bool(options.adaptive_state or options.external_selection)
                               or (live and bool(options.patient_id or options.approved_tracks or options.approve_calm)))
    if live and learning:
        if not options.patient_id or not (options.approved_tracks or options.approve_calm):
            sys.exit('Live learning needs --patient-id PERSON and --approve-calm (or --approved-tracks FILE).')
        if options.music != 'jamendo':
            sys.exit('Live learning uses Jamendo. Remove --music spotify.')
    if running():
        state = running_state() or {}
        actual_kind = 'live' if state.get('source_kind') == 'live' or state.get('live') else state.get('source_kind')
        requested_kind = 'live' if live else 'simulated' if source == 'sim' else 'recorded'
        requested_device = source.partition(':')[2] if live else ''
        actual_device = (state.get('live') or {}).get('device_id') or ''
        if (requested_kind != actual_kind or state.get('music') != options.music
                or (options.patient_id and state.get('patient_id') != options.patient_id)
                or bool(state.get('learning_enabled')) != learning
                or (requested_device and requested_device.casefold() != actual_device.casefold())):
            sys.exit('Stop the existing backend with Ctrl+C, then rerun this command. Its source/profile/mode differs.')
        print(f"Resonance is already running. Opening {URL}")
        webbrowser.open(URL)
        return
    py = ensure_env()
    music = options.music
    if music == 'jamendo':
        choose_music(py, True)
    flow = [] if options.source else ['--source', source]
    if live and learning and not (options.adaptive_state or options.external_selection):
        profile = hashlib.sha256(options.patient_id.encode()).hexdigest()[:16]
        flow += ['--adaptive-state', str(REPO/'data'/'raw'/f'adaptive-live-jamendo-{profile}.json')]
    elif source == 'sim' and learning:
        patient = options.patient_id or 'sim-patient-01'
        if not options.patient_id:
            flow += ['--patient-id', patient]
        if not (options.sim_response or options.no_audio_effect):
            flow += ['--sim-response']
        if not (options.approved_tracks or options.approve_calm or options.demo_approve_calm):
            flow += ['--demo-approve-calm']
        if not (options.adaptive_state or options.external_selection):
            profile = hashlib.sha256(patient.encode()).hexdigest()[:16]
            flow += ['--adaptive-state', str(REPO/'data'/'raw'/f'adaptive-simulated-{music}-{profile}.json')]
    what = 'LIVE Garmin' if live else 'SIMULATED' if source == 'sim' else 'recorded'
    print(f"\nStarting Resonance ({music} music, {what} heart rate, {'adaptive' if learning else 'fixed'} ranking).")
    print(f"The console opens at {URL}. Keep this window open; close it or press q here to stop.\n")
    threading.Thread(target=open_console_when_ready, daemon=True).start()
    os.chdir(REPO)
    sys.exit(subprocess.call([py, str(HERE / "controller.py"), "--music", music, *flow, *args]))


if __name__ == "__main__":
    main()
