"""
============================================================================
 DISCORD RICH PRESENCE - CODE DETECTOR (Windows 10/11)
 - Each language uses its OWN Discord Application (its own CLIENT_ID)
 - Switches connection only when the detected language actually changes
 - Skips update() calls when nothing changed, to avoid spamming the API
============================================================================
Automatically detects what language you're coding in (Python, C, C++, C#,
Go, Java, ...) by reading the title of the active window (VS Code, Sublime,
Notepad++, PyCharm, Visual Studio, ...) and updates Discord Rich Presence
in real time.

ANTI RATE-LIMIT DESIGN:
  - Each language has its own Discord Application / CLIENT_ID (see LANG_MAP
    below). The script only connects to the Discord Application that
    matches the language you are CURRENTLY editing.
  - If you switch to a file whose language uses the SAME client_id as the
    one you're already connected to (e.g. .cpp -> .cc), it will NOT
    disconnect/reconnect - it just calls update() with the new details.
  - If the language changes to one with a DIFFERENT client_id, the old
    connection is closed and a new one is opened for that language's app.
  - If you switch away from a recognized file (or Discord itself isn't
    running), the presence is cleared and the connection is closed until
    a recognized language is detected again.
  - Before every update() call, the new payload is compared against the
    last one sent. If nothing changed, the call is skipped entirely.

--------------------------------- SETUP -----------------------------------
1) Install Python 3.9+ (https://www.python.org/downloads/), check "Add to
   PATH" during install.

2) Open CMD and install the required libraries:
       pip install pypresence pywin32 psutil colorama

3) For EACH language you want to track, create a separate Discord
   Application (this is what gives you a working large_image / name per
   language):
     a. Go to https://discord.com/developers/applications
     b. "New Application" -> give it a name (e.g. "Coding - Python")
     c. "General Information" tab -> copy the "APPLICATION ID"
     d. Paste it into LANG_MAP below as that language's "client_id"

   (The CLIENT_IDs below are already filled in for C#, Python, C++, C,
   Go, and Java. Add more languages the same way if you want them.)

4) (Optional) In each Discord Application's "Rich Presence" -> "Art
   Assets" tab, upload an icon. The script sends the language's lowercase
   name as the image key (e.g. "python", "c++"), so name your uploaded
   asset to match, or just leave it - the script still works without it.

5) Open Discord first, then run:
       python discord_rpc_windows.py

6) Leave the CMD window running while you code. Stop with Ctrl+C.
============================================================================
"""

import os
import re
import sys
import time
import ctypes

# ---------------------------------------------------------------------------
# Check required libraries
# ---------------------------------------------------------------------------
missing = []
try:
    import win32gui
    import win32process
except ImportError:
    missing.append("pywin32")

try:
    import psutil
except ImportError:
    missing.append("psutil")

try:
    from pypresence import Presence
    from pypresence.exceptions import (
        DiscordNotFound,
        InvalidID,
        PyPresenceException,
    )
except ImportError:
    missing.append("pypresence")

if missing:
    print("Missing libraries. Run this command then try again:")
    print("    pip install " + " ".join(missing))
    sys.exit(1)

try:
    from colorama import init, Fore, Style

    init(autoreset=True)
except ImportError:

    class _NoColor:
        def __getattr__(self, _):
            return ""

    Fore = Style = _NoColor()


# =============================== CONFIG =====================================

UPDATE_INTERVAL = 2          # seconds between each active-window scan
RECONNECT_DELAY = 5          # seconds to wait before retrying a failed connect

APP_NAME_TAG = "Coding Status"

# Process (.exe) names of editors/IDEs this script can recognize
EDITOR_PROCESSES = {
    "code.exe": "Visual Studio Code",
    "code - insiders.exe": "VS Code Insiders",
    "sublime_text.exe": "Sublime Text",
    "notepad++.exe": "Notepad++",
    "pycharm64.exe": "PyCharm",
    "pycharm.exe": "PyCharm",
    "webstorm64.exe": "WebStorm",
    "clion64.exe": "CLion",
    "idea64.exe": "IntelliJ IDEA",
    "devenv.exe": "Visual Studio",
    "atom.exe": "Atom",
    "notepad.exe": "Notepad",
    "windowsterminal.exe": "Windows Terminal",
    "cmd.exe": "Command Prompt",
    "powershell.exe": "PowerShell",
    "vim.exe": "Vim",
    "neovide.exe": "Neovim",
}

# File extension -> { client_id, name }. Each entry is its own Discord
# Application. Add more languages by following the same pattern.
LANG_MAP = {
    ".cs":  {"client_id": "1525189999610433626", "name": "C#"},
    ".py":  {"client_id": "1514648392284897331", "name": "Python"},
    ".cpp": {"client_id": "1525040408369827911", "name": "C++"},
    ".cc":  {"client_id": "1525040408369827911", "name": "C++"},
    ".cxx": {"client_id": "1525040408369827911", "name": "C++"},
    ".hpp": {"client_id": "1525040408369827911", "name": "C++"},
    ".c":   {"client_id": "1525174693294768138", "name": "C"},
    ".h":   {"client_id": "1525174693294768138", "name": "C"},
    ".go":  {"client_id": "1525176217320292452", "name": "Go"},
    ".java": {"client_id": "1525188759581229139", "name": "Java"},
}

BANNER = r"""
  ____  _                       _   ____   ____
 |  _ \(_)___  ___ ___  _ __ __| | |  _ \ |  _ \  ___
 | | | | / __|/ __/ _ \| '__/ _` | | |_) || |_) |/ __|
 | |_| | \__ \ (_| (_) | | | (_| | |  _ < |  __/| (__
 |____/|_|___/\___\___/|_|  \__,_| |_| \_\|_|    \___|

          Code bt LOLbyte - Rich Presence
"""


