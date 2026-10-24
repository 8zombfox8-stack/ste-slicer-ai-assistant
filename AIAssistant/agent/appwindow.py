# Opening the chat as a separate application window (no address bar, no tabs).
# Chromium browsers support "--app=<url>"; the first one found is used: Edge (present on updated Windows 10/11),
# Chrome, Yandex Browser. Without any of them the chat opens in the default browser.

import json
import os
import subprocess
import webbrowser

WINDOW_ARGS = ["--window-size=1280,860", "--no-first-run", "--no-default-browser-check"]


def _candidates():
    env = os.environ
    roots = [env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), env.get("ProgramFiles", r"C:\Program Files"),
             env.get("LOCALAPPDATA", "")]
    relative = [r"Microsoft\Edge\Application\msedge.exe",
                r"Google\Chrome\Application\chrome.exe",
                r"Yandex\YandexBrowser\Application\browser.exe",
                r"Chromium\Application\chrome.exe"]
    for rel in relative:
        for root in roots:
            if root:
                yield os.path.join(root, rel)


def find_browser():
    for path in _candidates():
        if os.path.isfile(path):
            return path
    return None


def profile_dir():
    # A separate profile: the chat window is its own app, independent of the user's browser windows.
    path = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "STE_AI_Assistant", "window")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def open_window(url):
    browser = find_browser()
    if browser is None:
        webbrowser.open(url)
        return "browser"
    subprocess.Popen([browser, "--app=" + url, "--user-data-dir=" + profile_dir()] + WINDOW_ARGS,
                     close_fds=True)
    return os.path.basename(browser)


def write_port_file(port, slicer_exe):
    # Read by the "AI Assistant" launcher to find the running chat server.
    folder = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "steslicer")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(os.path.join(folder, "ai_assistant.json"), "w", encoding="utf-8") as f:
        json.dump({"port": port, "pid": os.getpid(), "url": "http://127.0.0.1:%d/" % port,
                   "slicer_exe": slicer_exe}, f)
