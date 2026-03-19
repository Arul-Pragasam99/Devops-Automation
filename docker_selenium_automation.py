"""
Docker Lifecycle Automation with Selenium
==========================================
WINDOW BEHAVIOUR (fixed):
  - Docker Desktop launches FIRST, stays MAXIMIZED when shown
  - Chrome opens MAXIMIZED and NEVER randomly minimizes
  - Docker Desktop is shown at: image build, container run, cleanup
  - Chrome is brought back to front after each Docker Desktop view
  - No background threads that fight over window focus

TABS:
  Tab 1 - Live status dashboard
  Tab 2 - Docker Hub
  Tab 3 - Docker Build docs
  Tab 4 - Docker Run docs

Requirements:
    pip install selenium webdriver-manager docker
    Docker Desktop must be installed and running.
"""

import os
import sys
import time
import ctypes
import subprocess
import tempfile
import threading
import http.server
import socketserver
import urllib.request

import docker
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════
IMAGE_NAME     = "selenium-demo-image"
IMAGE_TAG      = "latest"
CONTAINER_NAME = "selenium-demo-container"
FULL_IMAGE     = f"{IMAGE_NAME}:{IMAGE_TAG}"
STATUS_PORT    = 8787

DOCKERHUB_URL    = "https://hub.docker.com/_/python"
DOCKER_BUILD_URL = "https://docs.docker.com/reference/cli/docker/image/build/"
DOCKER_RUN_URL   = "https://docs.docker.com/reference/cli/docker/container/run/"

DOCKERFILE_CONTENT = """\
FROM python:3.11-alpine
LABEL maintainer="selenium-demo"
RUN echo "Hello from Docker!" > /message.txt
CMD ["cat", "/message.txt"]
"""

DOCKER_DESKTOP_PATHS = [
    r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
    r"C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Docker\Docker Desktop.exe"),
]

# ══════════════════════════════════════════════════════════════
# SECTION 1 — Windows API helpers (stable, no racing threads)
# ══════════════════════════════════════════════════════════════

_user32 = ctypes.windll.user32

def _find_hwnd(title_substring: str):
    """
    Return the HWND of the first VISIBLE window whose title
    contains title_substring (case-insensitive). Returns 0 if not found.
    """
    result = ctypes.c_int(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    def _cb(hwnd, _lp):
        if _user32.IsWindowVisible(hwnd):
            length = _user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_substring.lower() in buf.value.lower():
                    result.value = hwnd
                    return False   # stop enumeration
        return True

    _user32.EnumWindows(_cb, 0)
    return result.value


def _bring_to_front_maximized(hwnd):
    """
    Reliably restore + maximize + foreground a window by HWND.
    Uses SW_RESTORE first to un-minimize, then SW_MAXIMIZE.
    """
    SW_RESTORE  = 9
    SW_MAXIMIZE = 3
    _user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.3)
    _user32.ShowWindow(hwnd, SW_MAXIMIZE)
    time.sleep(0.2)
    # SetForegroundWindow needs the thread to be attached to the input
    _user32.SetForegroundWindow(hwnd)
    _user32.BringWindowToTop(hwnd)
    time.sleep(0.3)


def _minimize_hwnd(hwnd):
    SW_MINIMIZE = 6
    _user32.ShowWindow(hwnd, SW_MINIMIZE)
    time.sleep(0.3)


def show_app_maximized(title_substring: str, wait_sec: int = 12) -> bool:
    """
    Wait up to wait_sec for a window matching title_substring,
    then bring it to front MAXIMIZED. Returns True if found.
    """
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        hwnd = _find_hwnd(title_substring)
        if hwnd:
            _bring_to_front_maximized(hwnd)
            return True
        time.sleep(0.5)
    print(f"[WARN] Window '{title_substring}' not found after {wait_sec}s.")
    return False


def minimize_app(title_substring: str):
    """Minimize a window matching title_substring."""
    hwnd = _find_hwnd(title_substring)
    if hwnd:
        _minimize_hwnd(hwnd)


# ══════════════════════════════════════════════════════════════
# SECTION 2 — Docker Desktop launcher
# ══════════════════════════════════════════════════════════════

def _docker_desktop_process_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Docker Desktop.exe"],
            capture_output=True, text=True, timeout=5
        ).stdout
        return "Docker Desktop.exe" in out
    except Exception:
        return False