# ============================== HELPERS ======================================

def clear_console():
    os.system("cls" if os.name == "nt" else "clear")


def set_console_title(title):
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def get_active_window_info():
    """Get the window title and process (.exe) name of the focused window."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)
        exe_name = process.name().lower()
        return title, exe_name
    except Exception:
        return "", ""


def extract_filename_from_title(title):
    """
    Extract the filename from the window title for common formats:
      - VS Code:    "* main.py - my-project - Visual Studio Code"
      - Sublime:    "main.py - Sublime Text"
      - Notepad++:  "main.py - Notepad++"
      - PyCharm:    "main.py - my-project - PyCharm"
    """
    if not title:
        return None
    cleaned = title.strip().lstrip("*").lstrip("\u25cf").strip()
    first_segment = cleaned.split(" - ")[0].strip()
    return first_segment


def detect_language(title):
    """
    Returns (lang_name, client_id, filename) if the window title contains
    a recognized file extension, otherwise (None, None, None).
    """
    filename = extract_filename_from_title(title)
    if not filename:
        return None, None, None

    match = re.search(r"(\.[a-zA-Z0-9_]+)$", filename)
    if not match:
        return None, None, None

    ext = match.group(1).lower()
    if ext in LANG_MAP:
        entry = LANG_MAP[ext]
        return entry["name"], entry["client_id"], filename

    return None, None, None


def format_elapsed(start_time):
    elapsed = int(time.time() - start_time)
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def connect_to(client_id):
    """Connect to the Discord Application that matches this client_id."""
    try:
        rpc = Presence(client_id)
        rpc.connect()
        return rpc
    except DiscordNotFound:
        print(Fore.RED + "[ERROR] Discord isn't running. Please open Discord first.")
        return None
    except InvalidID:
        print(Fore.RED + f"[ERROR] Invalid client_id: {client_id}")
        return None
    except Exception as e:
        print(Fore.RED + f"[ERROR] Could not connect to Discord: {e}")
        return None


def safe_close(rpc):
    if rpc:
        try:
            rpc.clear()
            rpc.close()
        except Exception:
            pass


def render_status(editor_name, lang_name, filename, session_start, connected, update_count):
    clear_console()
    print(Fore.CYAN + Style.BRIGHT + BANNER)

    status_color = Fore.GREEN if connected else Fore.RED
    print(status_color + f"  [Discord]      : {'Connected' if connected else 'Not connected / idle'}")
    print(Fore.WHITE + f"  [Time]         : {time.strftime('%H:%M:%S %d-%m-%Y')}")
    print(Fore.WHITE + f"  [Session]      : {format_elapsed(session_start)}")
    print(Fore.WHITE + f"  [Updates sent] : {update_count}")
    print(Fore.WHITE + "  " + "-" * 60)

    if lang_name:
        print(Fore.YELLOW + Style.BRIGHT + f"  Editing      : {filename}")
        print(Fore.YELLOW + Style.BRIGHT + f"  Language     : {lang_name}")
        print(Fore.MAGENTA + f"  Editor       : {editor_name}")
    elif editor_name:
        print(Fore.YELLOW + f"  Open         : {editor_name}")
        print(Fore.MAGENTA + "  Language     : unrecognized file / no matching extension")
    else:
        print(Fore.LIGHTBLACK_EX + "  No coding activity detected (in another app)")

    print(Fore.WHITE + "  " + "-" * 60)
    print(Fore.LIGHTBLACK_EX + "  Press Ctrl+C to stop...")


def main():
    set_console_title(APP_NAME_TAG)
    session_start = time.time()

    rpc = None
    current_client_id = None
    last_payload_key = None
    last_connect_attempt = 0
    update_count = 0

    try:
        while True:
            title, exe = get_active_window_info()
            editor_name = EDITOR_PROCESSES.get(exe)
            lang_name, target_client_id, filename = (
                detect_language(title) if editor_name else (None, None, None)
            )

            if target_client_id:
                # Need a connection to this language's Discord Application.
                if target_client_id != current_client_id:
                    safe_close(rpc)
                    rpc = None
                    if time.time() - last_connect_attempt >= RECONNECT_DELAY:
                        rpc = connect_to(target_client_id)
                        last_connect_attempt = time.time()
                    if rpc:
                        current_client_id = target_client_id
                        last_payload_key = None
                    else:
                        current_client_id = None

                if rpc:
                    details = f"Editing: {filename}"
                    state = f"Language: {lang_name}"
                    large_image = lang_name.lower()
                    large_text = lang_name

                    payload_key = (target_client_id, details, state)
                    if payload_key != last_payload_key:
                        try:
                            rpc.update(
                                details=details,
                                state=state,
                                large_image=large_image,
                                large_text=large_text,
                                start=session_start,
                            )
                            last_payload_key = payload_key
                            update_count += 1
                        except PyPresenceException:
                            rpc = None
                            current_client_id = None
                        except Exception:
                            rpc = None
                            current_client_id = None
            else:
                # No recognized language focused -> clear presence & disconnect.
                if rpc:
                    safe_close(rpc)
                    rpc = None
                    current_client_id = None
                    last_payload_key = None

            render_status(
                editor_name, lang_name, filename, session_start,
                connected=rpc is not None, update_count=update_count,
            )

            time.sleep(UPDATE_INTERVAL)

    except KeyboardInterrupt:
        print(Fore.CYAN + "\nStopping...")
    finally:
        safe_close(rpc)
        print(Fore.CYAN + "Discord Rich Presence stopped. See you next time!")


if __name__ == "__main__":
    main()
