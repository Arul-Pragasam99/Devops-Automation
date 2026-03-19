"""
Docker Lifecycle Automation with Selenium
==========================================
What you will SEE in Chrome:
  Tab 1 - Live status dashboard (auto-refreshes each step)
  Tab 2 - Docker Hub (python:3.11-alpine image page)
  Tab 3 - Docker Docs (run reference)
  Tab 4 - Docker Desktop localhost UI (if running)

Chrome switches between tabs at each stage so you can
watch real browsing activity during the Docker lifecycle.
"""

import time
import os
import sys
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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
IMAGE_NAME     = "selenium-demo-image"
IMAGE_TAG      = "latest"
CONTAINER_NAME = "selenium-demo-container"
FULL_IMAGE     = f"{IMAGE_NAME}:{IMAGE_TAG}"
STATUS_PORT    = 8787

# Real URLs Selenium will browse during processing
DOCKERHUB_URL     = "https://hub.docker.com/_/python"
DOCKER_RUN_URL    = "https://docs.docker.com/reference/cli/docker/container/run/"
DOCKER_BUILD_URL  = "https://docs.docker.com/reference/cli/docker/image/build/"
DOCKER_LOCAL_URL  = "http://localhost/"   # Docker Desktop dashboard (may 404 — handled)

DOCKERFILE_CONTENT = """\
FROM python:3.11-alpine
LABEL maintainer="selenium-demo"
RUN echo "Hello from Docker!" > /message.txt
CMD ["cat", "/message.txt"]
"""

