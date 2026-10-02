<div align="center">

# 🚢 Maritime Edge-NVR Alert System

**An edge-computing NVR alert system for ships — real-time person & boat detection, hardware-accelerated video transcoding, and a fault-tolerant store-and-forward pipeline that delivers compressed evidence to a shore server over VSAT.**

[![Frigate](https://img.shields.io/badge/Frigate-NVR-1E90FF?logo=blueprint&logoColor=white)](https://frigate.video)
[![OpenVINO](https://img.shields.io/badge/OpenVINO-Inference-0068B5?logo=intel&logoColor=white)](https://docs.openvino.ai)
[![Python](https://img.shields.io/badge/Python-3.x-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-VAAPI-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org)
[![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com)

</div>

---

## 📖 About

**Maritime Edge-NVR Alert System** is an onboard (*edge*) surveillance system designed for vessels
operating on unstable satellite connectivity. All heavy work — AI detection, recording, and video
transcoding — runs **locally on the ship**. Only a lightweight (~1 MB) compressed preview and its
metadata are transmitted to a shore server, dramatically cutting VSAT bandwidth usage.

The system pairs **Frigate NVR** (running in Docker) with a custom **Python orchestrator**
(`maritime_agent.py`) that turns detections into a durable, offline-tolerant upload queue. If the
link to shore drops, nothing is lost: evidence accumulates in a local SQLite queue and is flushed
automatically (FIFO) the moment connectivity returns.

> **Design goal:** operate autonomously and never lose an alert, even with intermittent power and
> connectivity at sea.

---

## ✨ Key Features

- **Edge AI detection** — Person & boat detection via **OpenVINO** (SSDLite MobileNetV2) on an
  Intel iGPU, at 640×360 @ 3 FPS to keep CPU load low.
- **Hardware video transcoding** — FFmpeg + **Intel VAAPI (H.264)** with automatic **libx264**
  software fallback; each alert is shrunk to a ~1 MB preview.
- **Store-and-forward queue** — Durable **SQLite** queue (WAL mode) guarantees no alert is lost
  while offline; uploads resume automatically.
- **Connection-aware uploader** — A `threading.Event` flag gates all network I/O, so detection,
  recording, and compression keep running even with no internet.
- **Anti-false-positive filtering** — Object size/score thresholds, critical-zone checks, and
  **loitering time** to ignore wave-induced jitter.
- **Evidence lifecycle management** — Paired HD-video + snapshot evidence with automatic
  precision pruning (200 GB → 150 GB) and recordings retention (3 days).
- **Multi-threaded** — Independent snapshot and event uploaders so one never blocks the other.
- **Low-latency live view** — go2rtc restreaming with WebRTC for browser playback.

---

## 🧰 Tech Stack

| Layer | Technology | Where it runs |
| :--- | :--- | :--- |
| NVR Engine | **Frigate NVR** (Docker) | Ship (Edge) |
| AI Detector | **OpenVINO** — SSDLite MobileNetV2 (300×300) | Ship (Intel iGPU) |
| Video Transcoder | **FFmpeg + Intel VAAPI** (H.264), libx264 fallback | Ship (Intel iGPU) |
| Agent Orchestrator | **Python 3** (`maritime_agent.py`) | Ship (Host OS) |
| Queue Database | **SQLite** (WAL mode, 10 GB cap) | Ship (Host OS) |
| RTSP Restreamer | **go2rtc** (built into Frigate) | Ship (Docker) |
| Container Runtime | **Docker / Docker Compose** | Ship |
| Shore Server | **REST API** (Node.js) | Shore |

### Edge Hardware Reference

| Item | Spec |
| :--- | :--- |
| CPU | Intel Core i7-6700 (4C/8T) |
| iGPU | Intel HD Graphics 530 (Skylake) |
| RAM | ≥ 8 GB |
| Storage | ≥ 500 GB (SSD/HDD) |
| OS | Linux (Ubuntu/Debian) |

---

## 🏗️ System Architecture

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                         SHIP  (EDGE NVR)                          │
 │                                                                  │
 │  ┌────────────┐   RTSP    ┌───────────────────────────────────┐  │
 │  │ IP Camera  │──────────▶│ Frigate NVR (Docker)              │  │
 │  │ (Hikvision)│           │ ├─ go2rtc  (RTSP restream)        │  │
 │  └────────────┘           │ ├─ OpenVINO AI detect (iGPU)      │  │
 │                           │ ├─ Recording (alerts/detections)  │  │
 │                           │ └─ REST API (:5000)               │  │
 │                           └────────────────┬──────────────────┘  │
 │                                            │ poll every 10 s      │
 │                           ┌────────────────▼──────────────────┐  │
 │                           │ Maritime Agent (Python)           │  │
 │                           │  1. detect new events             │  │
 │                           │  2. download HD clip              │  │
 │                           │  3. transcode VAAPI/libx264 →~1MB │  │
 │                           │  4. store HD evidence locally     │  │
 │                           │  5. enqueue preview → SQLite      │  │
 │                           └────────────────┬──────────────────┘  │
 │                                            │                     │
 │                           ┌────────────────▼──────────────────┐  │
 │                           │  SQLite queue.db (WAL)  +  FLAG    │  │
 │                           │  (threading.Event: True / False)  │  │
 │                           └───────┬────────────────┬──────────┘  │
 │                                   │                │             │
 │                         ┌─────────▼──┐   ┌─────────▼─────────┐   │
 │                         │ConnChecker │   │ 2 Uploader Threads │   │
 │                         │ (5 min)    │   │ snapshot + event   │   │
 │                         └────────────┘   └─────────┬─────────┘   │
 └────────────────────────────────────────────────────┼─────────────┘
                                                      │  VSAT / Internet
                                                      │  (limited bandwidth)
 ┌────────────────────────────────────────────────────▼─────────────┐
 │                          SHORE SERVER                            │
 │   REST API (Node.js)                                             │
 │   POST /api/v1/event     ← ~1 MB alert video + metadata          │
 │   POST /api/v1/snapshot  ← JPEG snapshot + metadata              │
 │   Auth: Bearer <API_KEY>                                         │
 └──────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ How It Works

### 1. Detection — Frigate + OpenVINO
Frigate restreams each camera through **go2rtc** (one connection to the camera, shared by recording,
detection, and live view). The **substream** (640×360) feeds the OpenVINO detector on the iGPU,
tracking only `person` and `boat` with per-object area and confidence filters.

### 2. Filtering & Anti-Roll
Filters run mostly inside Frigate (`config.yml`): `min_area` / `max_area`, `min_score` / `threshold`,
and a **critical zone** with `loitering_time: 3`. A boat must remain inside the zone for at least
3 seconds to count as an event — this suppresses false positives caused by sea swell. The agent adds
a final label check (only `person` / `boat`).

### 3. Video Transcoding — FFmpeg + VAAPI
Each confirmed event's HD clip is transcoded to a lightweight preview:

| Parameter | Value |
| :--- | :--- |
| Target size | ~1 MB |
| Resolution / FPS | 640 px wide / 15 FPS |
| Bitrate | 500 kbps |
| Codec | H.264 via **VAAPI**, fallback **libx264** |
| Audio | Removed (`-an`) |
| Max duration | 30 s |
| Optimization | `+faststart` |

Compression retries are **local and unbounded**: 5 rounds (VAAPI → libx264, 5 s apart); if all fail,
wait 3 minutes and start a new cycle. The **raw HD video is never sent** to shore.

### 4. Evidence Storage & Lifecycle
- **HD video + snapshot** are kept locally under `evidence/YYYY-MM-DD/` (max **200 GB**), pruned
  in FIFO pairs (HD + JPG) down to **150 GB**.
- **Continuous recordings** are retained **3 days**, then cleaned up automatically.

### 5. Store-and-Forward Queue (SQLite)
Every payload is persisted to a local **SQLite** queue before transmission:

| Table | Purpose |
| :--- | :--- |
| `queue` | `type` (event/snapshot), `file_path`, `payload` (JSON), `priority`, `timestamp`, `retry_count` |
| `processed_events` | Frigate `event_id`s already handled (deduplication) |

SQLite runs in **WAL mode** (crash-safe against sudden power loss) with a **10 GB** page cap so the
queue can never fill the ship's SSD.

### 6. Multi-Thread Uploader
Three daemon threads coordinate through a single `threading.Event` flag:

| Thread | Role |
| :--- | :--- |
| **Connection Checker** | While offline, probes the link every 5 min; on recovery, sets the flag → uploaders wake. |
| **Snapshot Uploader** | `POST /api/v1/snapshot`; prunes the queue if it exceeds 5000 items. |
| **Event Uploader** | `POST /api/v1/event` with the ~1 MB preview + metadata. |

On any failure the flag is cleared and the item stays queued for the next successful window.

### 7. Scheduled Tasks

| Task | Interval | Function |
| :--- | :--- | :--- |
| Snapshot tracing | 1 min | Grab latest frame per camera → queue |
| Event listener | 10 s | Poll Frigate for new events → download → transcode → enqueue |
| Evidence pruning | 30 min | Enforce the 200 GB → 150 GB policy |
| Recordings cleanup | 6 h | Delete recordings older than 3 days |

---

## 📂 Repository Structure

```
.
├── docker-compose.yml          # Frigate NVR container
├── monitor.sh                  # System monitoring
├── restart.sh                  # Restart Frigate
├── update.sh                   # Pull latest Frigate image
├── config/
│   └── config.yml              # Frigate NVR configuration
└── agent/
    ├── maritime_agent.py       # Orchestrator (detect → transcode → queue → upload)
    ├── requirements.txt        # Python dependencies (requests, schedule)
    ├── queue.db                # SQLite store-and-forward queue (runtime)
    ├── queue_storage/          # Transient files awaiting upload
    └── evidence/               # Local evidence (HD video + snapshots)
```

---

## 🚀 Getting Started

### Prerequisites

- **Git**
- **Docker & Docker Compose** (for Frigate NVR)
- **Python 3** (standard on Linux)
- An Intel CPU/iGPU with **VAAPI** support (for hardware transcoding)

### 1. Clone

```bash
git clone https://github.com/irfansss-03/maritime-edge-nvr-alert-system.git
cd maritime-edge-nvr-alert-system
```

### 2. Configure

Edit `config/config.yml` and set your camera RTSP URLs, detection zones, and retention policy.
Edit the constants at the top of `agent/maritime_agent.py` (or supply them via environment
variables) for your ship code, shore server URL, and API key.

### 3. Run Frigate NVR

```bash
docker compose up -d
docker compose logs -f        # follow logs
```

Frigate Web UI: `http://<host>:5000`

### 4. Run the Maritime Agent

```bash
cd agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python maritime_agent.py
```

Run it persistently in the background:

```bash
nohup python maritime_agent.py > agent.log 2>&1 &
tail -f agent.log
```

---

## 🔧 Configuration Reference

Key agent parameters (top of `maritime_agent.py`):

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `FRIGATE_API` | `http://localhost:5000/api` | Local Frigate API endpoint |
| `SERVER_API_URL` | *your shore server* | Shore REST API base URL |
| `SHIP_CODE` | `KP-001` | Registered ship identifier |
| `CAMERAS` | `kamera_depan`, `kamera_test` | Cameras to monitor |
| `ALERT_VIDEO_FPS` | `15` | Preview frame rate |
| `ALERT_VIDEO_BITRATE` | `500k` | Preview bitrate |
| `ALERT_VIDEO_SCALE` | `640` | Preview width (px) |
| `ALERT_VIDEO_MAX_SEC` | `30` | Max clip duration |
| `EVIDENCE_MAX_GB` | `200` | Evidence pruning threshold |
| `EVIDENCE_TARGET_GB` | `150` | Target size after pruning |
| `RECORDINGS_MAX_DAYS` | `3` | Recordings retention |
| `CONNECTION_CHECK_INTERVAL` | `300` | Offline probe interval (s) |
| `COMPRESS_RETRY_DELAY` | `180` | Delay between compression cycles (s) |

> ⚠️ **Security:** keep credentials (camera passwords, API keys, shore server URLs) out of version
> control — use environment variables or an untracked `.env` file.

---

## 🛡️ Fault Tolerance

| Failure | Behavior |
| :--- | :--- |
| Internet / VSAT down | Detection, recording, transcoding continue; data queues in SQLite. |
| Link recovers | Checker detects within ≤ 5 min; queue flushes automatically (FIFO). |
| Upload interrupted | Flag → False; file stays queued; retried on recovery. |
| VAAPI fails | Automatic fallback to libx264 in the same round. |
| Both codecs fail | Retry loop (5 rounds → 3 min delay → repeat) until success. |
| Clip not ready (HTTP 400) | Event is not marked processed; retried on the next poll. |
| Sudden power loss | SQLite **WAL** mode prevents database corruption. |
| Storage full | Evidence precision pruning + recordings cleanup + queue cap. |

---

## 🗺️ Roadmap

- [ ] Point connectivity check at the shore server (currently a placeholder URL).
- [ ] Validate shore-server JSON responses (captive-portal detection).
- [ ] Configure the critical zone for the main forward camera.
- [ ] Add more cameras (aft, port/starboard) via go2rtc.

---

## 👤 Author

- [@irfansss-03](https://github.com/irfansss-03)

<div align="center"><sub>Built for autonomous maritime surveillance at the edge.</sub></div>
