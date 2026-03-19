"""
Docker Lifecycle Automation with Selenium
==========================================
This script:
1. Builds a sample Docker image
2. Runs a container from it
3. Opens a local status page via Selenium (Chrome) to verify/display steps
4. Automatically stops and removes the container and image
5. Shows a final cleanup confirmation

Requirements:
    pip install selenium webdriver-manager docker
    Docker Desktop / Docker Engine must be running.
"""

import time
import subprocess
import os
import sys
import tempfile
import threading
import http.server
import socketserver
import docker
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
IMAGE_NAME = "selenium-demo-image"
IMAGE_TAG = "latest"
CONTAINER_NAME = "selenium-demo-container"
FULL_IMAGE = f"{IMAGE_NAME}:{IMAGE_TAG}"
STATUS_PORT = 8787  # local HTTP server port for the status page

# ─────────────────────────────────────────────
# Step 1 – Build a minimal Dockerfile in memory
# ─────────────────────────────────────────────
DOCKERFILE_CONTENT = """\
FROM python:3.11-alpine
LABEL maintainer="selenium-demo"
RUN echo "Hello from Docker!" > /message.txt
CMD ["cat", "/message.txt"]
"""

# ─────────────────────────────────────────────
# HTML Status Page (served locally)
# ─────────────────────────────────────────────
def build_status_html(steps: list[dict]) -> str:
    rows = ""
    for s in steps:
        icon = "✅" if s["status"] == "ok" else ("⏳" if s["status"] == "pending" else "❌")
        color = "#22c55e" if s["status"] == "ok" else ("#f59e0b" if s["status"] == "pending" else "#ef4444")
        rows += f"""
        <tr>
          <td style="padding:12px 16px;font-size:1.1rem;">{icon}</td>
          <td style="padding:12px 16px;color:#e2e8f0;font-weight:500;">{s['label']}</td>
          <td style="padding:12px 16px;color:{color};font-family:monospace;font-size:0.9rem;">{s.get('detail','')}</td>
        </tr>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>Docker Automation Status</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0f172a;font-family:'JetBrains Mono',monospace;min-height:100vh;
         display:flex;align-items:center;justify-content:center;padding:2rem}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:16px;
           max-width:820px;width:100%;padding:2.5rem;box-shadow:0 25px 60px rgba(0,0,0,.5)}}
    h1{{font-family:'Syne',sans-serif;font-size:2rem;color:#38bdf8;letter-spacing:-0.5px;margin-bottom:.4rem}}
    .sub{{color:#64748b;font-size:.85rem;margin-bottom:2rem}}
    table{{width:100%;border-collapse:collapse}}
    tr{{border-bottom:1px solid #334155}}
    tr:last-child{{border-bottom:none}}
    .badge{{display:inline-block;background:#0ea5e9;color:#fff;
            border-radius:999px;padding:2px 12px;font-size:.75rem;margin-left:10px}}
  </style>
</head>
<body>
<div class="card">
  <h1>🐳 Docker Automation <span class="badge">Selenium</span></h1>
  <p class="sub">Automated lifecycle: Build → Run → Verify → Cleanup</p>
  <table>
    <thead>
      <tr style="color:#64748b;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em">
        <th style="padding:8px 16px;text-align:left">Status</th>
        <th style="padding:8px 16px;text-align:left">Step</th>
        <th style="padding:8px 16px;text-align:left">Detail</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>
</body>
</html>"""


# ─────────────────────────────────────────────
# Tiny local HTTP server
# ─────────────────────────────────────────────
_html_content = ""

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = _html_content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence access logs
        pass