# ─────────────────────────────────────────────────────────────
# HTML Status Dashboard
# ─────────────────────────────────────────────────────────────
def build_status_html(steps, current_step=-1):
    rows = ""
    for i, s in enumerate(steps):
        if s["status"] == "ok":
            icon, color = "✅", "#22c55e"
        elif s["status"] == "error":
            icon, color = "❌", "#ef4444"
        elif i == current_step:
            icon, color = "🔄", "#38bdf8"
        else:
            icon, color = "⏳", "#f59e0b"

        highlight = "background:rgba(56,189,248,0.07);border-left:3px solid #38bdf8;" if i == current_step else ""
        rows += f"""
        <tr style="{highlight}">
          <td style="padding:12px 16px;font-size:1.1rem;">{icon}</td>
          <td style="padding:12px 16px;color:#e2e8f0;font-weight:600;">{s['label']}</td>
          <td style="padding:12px 16px;color:{color};font-family:monospace;font-size:.85rem;">{s.get('detail','')}</td>
        </tr>"""

    progress = sum(1 for s in steps if s["status"] == "ok")
    pct = int(progress / len(steps) * 100)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta http-equiv="refresh" content="2"/>
  <title>Docker Automation — Live Status</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0f172a;font-family:'JetBrains Mono',monospace;min-height:100vh;
         display:flex;align-items:center;justify-content:center;padding:2rem}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:16px;
           max-width:900px;width:100%;padding:2.5rem;
           box-shadow:0 25px 60px rgba(0,0,0,.6)}}
    h1{{font-family:'Syne',sans-serif;font-size:2rem;color:#38bdf8;
        letter-spacing:-.5px;margin-bottom:.3rem}}
    .sub{{color:#64748b;font-size:.82rem;margin-bottom:1.5rem}}
    .progress-wrap{{background:#334155;border-radius:99px;height:8px;margin-bottom:2rem;overflow:hidden}}
    .progress-bar{{background:linear-gradient(90deg,#0ea5e9,#38bdf8);height:100%;
                  border-radius:99px;transition:width .4s ease;width:{pct}%}}
    .pct{{color:#94a3b8;font-size:.78rem;text-align:right;margin-top:.3rem;margin-bottom:1.5rem}}
    table{{width:100%;border-collapse:collapse}}
    tr{{border-bottom:1px solid #1e293b}}
    th{{color:#475569;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;
        padding:8px 16px;text-align:left;border-bottom:1px solid #334155}}
    .badge{{display:inline-block;background:#0ea5e9;color:#fff;
            border-radius:999px;padding:2px 12px;font-size:.72rem;margin-left:10px;
            vertical-align:middle}}
    .pulse{{animation:pulse 1.5s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
  </style>
</head>
<body>
<div class="card">
  <h1>🐳 Docker Automation <span class="badge">Selenium Live</span></h1>
  <p class="sub">Build → Run → Verify → Auto Cleanup &nbsp;|&nbsp; Tab 1 of 4</p>
  <div class="progress-wrap"><div class="progress-bar"></div></div>
  <p class="pct">{pct}% complete</p>
  <table>
    <thead><tr><th>Status</th><th>Step</th><th>Detail</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# Thread-safe HTTP server
# ─────────────────────────────────────────────────────────────
_lock = threading.Lock()
_html_bytes = b""

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with _lock:
            body = _html_bytes
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

def update_page(steps, current=-1):
    global _html_bytes
    with _lock:
        _html_bytes = build_status_html(steps, current).encode("utf-8")

def start_server():
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", STATUS_PORT), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def wait_for_server(port, timeout=12):
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


# ─────────────────────────────────────────────────────────────
# Docker helpers
# ─────────────────────────────────────────────────────────────
def get_docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as e:
        print(f"[ERROR] Docker not reachable: {e}")
        sys.exit(1)

def build_image(client, build_dir):
    print(f"[DOCKER] Building {FULL_IMAGE} ...")
    image, logs = client.images.build(path=build_dir, tag=FULL_IMAGE, rm=True)
    for chunk in logs:
        line = chunk.get("stream", "").strip()
        if line: print(f"         {line}")
    return image

def run_container(client):
    print(f"[DOCKER] Running container {CONTAINER_NAME} ...")
    c = client.containers.run(FULL_IMAGE, name=CONTAINER_NAME, detach=True, remove=False)
    c.wait()
    out = c.logs().decode().strip()
    print(f"[DOCKER] Output: {out}")
    return c, out

def remove_container(client):
    try:
        client.containers.get(CONTAINER_NAME).remove(force=True)
        print(f"[DOCKER] Container '{CONTAINER_NAME}' removed.")
        return f"'{CONTAINER_NAME}' deleted"
    except docker.errors.NotFound:
        return "Already removed"

def remove_image(client):
    try:
        client.images.remove(FULL_IMAGE, force=True)
        print(f"[DOCKER] Image '{FULL_IMAGE}' removed.")
        return f"'{FULL_IMAGE}' deleted"
    except docker.errors.ImageNotFound:
        return "Already removed"


# ─────────────────────────────────────────────────────────────
# Selenium helpers
# ─────────────────────────────────────────────────────────────
def init_driver():
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-sync")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--metrics-recording-only")
    opts.add_argument("--start-maximized")          # maximize on launch
    # NOTE: do NOT add --window-size here — it overrides --start-maximized
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    svc = Service(ChromeDriverManager().install())
    svc.creation_flags = 0x08000000
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.maximize_window()                        # force maximize via API too
    return driver

def safe_get(driver, url, retries=4, delay=1.2):
    for i in range(retries):
        try:
            driver.get(url)
            return
        except Exception as e:
            if i < retries - 1:
                print(f"[WARN] get({url}) failed attempt {i+1}: {e}")
                time.sleep(delay)
            else:
                raise

def open_new_tab(driver, url):
    """Open URL in a new tab, switch to it, and maximize."""
    driver.execute_script("window.open(arguments[0], '_blank');", url)
    driver.switch_to.window(driver.window_handles[-1])
    driver.maximize_window()   # ensure every new tab is also maximized
    time.sleep(1.5)

def switch_tab(driver, index):
    """Switch to tab by index (0-based) and maximize."""
    handles = driver.window_handles
    if index < len(handles):
        driver.switch_to.window(handles[index])
        driver.maximize_window()
        time.sleep(0.5)

def scroll_page(driver, times=3, pause=0.6):
    """Scroll down the current page naturally."""
    for _ in range(times):
        driver.execute_script("window.scrollBy(0, 350);")
        time.sleep(pause)

def highlight_element(driver, element):
    """Flash-highlight a web element so it's visible."""
    driver.execute_script(
        "arguments[0].style.border='3px solid #38bdf8';"
        "arguments[0].style.boxShadow='0 0 12px #38bdf8';", element)
    time.sleep(0.4)

def try_click(driver, by, value, timeout=6):
    """Try to find and click an element — no crash if not found."""
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((by, value)))
        highlight_element(driver, el)
        el.click()
        return True
    except Exception:
        return False

def browse_dockerhub(driver):
    """Tab 2: Browse Docker Hub — search for the base image."""
    print("[BROWSER] Browsing Docker Hub ...")
    switch_tab(driver, 1)
    scroll_page(driver, times=4, pause=0.8)

    # Try to interact with the search bar on Docker Hub
    try:
        search = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='search'], input[placeholder*='earch']"))
        )
        highlight_element(driver, search)
        search.clear()
        search.send_keys("python alpine")
        time.sleep(1.2)
        search.send_keys(Keys.RETURN)
        time.sleep(2.5)
        scroll_page(driver, times=2, pause=0.7)
    except Exception:
        scroll_page(driver, times=3, pause=0.7)

def browse_docker_build_docs(driver):
    """Tab 3: Browse Docker Build docs page."""
    print("[BROWSER] Browsing Docker Build docs ...")
    switch_tab(driver, 2)
    time.sleep(1.5)
    scroll_page(driver, times=5, pause=0.7)

    # Highlight any code blocks on the page
    try:
        codes = driver.find_elements(By.CSS_SELECTOR, "code, pre")
        for el in codes[:3]:
            highlight_element(driver, el)
            time.sleep(0.3)
    except Exception:
        pass

def browse_docker_run_docs(driver):
    """Tab 4: Browse Docker Run reference docs."""
    print("[BROWSER] Browsing Docker Run docs ...")
    switch_tab(driver, 3)
    time.sleep(1.5)
    scroll_page(driver, times=5, pause=0.7)
    try:
        codes = driver.find_elements(By.CSS_SELECTOR, "code, pre")
        for el in codes[:3]:
            highlight_element(driver, el)
            time.sleep(0.3)
    except Exception:
        pass

def go_to_status(driver):
    switch_tab(driver, 0)
    try:
        driver.refresh()
    except Exception:
        pass
    time.sleep(0.8)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    status_url = f"http://127.0.0.1:{STATUS_PORT}/"

    steps = [
        {"label": "Docker daemon reachable",       "status": "pending", "detail": ""},
        {"label": "Dockerfile written",             "status": "pending", "detail": ""},
        {"label": "Browsing Docker Hub (Tab 2)",    "status": "pending", "detail": ""},
        {"label": "Browsing Docker Build docs (Tab 3)", "status": "pending", "detail": ""},
        {"label": "Docker image built",             "status": "pending", "detail": ""},
        {"label": "Browsing Docker Run docs (Tab 4)", "status": "pending", "detail": ""},
        {"label": "Container started & run",        "status": "pending", "detail": ""},
        {"label": "Container output verified",      "status": "pending", "detail": ""},
        {"label": "Container removed",              "status": "pending", "detail": ""},
        {"label": "Image removed",                  "status": "pending", "detail": ""},
        {"label": "All cleanup complete",           "status": "pending", "detail": ""},
    ]

    # ── Start local server ───────────────────────────────────
    update_page(steps, 0)
    server = start_server()
    print(f"[INFO] Waiting for status server on port {STATUS_PORT} ...")
    if not wait_for_server(STATUS_PORT):
        print("[ERROR] Server did not start. Exiting.")
        sys.exit(1)
    print(f"[INFO] Server ready → {status_url}")

    # ── Launch Chrome ────────────────────────────────────────
    print("[INFO] Launching Chrome ...")
    driver = init_driver()

    # Tab 1 — Status dashboard
    safe_get(driver, status_url)
    time.sleep(1)

    # Tab 2 — Docker Hub
    open_new_tab(driver, DOCKERHUB_URL)

    # Tab 3 — Docker Build docs
    open_new_tab(driver, DOCKER_BUILD_URL)

    # Tab 4 — Docker Run docs
    open_new_tab(driver, DOCKER_RUN_URL)

    # Return to status tab
    go_to_status(driver)
    print("[INFO] All tabs opened. Starting Docker automation ...\n")

    client = None
    try:
        # ── Step 0: Docker daemon ────────────────────────────
        update_page(steps, 0)
        client = get_docker_client()
        steps[0] = {"label": steps[0]["label"], "status": "ok", "detail": "Connected ✓"}
        update_page(steps, 1); go_to_status(driver)

        # ── Step 1: Write Dockerfile ─────────────────────────
        build_dir = tempfile.mkdtemp(prefix="sel_docker_")
        df_path = os.path.join(build_dir, "Dockerfile")
        with open(df_path, "w") as f:
            f.write(DOCKERFILE_CONTENT)
        steps[1] = {"label": steps[1]["label"], "status": "ok", "detail": df_path}
        update_page(steps, 2); go_to_status(driver)
        time.sleep(0.8)

        # ── Step 2: Browse Docker Hub while daemon starts ────
        update_page(steps, 2)
        browse_dockerhub(driver)
        steps[2] = {"label": steps[2]["label"], "status": "ok",
                    "detail": "Searched 'python alpine' on Docker Hub"}
        update_page(steps); go_to_status(driver)

        # ── Step 3: Browse Build docs ────────────────────────
        update_page(steps, 3)
        browse_docker_build_docs(driver)
        steps[3] = {"label": steps[3]["label"], "status": "ok",
                    "detail": "Scrolled Docker build reference"}
        update_page(steps); go_to_status(driver)

        # ── Step 4: Build image (back on status tab) ─────────
        update_page(steps, 4)
        go_to_status(driver)
        print("[DOCKER] Building image — watch the terminal ...")
        try:
            build_image(client, build_dir)
            steps[4] = {"label": steps[4]["label"], "status": "ok", "detail": FULL_IMAGE}
        except Exception as e:
            steps[4] = {"label": steps[4]["label"], "status": "error", "detail": str(e)[:80]}
            update_page(steps); go_to_status(driver); raise
        update_page(steps, 5); go_to_status(driver)

        # ── Step 5: Browse Run docs while image settles ──────
        update_page(steps, 5)
        browse_docker_run_docs(driver)
        steps[5] = {"label": steps[5]["label"], "status": "ok",
                    "detail": "Scrolled Docker run reference"}
        update_page(steps); go_to_status(driver)

        # ── Step 6: Run container ────────────────────────────
        update_page(steps, 6)
        go_to_status(driver)
        try:
            container, output = run_container(client)
            steps[6] = {"label": steps[6]["label"], "status": "ok",
                        "detail": f"Container ID: {container.short_id}"}
        except Exception as e:
            steps[6] = {"label": steps[6]["label"], "status": "error", "detail": str(e)[:80]}
            update_page(steps); go_to_status(driver); raise
        update_page(steps, 7); go_to_status(driver)

        # ── Step 7: Verify output ────────────────────────────
        expected = "Hello from Docker!"
        ok = expected in output
        steps[7] = {
            "label": steps[7]["label"],
            "status": "ok" if ok else "error",
            "detail": f'Output: "{output}"' if ok else f'Unexpected: "{output}"'
        }
        update_page(steps, 8); go_to_status(driver)

        # Let user see the "all running" state for a moment
        print("[INFO] Pausing 4 seconds — review the running state ...")
        time.sleep(4)

        # ── Step 8: Remove container ─────────────────────────
        update_page(steps, 8)
        steps[8] = {"label": steps[8]["label"], "status": "ok",
                    "detail": remove_container(client)}
        update_page(steps, 9); go_to_status(driver)

        # ── Step 9: Remove image ─────────────────────────────
        update_page(steps, 9)
        steps[9] = {"label": steps[9]["label"], "status": "ok",
                    "detail": remove_image(client)}
        update_page(steps, 10); go_to_status(driver)

        # ── Step 10: All done ────────────────────────────────
        steps[10] = {"label": steps[10]["label"], "status": "ok",
                     "detail": "Image & container fully purged 🎉"}
        update_page(steps); go_to_status(driver)

        print("\n[INFO] ✅ All done! Browser closes in 10 seconds ...")
        time.sleep(10)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")

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