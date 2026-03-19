# 🐳 Docker Lifecycle Automation with Selenium

A Python automation script that performs a **complete Docker lifecycle** — build, run, verify, and auto-cleanup — while controlling Chrome and Docker Desktop windows so you can watch everything happen live on your screen.

---

## 📺 What You Will See

When you run the script, your screen does the following automatically:

| Moment | What Happens on Screen |
|---|---|
| Script starts | **Docker Desktop** opens and maximizes |
| 3 seconds later | **Chrome** opens with 4 maximized tabs |
| Tab 2 | Chrome browses **Docker Hub**, searches `python alpine` |
| Tab 3 | Chrome scrolls through **Docker Build docs**, highlights code |
| Image building | **Docker Desktop** pops to front — watch the image appear |
| Tab 4 | Chrome scrolls through **Docker Run docs**, highlights code |
| Container running | **Docker Desktop** pops to front — watch the container appear |
| Cleanup | **Docker Desktop** pops to front — watch container & image disappear |
| End | Chrome returns to the **live status dashboard**, all steps green ✅ |

---

## 🗂️ Chrome Tabs

| Tab | URL | Purpose |
|---|---|---|
| **Tab 1** | `http://127.0.0.1:8787` | Live status dashboard with progress bar — auto-scrolls to the active step |
| **Tab 2** | `hub.docker.com/_/python` | Browses Docker Hub, searches for `python alpine` |
| **Tab 3** | `docs.docker.com/.../image/build/` | Scrolls Docker Build reference docs |
| **Tab 4** | `docs.docker.com/.../container/run/` | Scrolls Docker Run reference docs |

---

## ⚙️ Automation Steps

The status dashboard tracks **12 steps** in real time:

```
 1. Docker Desktop launched
 2. Docker daemon reachable
 3. Dockerfile written
 4. Browsed Docker Hub (Tab 2)
 5. Browsed Docker Build docs (Tab 3)
 6. Docker image built              ← Docker Desktop shown
 7. Browsed Docker Run docs (Tab 4)
 8. Container started & run         ← Docker Desktop shown
 9. Container output verified
10. Container removed               ← Docker Desktop shown
11. Image removed                   ← Docker Desktop shown
12. All cleanup complete
```

Each step shows a **live status icon**, a **detail message**, and the progress bar updates automatically.

---

## 🐋 Docker Resources Created

| Resource | Name |
|---|---|
| **Image** | `selenium-demo-image:latest` |
| **Container** | `selenium-demo-container` |
| **Base image** | `python:3.11-alpine` |

The Dockerfile used is minimal:

```dockerfile
FROM python:3.11-alpine
LABEL maintainer="selenium-demo"
RUN echo "Hello from Docker!" > /message.txt
CMD ["cat", "/message.txt"]
```

Both the container and image are **automatically deleted** at the end of the run. Cleanup also runs on errors, so no leftover resources are ever left behind.

---

## 🖥️ Requirements

### Software

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.10 or higher | Must be on PATH |
| **Google Chrome** | Any recent version | `webdriver-manager` auto-downloads the matching ChromeDriver |
| **Docker Desktop** | Any recent version | Must be installed at the default path (see below) |
| **Windows** | 10 or 11 | Window focus control uses the Windows API (`ctypes`) |

### Python Packages

```
selenium>=4.18.0
webdriver-manager>=4.0.1
docker>=7.0.0
```

---

## 🚀 Installation & Setup

### Step 1 — Clone or download the files