def _find_docker_desktop_exe():
    for p in DOCKER_DESKTOP_PATHS:
        p = os.path.normpath(p)
        if os.path.isfile(p):
            return p
    return None


def launch_docker_desktop() -> bool:
    """
    Start Docker Desktop if not running, then bring it to front MAXIMIZED.
    Returns True when the window is visible and maximized.
    """
    if not _docker_desktop_process_running():
        exe = _find_docker_desktop_exe()
        if exe:
            print(f"[DOCKER DESKTOP] Launching: {exe}")
            os.startfile(exe)
            print("[DOCKER DESKTOP] Waiting up to 25s for window to appear ...")
        else:
            print("[WARN] Docker Desktop exe not found. Open it manually.")
        ok = show_app_maximized("Docker Desktop", wait_sec=25)
    else:
        print("[DOCKER DESKTOP] Already running — bringing to front maximized ...")
        ok = show_app_maximized("Docker Desktop", wait_sec=10)

    if ok:
        print("[DOCKER DESKTOP] ✓ Visible and maximized.")
    return ok


def show_docker_desktop():
    """Bring Docker Desktop to front MAXIMIZED (already running)."""
    print("[DOCKER DESKTOP] Showing Docker Desktop ...")
    ok = show_app_maximized("Docker Desktop", wait_sec=8)
    if not ok:
        print("[WARN] Docker Desktop window not found.")
    return ok


def hide_docker_desktop():
    """Minimize Docker Desktop."""
    minimize_app("Docker Desktop")
    print("[DOCKER DESKTOP] Minimized.")


# ══════════════════════════════════════════════════════════════
# SECTION 3 — Chrome / Selenium helpers (stable maximization)
# ══════════════════════════════════════════════════════════════

def init_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-sync")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--start-maximized")
    # Do NOT add --window-size — it overrides --start-maximized
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    svc = Service(ChromeDriverManager().install())
    svc.creation_flags = 0x08000000   # CREATE_NO_WINDOW — hides driver console

    driver = webdriver.Chrome(service=svc, options=opts)
    driver.maximize_window()          # belt-and-suspenders
    return driver


def open_new_tab(driver: webdriver.Chrome, url: str):
    """Open url in a new tab, switch to it, and maximize."""
    driver.execute_script("window.open(arguments[0], '_blank');", url)
    driver.switch_to.window(driver.window_handles[-1])
    driver.maximize_window()
    time.sleep(1.5)


def switch_to_tab(driver: webdriver.Chrome, index: int):
    """Switch to a tab by index and maximize — no random resizing."""
    handles = driver.window_handles
    if index < len(handles):
        driver.switch_to.window(handles[index])
        driver.maximize_window()
        time.sleep(0.4)


def focus_chrome(driver: webdriver.Chrome):
    """
    Bring Chrome back to front using Windows API (more reliable than
    just calling driver.maximize_window after Docker Desktop was shown).
    """
    # First maximize via Selenium API
    try:
        driver.maximize_window()
    except Exception:
        pass

    # Then use Win32 to bring the Chrome window to front
    hwnd = _find_hwnd("Google Chrome")
    if not hwnd:
        hwnd = _find_hwnd("Docker Automation")   # our status page title
    if hwnd:
        _bring_to_front_maximized(hwnd)
    time.sleep(0.5)


def safe_get(driver: webdriver.Chrome, url: str, retries: int = 4):
    """Navigate with retries."""
    for i in range(retries):
        try:
            driver.get(url)
            return
        except Exception as e:
            if i < retries - 1:
                print(f"[WARN] driver.get attempt {i+1} failed: {e}")
                time.sleep(1.2)
            else:
                raise


def go_to_status(driver: webdriver.Chrome, current_step: int = -1):
    """
    Switch to Tab 1 (status dashboard), reload the latest HTML from the
    local server, and scroll the active step row into view.
    No driver.refresh() — that destroys scroll position and races with
    the server update. Instead we navigate to the URL so the browser
    fetches the freshest HTML, then scroll via execute_script.
    """
    switch_to_tab(driver, 0)
    try:
        # Re-navigate (not refresh) so we always get latest server HTML
        driver.get(f"http://127.0.0.1:{STATUS_PORT}/")
        driver.maximize_window()
        # Scroll the active row into centre view
        if current_step >= 0:
            driver.execute_script(
                "var el = document.getElementById('s' + arguments[0]);"
                "if (el) { el.scrollIntoView({behavior: 'smooth', block: 'center'}); }",
                current_step
            )
    except Exception:
        pass
    time.sleep(0.7)