def start_local_server():
    server = socketserver.TCPServer(("", STATUS_PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def update_page(steps):
    global _html_content
    _html_content = build_status_html(steps)


# ─────────────────────────────────────────────
# Docker helpers
# ─────────────────────────────────────────────
def docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as e:
        print(f"[ERROR] Cannot connect to Docker daemon: {e}")
        sys.exit(1)


def build_image(client, build_dir):
    print(f"[INFO] Building image '{FULL_IMAGE}' ...")
    image, logs = client.images.build(path=build_dir, tag=FULL_IMAGE, rm=True)
    for chunk in logs:
        if "stream" in chunk:
            print("   ", chunk["stream"].strip())
    return image


def run_container(client):
    print(f"[INFO] Running container '{CONTAINER_NAME}' ...")
    container = client.containers.run(
        FULL_IMAGE,
        name=CONTAINER_NAME,
        detach=True,
        remove=False,
    )
    container.wait()   # wait for CMD to finish
    logs = container.logs().decode().strip()
    print(f"[INFO] Container output: {logs}")
    return container, logs


def cleanup(client):
    print("[INFO] Starting cleanup ...")
    # Remove container
    try:
        c = client.containers.get(CONTAINER_NAME)
        c.remove(force=True)
        print(f"[INFO] Container '{CONTAINER_NAME}' removed.")
    except docker.errors.NotFound:
        print(f"[WARN] Container '{CONTAINER_NAME}' not found (already gone).")

    # Remove image
    try:
        client.images.remove(FULL_IMAGE, force=True)
        print(f"[INFO] Image '{FULL_IMAGE}' removed.")
    except docker.errors.ImageNotFound:
        print(f"[WARN] Image '{FULL_IMAGE}' not found (already gone).")


# ─────────────────────────────────────────────
# Selenium browser driver
# ─────────────────────────────────────────────
def init_driver(headless: bool = False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


# ─────────────────────────────────────────────
# Main orchestration
# ─────────────────────────────────────────────
def main():
    headless = "--headless" in sys.argv

    steps = [
        {"label": "Docker daemon reachable",  "status": "pending", "detail": ""},
        {"label": "Dockerfile created",        "status": "pending", "detail": ""},
        {"label": "Image built",               "status": "pending", "detail": ""},
        {"label": "Container started & run",   "status": "pending", "detail": ""},
        {"label": "Container output verified", "status": "pending", "detail": ""},
        {"label": "Container removed",         "status": "pending", "detail": ""},
        {"label": "Image removed",             "status": "pending", "detail": ""},
        {"label": "All cleanup complete",      "status": "pending", "detail": ""},
    ]

    # Start local HTTP status server
    update_page(steps)
    server = start_local_server()
    url = f"http://localhost:{STATUS_PORT}"
    print(f"[INFO] Status page available at {url}")

    # Launch Selenium
    driver = init_driver(headless=headless)
    driver.get(url)
    wait = WebDriverWait(driver, 30)

    def refresh_browser():
        driver.refresh()
        time.sleep(0.6)

    try:
        # ── Step 0: Docker daemon ──────────────────
        client = docker_client()
        steps[0] = {"label": steps[0]["label"], "status": "ok", "detail": "Connected ✓"}
        update_page(steps); refresh_browser()

        # ── Step 1: Write Dockerfile ───────────────
        build_dir = tempfile.mkdtemp(prefix="selenium_docker_")
        df_path = os.path.join(build_dir, "Dockerfile")
        with open(df_path, "w") as f:
            f.write(DOCKERFILE_CONTENT)
        steps[1] = {"label": steps[1]["label"], "status": "ok", "detail": df_path}
        update_page(steps); refresh_browser()

        # ── Step 2: Build image ────────────────────
        try:
            build_image(client, build_dir)
            steps[2] = {"label": steps[2]["label"], "status": "ok", "detail": FULL_IMAGE}
        except Exception as e:
            steps[2] = {"label": steps[2]["label"], "status": "error", "detail": str(e)}
            update_page(steps); refresh_browser()
            raise
        update_page(steps); refresh_browser()

        # ── Step 3: Run container ──────────────────
        try:
            container, output = run_container(client)
            steps[3] = {"label": steps[3]["label"], "status": "ok", "detail": f"ID: {container.short_id}"}
        except Exception as e:
            steps[3] = {"label": steps[3]["label"], "status": "error", "detail": str(e)}
            update_page(steps); refresh_browser()
            raise
        update_page(steps); refresh_browser()

        # ── Step 4: Verify output ──────────────────
        expected = "Hello from Docker!"
        if expected in output:
            steps[4] = {"label": steps[4]["label"], "status": "ok", "detail": f'Got: "{output}"'}
        else:
            steps[4] = {"label": steps[4]["label"], "status": "error", "detail": f'Unexpected: "{output}"'}
        update_page(steps); refresh_browser()

        print("[INFO] Pausing 4s so you can see the running state ...")
        time.sleep(4)

        # ── Step 5 & 6: Cleanup ────────────────────
        try:
            c = client.containers.get(CONTAINER_NAME)
            c.remove(force=True)
            steps[5] = {"label": steps[5]["label"], "status": "ok", "detail": f"'{CONTAINER_NAME}' deleted"}
        except docker.errors.NotFound:
            steps[5] = {"label": steps[5]["label"], "status": "ok", "detail": "Already removed"}
        update_page(steps); refresh_browser()

        try:
            client.images.remove(FULL_IMAGE, force=True)
            steps[6] = {"label": steps[6]["label"], "status": "ok", "detail": f"'{FULL_IMAGE}' deleted"}
        except docker.errors.ImageNotFound:
            steps[6] = {"label": steps[6]["label"], "status": "ok", "detail": "Already removed"}
        update_page(steps); refresh_browser()

        # ── Step 7: Done ───────────────────────────
        steps[7] = {"label": steps[7]["label"], "status": "ok",
                    "detail": "Image & container fully purged 🎉"}
        update_page(steps); refresh_browser()

        print("[INFO] All done! Browser will stay open for 8 seconds.")
        time.sleep(8)

    except Exception as exc:
        print(f"[FATAL] {exc}")
        # Attempt best-effort cleanup even on error
        try:
            cleanup(client)
        except Exception:
            pass
        time.sleep(6)

    finally:
        driver.quit()
        server.shutdown()
        print("[INFO] Script finished.")


if __name__ == "__main__":
    main()