Place both files in the same folder, e.g. `D:\Devops Automation\`:

```
D:\Devops Automation\
├── docker_selenium_automation.py
└── requirements.txt
```

### Step 2 — Install Python dependencies

Open **Command Prompt** or **PowerShell** in that folder:

```cmd
cd "D:\Devops Automation"
pip install -r requirements.txt
```

### Step 3 — Make sure Docker Desktop is running

Open Docker Desktop and wait until it shows **"Engine running"** in the bottom-left corner.

### Step 4 — Run the script

```cmd
python docker_selenium_automation.py
```

Chrome will open automatically. Do not close it — the script controls it throughout the run.

---

## 📁 Docker Desktop Path Detection

The script searches for Docker Desktop in these locations automatically:

```
C:\Program Files\Docker\Docker\Docker Desktop.exe       ← default
C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe
%LOCALAPPDATA%\Docker\Docker Desktop.exe
```

If Docker Desktop is found at any of these paths, it will be launched and maximized automatically. If not found, the warning `Docker Desktop exe not found — open it manually` is printed and the script continues.

---

## 🪟 Window Behaviour

| Behaviour | How It Works |
|---|---|
| Chrome always **maximized** | `--start-maximized` flag + `driver.maximize_window()` after every tab switch |
| Docker Desktop **maximized** when shown | Win32 `SW_RESTORE` → `SW_MAXIMIZE` → `SetForegroundWindow` sequence |
| Chrome **returns to front** after Docker Desktop | Win32 `_find_hwnd("Google Chrome")` + `_bring_to_front_maximized()` |
| **No random resizing** | No background threads fighting over window focus |
| Status page **auto-scrolls** | `driver.execute_script(scrollIntoView)` targets the active step row after every update |

---

## 📊 Status Dashboard

The live status page at `http://127.0.0.1:8787` features:

- **Progress bar** — fills as steps complete
- **Step counter** — e.g. `4/12 steps done`
- **Active step highlighted** in blue with a glow border
- **Smooth auto-scroll** — the page scrolls to keep the currently running step in the centre of the viewport
- **Sticky table header** — column headers stay visible while scrolling
- **No auto-refresh** — Selenium drives all updates directly via `execute_script`, avoiding the scroll-reset bug that `<meta http-equiv="refresh">` causes

---

## 🛠️ Troubleshooting

### `Cannot connect to Docker daemon`
Docker Desktop is not running. Open it and wait for **"Engine running"** before retrying.

### `Docker Desktop exe not found`
Docker Desktop is not installed at a standard path. Open it manually before running the script. The automation will still work — it just won't launch Docker Desktop for you.

### `driver.get` connection errors on first run
`webdriver-manager` is downloading ChromeDriver. This only happens once; subsequent runs start immediately.

### Chrome minimizes randomly
This was a known bug in earlier versions caused by background threads competing for window focus. It is fully fixed in the current version — window control is single-threaded and sequential.

### Status page does not scroll to active step
Ensure you are on **Tab 1** (the status dashboard). The scroll is injected by Selenium after navigating to the page — it only works when Chrome is on that tab.

---

## 📦 Project Structure

```
D:\Devops Automation\
├── docker_selenium_automation.py    # Main script
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

**Inside the script, sections are organized as:**

| Section | Contents |
|---|---|
| `SECTION 1` | Windows API helpers — find, maximize, minimize any window by title |
| `SECTION 2` | Docker Desktop launcher — detect, launch, show, hide |
| `SECTION 3` | Chrome / Selenium helpers — driver init, tab control, safe navigation |
| `SECTION 4` | HTML status dashboard builder |
| `SECTION 5` | Local HTTP server (thread-safe, serves the dashboard) |
| `SECTION 6` | Docker SDK helpers — build, run, remove container and image |
| `SECTION 7` | Browser browsing helpers — scroll, highlight, search Docker Hub |
| `SECTION 8` | Main orchestration — the full 12-step lifecycle |

---

## 🔧 Configuration

You can change these constants at the top of the script:

```python
IMAGE_NAME     = "selenium-demo-image"    # Docker image name
IMAGE_TAG      = "latest"                 # Docker image tag
CONTAINER_NAME = "selenium-demo-container"# Container name
STATUS_PORT    = 8787                     # Local dashboard port
```

To change the **Dockerfile content**, edit the `DOCKERFILE_CONTENT` string in the CONFIG section.

---

## ✅ Verified Working On

- Windows 10 / Windows 11
- Python 3.12
- Google Chrome (latest)
- Docker Desktop 4.x
- Selenium 4.x + webdriver-manager 4.x

---

## 📝 License

This project is for **educational and demo purposes**. Feel free to modify and extend it for your own DevOps automation workflows.