# ══════════════════════════════════════════════════════════════
# SECTION 4 — HTML Status Dashboard
# ══════════════════════════════════════════════════════════════

def _build_html(steps: list, current: int = -1) -> bytes:
    rows = ""
    for i, s in enumerate(steps):
        if s["status"] == "ok":
            icon, color = "&#x2705;", "#22c55e"
        elif s["status"] == "error":
            icon, color = "&#x274C;", "#ef4444"
        elif i == current:
            icon, color = "&#x1F504;", "#38bdf8"
        else:
            icon, color = "&#x23F3;", "#94a3b8"

        hl = (
            "background:rgba(56,189,248,0.13);border-left:4px solid #38bdf8;"
            if i == current
            else "border-left:4px solid transparent;"
        )
        glow = " box-shadow:inset 0 0 0 1px rgba(56,189,248,0.25);" if i == current else ""
        rows += (
            f'\n        <tr id="s{i}" style="{hl}{glow}transition:all .35s;">'
            f'\n          <td style="padding:15px 18px;font-size:1.15rem">{icon}</td>'
            f'\n          <td style="padding:15px 18px;color:#e2e8f0;font-weight:600">{s["label"]}</td>'
            f'\n          <td style="padding:15px 18px;color:{color};font-family:monospace;font-size:.85rem">{s.get("detail","")}</td>'
            f'\n        </tr>'
        )

    done = sum(1 for s in steps if s["status"] == "ok")
    pct  = int(done / len(steps) * 100)

    # NO meta-refresh — Selenium drives the page via execute_script scrolling
    html = (
        "<!DOCTYPE html>\n"
        "<html lang='en'>\n"
        "<head>\n"
        "  <meta charset='UTF-8'/>\n"
        "  <title>Docker Automation \u2014 Live Status</title>\n"
        "  <style>\n"
        "    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@800&display=swap');\n"
        "    *{box-sizing:border-box;margin:0;padding:0}\n"
        "    html,body{height:100%}\n"
        "    body{background:#0f172a;font-family:'JetBrains Mono',monospace;padding:2rem;overflow-y:auto}\n"
        "    .card{background:#1e293b;border:1px solid #334155;border-radius:18px;\n"
        "           max-width:960px;margin:0 auto;padding:2.5rem;\n"
        "           box-shadow:0 30px 70px rgba(0,0,0,.6)}\n"
        "    h1{font-family:'Syne',sans-serif;font-size:2rem;color:#38bdf8;margin-bottom:.3rem}\n"
        "    .sub{color:#64748b;font-size:.82rem;margin-bottom:1.4rem}\n"
        "    .bar-wrap{background:#334155;border-radius:99px;height:10px;margin-bottom:.4rem;overflow:hidden}\n"
        f"    .bar{{background:linear-gradient(90deg,#0ea5e9,#38bdf8);height:100%;border-radius:99px;width:{pct}%;transition:width .5s ease}}\n"
        "    .pct{color:#64748b;font-size:.78rem;text-align:right;margin-bottom:1.6rem}\n"
        "    table{width:100%;border-collapse:collapse}\n"
        "    tr{border-bottom:1px solid #1e293b}\n"
        "    tr:last-child{border-bottom:none}\n"
        "    th{color:#475569;font-size:.72rem;text-transform:uppercase;\n"
        "        letter-spacing:.06em;padding:8px 18px;text-align:left;\n"
        "        border-bottom:1px solid #334155;position:sticky;top:0;background:#1e293b;z-index:1}\n"
        "    .badge{display:inline-block;background:#0ea5e9;color:#fff;\n"
        "            border-radius:999px;padding:2px 14px;font-size:.72rem;\n"
        "            margin-left:10px;vertical-align:middle}\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "<div class='card'>\n"
        "  <h1>&#x1F433; Docker Automation <span class='badge'>Selenium Live</span></h1>\n"
        "  <p class='sub'>Build &#x2192; Run &#x2192; Verify &#x2192; Auto Cleanup &nbsp;|&nbsp; Docker Desktop opens automatically</p>\n"
        "  <div class='bar-wrap'><div class='bar'></div></div>\n"
        f"  <p class='pct'>{pct}% complete &nbsp;({done}/{len(steps)} steps done)</p>\n"
        "  <table>\n"
        "    <thead><tr><th>Status</th><th>Step</th><th>Detail</th></tr></thead>\n"
        f"    <tbody>{rows}</tbody>\n"
        "  </table>\n"
        "</div>\n"
        "</body>\n"
        "</html>"
    )
    return html.encode("utf-8")


