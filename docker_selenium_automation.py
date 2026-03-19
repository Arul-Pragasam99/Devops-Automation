"""
Docker Lifecycle Automation with Selenium
==========================================
Fixes applied:
  - Wait for local HTTP server to be ready before driver.get()
  - Suppress Chrome GCM / TensorFlow Lite noise
  - Retry logic for driver.get()
  - Cleaner thread-safe HTML updates
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
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
IMAGE_NAME      = "selenium-demo-image"
IMAGE_TAG       = "latest"
CONTAINER_NAME  = "selenium-demo-container"
FULL_IMAGE      = f"{IMAGE_NAME}:{IMAGE_TAG}"
STATUS_PORT     = 8787

DOCKERFILE_CONTENT = """\
FROM python:3.11-alpine
LABEL maintainer="selenium-demo"
RUN echo "Hello from Docker!" > /message.txt
CMD ["cat", "/message.txt"]
"""

# ─────────────────────────────────────────────
# HTML Status Page
# ─────────────────────────────────────────────
def build_status_html(steps):
    rows = ""
    for s in steps:
        icon  = "✅" if s["status"] == "ok" else ("⏳" if s["status"] == "pending" else "❌")
        color = "#22c55e" if s["status"] == "ok" else ("#f59e0b" if s["status"] == "pending" else "#ef4444")
        rows += f"""
        <tr>
          <td style="padding:12px 16px;font-size:1.1rem;">{icon}</td>
          <td style="padding:12px 16px;color:#e2e8f0;font-weight:500;">{s['label']}</td>
          <td style="padding:12px 16px;color:{color};font-family:monospace;font-size:.9rem;">{s.get('detail','')}</td>
        </tr>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta http-equiv="refresh" content="2"/>
  <title>Docker Automation Status</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0f172a;font-family:'JetBrains Mono',monospace;min-height:100vh;
         display:flex;align-items:center;justify-content:center;padding:2rem}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:16px;
           max-width:860px;width:100%;padding:2.5rem;box-shadow:0 25px 60px rgba(0,0,0,.5)}}
    h1{{font-family:'Syne',sans-serif;font-size:2rem;color:#38bdf8;letter-spacing:-.5px;margin-bottom:.4rem}}
    .sub{{color:#64748b;font-size:.85rem;margin-bottom:2rem}}
    table{{width:100%;border-collapse:collapse}}
    tr{{border-bottom:1px solid #334155}}
    tr:last-child{{border-bottom:none}}
    th{{color:#64748b;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;padding:8px 16px;text-align:left}}
    .badge{{display:inline-block;background:#0ea5e9;color:#fff;
            border-radius:999px;padding:2px 12px;font-size:.75rem;margin-left:10px}}
  </style>
</head>
<body>
<div class="card">
  <h1>Docker Automation <span class="badge">Selenium</span></h1>
  <p class="sub">Automated lifecycle: Build -&gt; Run -&gt; Verify -&gt; Auto Cleanup</p>
  <table>
    <thead><tr><th>Status</th><th>Step</th><th>Detail</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
</body>
</html>"""


# ─────────────────────────────────────────────
# Thread-safe local HTTP server
# ─────────────────────────────────────────────
_lock = threading.Lock()
_html_content = b""

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with _lock:
            body = _html_content
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence server logs


def update_page(steps):
    global _html_content
    with _lock:
        _html_content = build_status_html(steps).encode("utf-8")


def start_local_server():
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", STATUS_PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def wait_for_server(port, timeout=10):
    """Block until the local HTTP server responds."""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


# ─────────────────────────────────────────────
# Docker helpers
# ─────────────────────────────────────────────
def get_docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as e:
        print(f"[ERROR] Cannot connect to Docker daemon.")
        print(f"        Make sure Docker Desktop is running.")
        print(f"        Details: {e}")
        sys.exit(1)


def build_image(client, build_dir):
    print(f"[INFO] Building image '{FULL_IMAGE}' ...")
    image, logs = client.images.build(path=build_dir, tag=FULL_IMAGE, rm=True)
    for chunk in logs:
        if "stream" in chunk:
            line = chunk["stream"].strip()
            if line:
                print(f"   {line}")
    return image


def run_container(client):
    print(f"[INFO] Starting container '{CONTAINER_NAME}' ...")
    container = client.containers.run(
        FULL_IMAGE,
        name=CONTAINER_NAME,
        detach=True,
        remove=False,
    )
    container.wait()
    output = container.logs().decode().strip()
    print(f"[INFO] Container output: {output}")
    return container, output


def remove_container(client):
    try:
        c = client.containers.get(CONTAINER_NAME)
        c.remove(force=True)
        print(f"[INFO] Container '{CONTAINER_NAME}' removed.")
        return f"'{CONTAINER_NAME}' deleted"
    except docker.errors.NotFound:
        return "Already removed"


def remove_image(client):
    try:
        client.images.remove(FULL_IMAGE, force=True)
        print(f"[INFO] Image '{FULL_IMAGE}' removed.")
        return f"'{FULL_IMAGE}' deleted"
    except docker.errors.ImageNotFound:
        return "Already removed"


# ─────────────────────────────────────────────
# Selenium driver
# ─────────────────────────────────────────────
def init_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")

    # Suppress noisy Chrome / TensorFlow / GCM logs
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-sync")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--metrics-recording-only")
    opts.add_argument("--window-size=1280,900")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    service = Service(ChromeDriverManager().install())
    # Suppress ChromeDriver console window on Windows
    service.creation_flags = 0x08000000  # CREATE_NO_WINDOW

    return webdriver.Chrome(service=service, options=opts)


def safe_get(driver, url, retries=5, delay=1.0):
    """Navigate with retries in case of transient connection errors."""
    for attempt in range(retries):
        try:
            driver.get(url)
            return
        except Exception as e:
            if attempt < retries - 1:
                print(f"[WARN] driver.get failed (attempt {attempt+1}/{retries}), retrying... {e}")
                time.sleep(delay)
            else:
                raise


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    headless = "--headless" in sys.argv
    url = f"http://127.0.0.1:{STATUS_PORT}/"

    steps = [
        {"label": "Docker daemon reachable",  "status": "pending", "detail": ""},
        {"label": "Dockerfile created",        "status": "pending", "detail": ""},
        {"label": "Docker image built",        "status": "pending", "detail": ""},
        {"label": "Container started & run",   "status": "pending", "detail": ""},
        {"label": "Container output verified", "status": "pending", "detail": ""},
        {"label": "Container removed",         "status": "pending", "detail": ""},
        {"label": "Image removed",             "status": "pending", "detail": ""},
        {"label": "All cleanup complete",      "status": "pending", "detail": ""},
    ]

    # ── 1. Start HTTP server and wait until it is ready ──────────────
    update_page(steps)
    server = start_local_server()

    print(f"[INFO] Waiting for status server on port {STATUS_PORT} ...")
    if not wait_for_server(STATUS_PORT):
        print("[ERROR] Status server did not start. Exiting.")
        sys.exit(1)
    print(f"[INFO] Server ready at {url}")

    # ── 2. Launch browser AFTER server confirmed ready ────────────────
    print("[INFO] Launching Chrome ...")
    driver = init_driver(headless=headless)
    safe_get(driver, url)

    def refresh():
        try:
            driver.refresh()
            time.sleep(0.6)
        except Exception:
            pass

    client = None
    try:
        # Step 0 — Docker daemon
        client = get_docker_client()
        steps[0] = {"label": steps[0]["label"], "status": "ok", "detail": "Connected"}
        update_page(steps); refresh()

        # Step 1 — Write Dockerfile
        build_dir = tempfile.mkdtemp(prefix="sel_docker_")
        df_path = os.path.join(build_dir, "Dockerfile")
        with open(df_path, "w") as f:
            f.write(DOCKERFILE_CONTENT)
        steps[1] = {"label": steps[1]["label"], "status": "ok", "detail": df_path}
        update_page(steps); refresh()

        # Step 2 — Build image
        try:
            build_image(client, build_dir)
            steps[2] = {"label": steps[2]["label"], "status": "ok", "detail": FULL_IMAGE}
        except Exception as e:
            steps[2] = {"label": steps[2]["label"], "status": "error", "detail": str(e)[:80]}
            update_page(steps); refresh(); raise
        update_page(steps); refresh()

        # Step 3 — Run container
        try:
            container, output = run_container(client)
            steps[3] = {"label": steps[3]["label"], "status": "ok",
                        "detail": f"ID: {container.short_id}"}
        except Exception as e:
            steps[3] = {"label": steps[3]["label"], "status": "error", "detail": str(e)[:80]}
            update_page(steps); refresh(); raise
        update_page(steps); refresh()

        # Step 4 — Verify output
        expected = "Hello from Docker!"
        ok = expected in output
        steps[4] = {
            "label": steps[4]["label"],
            "status": "ok" if ok else "error",
            "detail": f'"{output}"' if ok else f'Unexpected: "{output}"'
        }
        update_page(steps); refresh()

        print("[INFO] Pausing 4 seconds so you can review the running state ...")
        time.sleep(4)

        # Step 5 — Remove container
        steps[5] = {"label": steps[5]["label"], "status": "ok",
                    "detail": remove_container(client)}
        update_page(steps); refresh()

        # Step 6 — Remove image
        steps[6] = {"label": steps[6]["label"], "status": "ok",
                    "detail": remove_image(client)}
        update_page(steps); refresh()

        # Step 7 — All done
        steps[7] = {"label": steps[7]["label"], "status": "ok",
                    "detail": "Image and container fully purged"}
        update_page(steps); refresh()

        print("[INFO] Complete! Browser will close in 8 seconds ...")
        time.sleep(8)

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