# ══════════════════════════════════════════════════════════════
# SECTION 5 — Local HTTP server (thread-safe)
# ══════════════════════════════════════════════════════════════

_page_lock  = threading.Lock()
_page_bytes = b""

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with _page_lock:
            body = _page_bytes
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_): pass


def _set_page(steps, current=-1):
    global _page_bytes
    with _page_lock:
        _page_bytes = _build_html(steps, current)


def start_server():
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", STATUS_PORT), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wait_for_server(port: int, timeout: int = 12) -> bool:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


# ══════════════════════════════════════════════════════════════
# SECTION 6 — Docker helpers
# ══════════════════════════════════════════════════════════════

def get_docker_client():
    try:
        c = docker.from_env()
        c.ping()
        return c
    except Exception as e:
        print(f"[ERROR] Docker daemon not reachable: {e}")
        sys.exit(1)


def build_image(client, build_dir: str):
    print(f"[DOCKER] Building {FULL_IMAGE} ...")
    image, logs = client.images.build(path=build_dir, tag=FULL_IMAGE, rm=True)
    for chunk in logs:
        line = chunk.get("stream", "").strip()
        if line:
            print(f"         {line}")
    return image


def run_container(client):
    print(f"[DOCKER] Running {CONTAINER_NAME} ...")
    c = client.containers.run(FULL_IMAGE, name=CONTAINER_NAME, detach=True, remove=False)
    c.wait()
    out = c.logs().decode().strip()
    print(f"[DOCKER] Output: {out}")
    return c, out


def remove_container(client) -> str:
    try:
        client.containers.get(CONTAINER_NAME).remove(force=True)
        print(f"[DOCKER] Container '{CONTAINER_NAME}' removed.")
        return f"'{CONTAINER_NAME}' deleted"
    except docker.errors.NotFound:
        return "Already removed"


def remove_image(client) -> str:
    try:
        client.images.remove(FULL_IMAGE, force=True)
        print(f"[DOCKER] Image '{FULL_IMAGE}' removed.")
        return f"'{FULL_IMAGE}' deleted"
    except docker.errors.ImageNotFound:
        return "Already removed"


# ══════════════════════════════════════════════════════════════
# SECTION 7 — Browser browsing helpers
# ══════════════════════════════════════════════════════════════

def scroll_page(driver, times=4, pause=0.7):
    for _ in range(times):
        driver.execute_script("window.scrollBy(0, 380);")
        time.sleep(pause)


def highlight_codes(driver, max_els=3):
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "code, pre")
        for el in els[:max_els]:
            driver.execute_script(
                "arguments[0].style.outline='3px solid #38bdf8';"
                "arguments[0].style.borderRadius='4px';", el)
            time.sleep(0.35)
    except Exception:
        pass


def browse_dockerhub(driver):
    print("[BROWSER] Tab 2 — Docker Hub")
    switch_to_tab(driver, 1)
    scroll_page(driver, times=3, pause=0.8)
    try:
        search = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='search'],input[placeholder*='earch']")
            )
        )
        driver.execute_script("arguments[0].style.outline='3px solid #38bdf8';", search)
        search.clear()
        search.send_keys("python alpine")
        time.sleep(1)
        search.send_keys(Keys.RETURN)
        time.sleep(2)
        scroll_page(driver, times=2, pause=0.6)
    except Exception:
        scroll_page(driver, times=2, pause=0.7)


def browse_build_docs(driver):
    print("[BROWSER] Tab 3 — Docker Build docs")
    switch_to_tab(driver, 2)
    time.sleep(1.2)
    scroll_page(driver, times=5, pause=0.6)
    highlight_codes(driver)


def browse_run_docs(driver):
    print("[BROWSER] Tab 4 — Docker Run docs")
    switch_to_tab(driver, 3)
    time.sleep(1.2)
    scroll_page(driver, times=5, pause=0.6)
    highlight_codes(driver)


# ══════════════════════════════════════════════════════════════
# SECTION 8 — Main orchestration
# ══════════════════════════════════════════════════════════════

def main():
    status_url = f"http://127.0.0.1:{STATUS_PORT}/"

    steps = [
        {"label": "Docker Desktop launched",           "status": "pending", "detail": ""},
        {"label": "Docker daemon reachable",            "status": "pending", "detail": ""},
        {"label": "Dockerfile written",                 "status": "pending", "detail": ""},
        {"label": "Browsed Docker Hub (Tab 2)",         "status": "pending", "detail": ""},
        {"label": "Browsed Docker Build docs (Tab 3)",  "status": "pending", "detail": ""},
        {"label": "Docker image built",                 "status": "pending", "detail": ""},
        {"label": "Browsed Docker Run docs (Tab 4)",    "status": "pending", "detail": ""},
        {"label": "Container started & run",            "status": "pending", "detail": ""},
        {"label": "Container output verified",          "status": "pending", "detail": ""},
        {"label": "Container removed",                  "status": "pending", "detail": ""},
        {"label": "Image removed",                      "status": "pending", "detail": ""},
        {"label": "All cleanup complete",               "status": "pending", "detail": ""},
    ]

    def upd(current=-1):
        _set_page(steps, current)

    # ── 1. Start HTTP server ─────────────────────────────────
    upd(0)
    server = start_server()
    print(f"[INFO] Waiting for status server on port {STATUS_PORT} ...")
    if not wait_for_server(STATUS_PORT):
        print("[ERROR] Status server failed to start.")
        sys.exit(1)
    print(f"[INFO] Server ready → {status_url}")

    # ── 2. Launch Docker Desktop FIRST (so user sees it) ────
    print("\n[INFO] ═══ STEP: Open Docker Desktop ═══")
    dd_ok = launch_docker_desktop()
    steps[0] = {
        "label": steps[0]["label"],
        "status": "ok" if dd_ok else "error",
        "detail": "Launched & maximized" if dd_ok else "Not found — open manually",
    }
    upd(1)
    time.sleep(3)   # user sees Docker Desktop for 3 seconds before Chrome opens

    # ── 3. Launch Chrome MAXIMIZED ───────────────────────────
    print("[INFO] ═══ STEP: Launch Chrome ═══")
    driver = init_driver()

    # Tab 1 — Status page
    safe_get(driver, status_url)
    driver.maximize_window()
    time.sleep(1)

    # Tab 2 — Docker Hub
    open_new_tab(driver, DOCKERHUB_URL)
    # Tab 3 — Docker Build docs
    open_new_tab(driver, DOCKER_BUILD_URL)
    # Tab 4 — Docker Run docs
    open_new_tab(driver, DOCKER_RUN_URL)

    # Back to status tab — maximized
    go_to_status(driver, 0)
    print("[INFO] Chrome ready with 4 tabs. Starting automation ...\n")

    client = None
    try:
        # ── Docker daemon ────────────────────────────────────
        print("[INFO] ═══ STEP: Docker daemon ═══")
        upd(1)
        client = get_docker_client()
        steps[1] = {"label": steps[1]["label"], "status": "ok", "detail": "Connected"}
        upd(2); go_to_status(driver, 2)

        # ── Write Dockerfile ─────────────────────────────────
        print("[INFO] ═══ STEP: Write Dockerfile ═══")
        build_dir = tempfile.mkdtemp(prefix="sel_docker_")
        df_path   = os.path.join(build_dir, "Dockerfile")
        with open(df_path, "w") as f:
            f.write(DOCKERFILE_CONTENT)
        steps[2] = {"label": steps[2]["label"], "status": "ok", "detail": df_path}
        upd(3); go_to_status(driver, 3)

        # ── Browse Docker Hub ────────────────────────────────
        print("[INFO] ═══ STEP: Browse Docker Hub ═══")
        upd(3)
        browse_dockerhub(driver)
        steps[3] = {"label": steps[3]["label"], "status": "ok",
                    "detail": "Searched 'python alpine'"}
        upd(4); go_to_status(driver, 4)

        # ── Browse Build docs ────────────────────────────────
        print("[INFO] ═══ STEP: Browse Build docs ═══")
        upd(4)
        browse_build_docs(driver)
        steps[4] = {"label": steps[4]["label"], "status": "ok",
                    "detail": "Scrolled build reference"}
        upd(5); go_to_status(driver, 5)

        # ── BUILD IMAGE — show Docker Desktop ────────────────
        print("[INFO] ═══ STEP: Build Docker image ═══")
        upd(5); go_to_status(driver, 5)
        time.sleep(0.5)

        # Show Docker Desktop so user watches the image appear
        show_docker_desktop()
        time.sleep(1.5)

        try:
            build_image(client, build_dir)
            steps[5] = {"label": steps[5]["label"], "status": "ok", "detail": FULL_IMAGE}
        except Exception as e:
            steps[5] = {"label": steps[5]["label"], "status": "error", "detail": str(e)[:80]}
            upd(); hide_docker_desktop(); focus_chrome(driver); go_to_status(driver); raise

        upd(6)
        time.sleep(3)           # user sees new image in Docker Desktop
        hide_docker_desktop()
        focus_chrome(driver)    # Chrome comes back maximized
        go_to_status(driver, 6)

        # ── Browse Run docs ──────────────────────────────────
        print("[INFO] ═══ STEP: Browse Run docs ═══")
        upd(6)
        browse_run_docs(driver)
        steps[6] = {"label": steps[6]["label"], "status": "ok",
                    "detail": "Scrolled run reference"}
        upd(7); go_to_status(driver, 7)

        # ── RUN CONTAINER — show Docker Desktop ─────────────
        print("[INFO] ═══ STEP: Run container ═══")
        upd(7); go_to_status(driver, 7)
        time.sleep(0.5)

        # Show Docker Desktop so user watches container appear
        show_docker_desktop()
        time.sleep(1.5)

        try:
            container, output = run_container(client)
            steps[7] = {"label": steps[7]["label"], "status": "ok",
                        "detail": f"ID: {container.short_id}"}
        except Exception as e:
            steps[7] = {"label": steps[7]["label"], "status": "error", "detail": str(e)[:80]}
            upd(); hide_docker_desktop(); focus_chrome(driver); go_to_status(driver); raise

        upd(8)
        time.sleep(3)           # user sees running container in Docker Desktop
        hide_docker_desktop()
        focus_chrome(driver)
        go_to_status(driver, 8)

        # ── Verify output ────────────────────────────────────
        print("[INFO] ═══ STEP: Verify output ═══")
        upd(8)
        expected = "Hello from Docker!"
        ok = expected in output
        steps[8] = {
            "label": steps[8]["label"],
            "status": "ok" if ok else "error",
            "detail": f'Output: "{output}"' if ok else f'Unexpected: "{output}"',
        }
        upd(9); go_to_status(driver, 9)

        print("[INFO] Pausing 3s before cleanup ...")
        time.sleep(3)

        # ── REMOVE CONTAINER — show Docker Desktop ───────────
        print("[INFO] ═══ STEP: Remove container ═══")
        upd(9)

        # Show Docker Desktop so user watches container disappear
        show_docker_desktop()
        time.sleep(1)
        steps[9] = {"label": steps[9]["label"], "status": "ok",
                    "detail": remove_container(client)}
        upd(10)
        time.sleep(3)           # user sees container gone

        # ── REMOVE IMAGE — still in Docker Desktop ───────────
        print("[INFO] ═══ STEP: Remove image ═══")
        upd(10)
        steps[10] = {"label": steps[10]["label"], "status": "ok",
                     "detail": remove_image(client)}
        upd(11)
        time.sleep(3)           # user sees image gone

        hide_docker_desktop()
        focus_chrome(driver)
        go_to_status(driver, 11)

        # ── All done ─────────────────────────────────────────
        steps[11] = {"label": steps[11]["label"], "status": "ok",
                     "detail": "Image & container fully purged"}
        upd(); go_to_status(driver, 11)

        print("\n[INFO] ✅ All done! Browser closes in 10 seconds ...")
        time.sleep(10)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted.")

    except Exception as exc:
        print(f"[FATAL] {exc}")
        if client:
            remove_container(client)
            remove_image(client)
        time.sleep(5)

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        server.shutdown()
        print("[INFO] Script finished.")


if __name__ == "__main__":
    main()