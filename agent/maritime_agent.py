import sqlite3
import time
import json
import os
import shutil
import threading
import subprocess
import requests
import schedule
from datetime import datetime, timezone, timedelta


# ======================================================
# LOAD .env (tanpa dependency tambahan)
# ======================================================
def _load_env(path: str) -> None:
    """Muat pasangan KEY=VALUE dari file .env bila ada.

    Nilai yang sudah ada di environment tidak ditimpa (setdefault),
    sehingga variabel dari systemd/docker tetap diprioritaskan.
    """
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_load_env(_ENV_FILE)

# Definisikan Timezone WIB (UTC+7)
WIB = timezone(timedelta(hours=7))

# ======================================================
# KONFIGURASI — dibaca dari environment / file .env
# Salin .env.example → .env lalu sesuaikan nilainya.
# ======================================================
# Endpoint Frigate Lokal
FRIGATE_API = os.environ.get("FRIGATE_API", "http://localhost:5000/api")

# Konfigurasi Server Staging/Darat
SERVER_API_URL = os.environ.get("SERVER_API_URL", "http://localhost:3000/api/v1")
API_KEY        = os.environ.get("API_KEY", "your-api-key")   # Harus cocok dengan tabel ships di server
SHIP_CODE      = os.environ.get("SHIP_CODE", "KP-001")       # Kode kapal yang terdaftar di server

# Direktori Agent Lokal (default: folder tempat script ini berada)
AGENT_DIR     = os.environ.get("AGENT_DIR", os.path.dirname(os.path.abspath(__file__)))
QUEUE_STORAGE = os.path.join(AGENT_DIR, "queue_storage")
EVIDENCE_DIR  = os.path.join(AGENT_DIR, "evidence")  # Penyimpanan HD lokal untuk investigasi
DB_PATH       = os.path.join(AGENT_DIR, "queue.db")

# Kamera yang akan dipantau untuk snapshot tracing (pisahkan dengan koma)
CAMERAS = [c.strip() for c in os.environ.get("CAMERAS", "kamera_depan,kamera_test").split(",") if c.strip()]

# ── Video Alert Compression Settings ──────────────────
# Target: video alert max ~1MB, 15fps, 360p, H.264, no audio
ALERT_VIDEO_FPS       = int(os.environ.get("ALERT_VIDEO_FPS", "15"))
ALERT_VIDEO_BITRATE   = os.environ.get("ALERT_VIDEO_BITRATE", "500k")  # 500 kbps → ~937KB untuk 15 detik
ALERT_VIDEO_SCALE     = int(os.environ.get("ALERT_VIDEO_SCALE", "640"))  # Lebar piksel (tinggi auto proporsional)
ALERT_VIDEO_MAX_SEC   = int(os.environ.get("ALERT_VIDEO_MAX_SEC", "30"))  # Potong maksimal 30 detik (safety limit)
VAAPI_DEVICE          = os.environ.get("VAAPI_DEVICE", "/dev/dri/renderD128")

# ── Evidence Storage Management ───────────────────────
EVIDENCE_MAX_GB       = int(os.environ.get("EVIDENCE_MAX_GB", "200"))     # Batas maksimal penyimpanan evidence (GB)
EVIDENCE_TARGET_GB    = int(os.environ.get("EVIDENCE_TARGET_GB", "150"))  # Target setelah pruning (hapus batch 50GB)

# ── Recordings Cleanup ────────────────────────────────
RECORDINGS_DIR        = os.environ.get("RECORDINGS_DIR", os.path.join(os.path.dirname(AGENT_DIR), "storage", "recordings"))
RECORDINGS_MAX_DAYS   = int(os.environ.get("RECORDINGS_MAX_DAYS", "3"))   # Hapus folder rekaman lebih dari 3 hari

# ── Server Connectivity Flag ─────────────────────────
CONNECTION_CHECK_INTERVAL = int(os.environ.get("CONNECTION_CHECK_INTERVAL", "300"))  # Cek koneksi tiap 5 menit saat flag=False
CONNECTION_CHECK_URL = os.environ.get("CONNECTION_CHECK_URL", "https://www.google.com")  # Sementara, ganti ke SERVER_API_URL nanti
server_connected = threading.Event()  # Flag koneksi: set()=True, clear()=False

# ── Compress Retry (Lokal, tidak butuh koneksi server) ──
COMPRESS_RETRY_DELAY  = int(os.environ.get("COMPRESS_RETRY_DELAY", "180"))  # Delay antar siklus kompresi jika gagal 5 putaran

def _ts_to_iso(epoch: float) -> str:
    """Konversi UNIX timestamp ke format ISO 8601 WIB (UTC+7)."""
    return datetime.fromtimestamp(epoch, tz=WIB).strftime('%Y-%m-%dT%H:%M:%S.000+07:00')

def check_server_connection() -> bool:
    """Cek koneksi ke server tujuan.
    Sementara menggunakan google.com karena server production belum siap.
    Ganti CONNECTION_CHECK_URL ke SERVER_API_URL saat server sudah siap."""
    try:
        response = requests.get(CONNECTION_CHECK_URL, timeout=10)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

def compress_video(input_path: str, output_path: str, max_attempts: int = 5) -> bool:
    """
    Mengompresi video alert HD menjadi versi preview ringan (~1MB).
    Menggunakan loop percobaan:
      - Mencoba VAAPI (Hardware) -> jika gagal -> mencoba Software (libx264).
      - Jika kedua metode gagal dalam satu putaran, sistem akan menunggu 5 detik
        dan mengulangi percobaan hingga batas max_attempts.
    
    Menjamin video HD tidak akan dikirim ke server darat untuk menghemat bandwidth VSAT.
    """
    vaapi_cmd = [
        "ffmpeg", "-y",
        "-vaapi_device", VAAPI_DEVICE,
        "-i", input_path,
        "-t", str(ALERT_VIDEO_MAX_SEC),
        "-vf", f"format=nv12|vaapi,hwupload,scale_vaapi=w={ALERT_VIDEO_SCALE}:h=-2",
        "-c:v", "h264_vaapi",
        "-b:v", ALERT_VIDEO_BITRATE,
        "-r", str(ALERT_VIDEO_FPS),
        "-an",
        "-movflags", "+faststart",
        output_path
    ]

    sw_cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(ALERT_VIDEO_MAX_SEC),
        "-vf", f"scale={ALERT_VIDEO_SCALE}:-2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", ALERT_VIDEO_BITRATE,
        "-r", str(ALERT_VIDEO_FPS),
        "-an",
        "-movflags", "+faststart",
        output_path
    ]

    for attempt in range(1, max_attempts + 1):
        print(f"  -> [Percobaan Kompresi {attempt}/{max_attempts}] Memulai...")
        
        # 1. Coba VAAPI (Hardware)
        try:
            result = subprocess.run(vaapi_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and os.path.exists(output_path):
                size_kb = os.path.getsize(output_path) / 1024
                print(f"  -> 🎬 Kompresi [VAAPI] Sukses pada percobaan {attempt}: {size_kb:.0f} KB")
                return True
            else:
                print(f"  -> ⚠️  Kompresi [VAAPI] gagal (rc={result.returncode})")
        except Exception as e:
            print(f"  -> ⚠️  Kompresi [VAAPI] error: {e}")

        # 2. Coba Software fallback
        try:
            result = subprocess.run(sw_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and os.path.exists(output_path):
                size_kb = os.path.getsize(output_path) / 1024
                print(f"  -> 🎬 Kompresi [Software libx264] Sukses pada percobaan {attempt}: {size_kb:.0f} KB")
                return True
            else:
                print(f"  -> ⚠️  Kompresi [Software] gagal (rc={result.returncode})")
        except Exception as e:
            print(f"  -> ⚠️  Kompresi [Software] error: {e}")

        if attempt < max_attempts:
            print("  -> ⏳ Kedua metode gagal. Menunggu 5 detik sebelum mencoba lagi...")
            time.sleep(5)

    print(f"  -> ❌ Seluruh {max_attempts} percobaan kompresi gagal dalam siklus ini.")
    return False

# ======================================================
# EVIDENCE STORAGE MANAGEMENT (Batas 200GB)
# Hapus batch file terlama sampai tersisa 150GB
# ======================================================
def _get_dir_size_gb(path: str) -> float:
    """Menghitung ukuran total direktori dalam GB."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total / (1024 ** 3)

def prune_evidence():
    """
    Memangkas folder evidence jika melebihi EVIDENCE_MAX_GB (200GB).
    Menggunakan algoritma Precision-based Pruning:
      1. Scan rekursif seluruh subfolder tanggal di EVIDENCE_DIR.
      2. Kumpulkan semua file ke daftar (filepath, mtime, size).
      3. Urutkan dari file terlama (FIFO).
      4. Hapus file terlama berpasangan (HD + JPG) secara iteratif
         hingga kapasitas yang dibebaskan mencapai target 50GB.
      5. Bersihkan subfolder tanggal yang sudah kosong.
    """
    current_gb = _get_dir_size_gb(EVIDENCE_DIR)
    if current_gb < EVIDENCE_MAX_GB:
        return

    print(f"[{datetime.now()}] ⚠️  Evidence {current_gb:.1f}GB >= {EVIDENCE_MAX_GB}GB. Memulai pruning...")

    # ── Langkah 1: Scan rekursif seluruh subfolder tanggal ──
    files = []
    for dirpath, dirnames, filenames in os.walk(EVIDENCE_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                files.append((fp, os.path.getmtime(fp), os.path.getsize(fp)))
            except OSError:
                pass

    # ── Langkah 2: Urutkan berdasarkan waktu modifikasi (terlama duluan / FIFO) ──
    files.sort(key=lambda x: x[1])

    freed = 0
    deleted_count = 0
    target_free = (current_gb - EVIDENCE_TARGET_GB) * (1024 ** 3)  # Konversi ke bytes

    # ── Langkah 3: Iterasi hapus file terlama + pasangannya ──
    for fp, mtime, size in files:
        if freed >= target_free:
            break
        if not os.path.exists(fp):  # Mungkin sudah dihapus sebagai pasangan
            continue
        try:
            os.remove(fp)
            freed += size
            deleted_count += 1

            # Hapus pasangan file (HD ↔ JPG) di folder yang sama
            if fp.endswith('_hd.mp4'):
                snap_pair = fp.replace('_hd.mp4', '.jpg')
                if os.path.exists(snap_pair):
                    freed += os.path.getsize(snap_pair)
                    os.remove(snap_pair)
                    deleted_count += 1
            elif fp.endswith('.jpg'):
                hd_pair = fp.replace('.jpg', '_hd.mp4')
                if os.path.exists(hd_pair):
                    freed += os.path.getsize(hd_pair)
                    os.remove(hd_pair)
                    deleted_count += 1
        except OSError as e:
            print(f"  [WARNING] Gagal hapus {fp}: {e}")

    # ── Langkah 4: Bersihkan subfolder tanggal yang sudah kosong ──
    for folder_name in os.listdir(EVIDENCE_DIR):
        folder_path = os.path.join(EVIDENCE_DIR, folder_name)
        if os.path.isdir(folder_path):
            try:
                os.rmdir(folder_path)  # Hanya berhasil jika folder kosong
                print(f"  -> 📁 Folder kosong dihapus: {folder_name}")
            except OSError:
                pass  # Folder masih berisi file, skip

    new_gb = _get_dir_size_gb(EVIDENCE_DIR)
    print(f"  -> 🗑️  Pruning selesai: {deleted_count} file dihapus, "
          f"{freed / (1024**3):.1f}GB dibebaskan. Sisa: {new_gb:.1f}GB")

# ======================================================
# RECORDINGS CLEANUP (Hapus folder > 3 hari)
# Berlaku untuk SEMUA kamera
# ======================================================
def cleanup_recordings():
    """
    Menghapus folder rekaman yang lebih tua dari RECORDINGS_MAX_DAYS hari.
    Struktur: /storage/recordings/YYYY-MM-DD/HH/kamera_xxx/
    Menghapus folder tanggal (level pertama) yang sudah kedaluwarsa.
    """
    if not os.path.exists(RECORDINGS_DIR):
        return

    today = datetime.now().date()
    deleted = 0

    for folder_name in sorted(os.listdir(RECORDINGS_DIR)):
        folder_path = os.path.join(RECORDINGS_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue

        # Parse nama folder sebagai tanggal (format: YYYY-MM-DD)
        try:
            folder_date = datetime.strptime(folder_name, "%Y-%m-%d").date()
        except ValueError:
            continue  # Skip folder yang bukan format tanggal

        age_days = (today - folder_date).days
        if age_days > RECORDINGS_MAX_DAYS:
            try:
                shutil.rmtree(folder_path)
                deleted += 1
                print(f"  -> 🗑️  Hapus rekaman lama: {folder_name} ({age_days} hari)")
            except OSError as e:
                print(f"  [WARNING] Gagal hapus {folder_path}: {e}")

    if deleted > 0:
        print(f"[{datetime.now()}] Recordings cleanup: {deleted} folder dihapus (>{RECORDINGS_MAX_DAYS} hari)")

# ======================================================
# INISIALISASI DATABASE SQLITE
# ======================================================
def init_db():
    os.makedirs(QUEUE_STORAGE, exist_ok=True)
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ── OPTIMALISASI SQLITE (Revisi Kelompok 1) ──────────
    # 1. WAL Mode: tahan banting terhadap mati listrik tiba-tiba di kapal
    c.execute("PRAGMA journal_mode = WAL")
    # 2. Batasi ukuran database maksimal 10 GB (2621440 pages × 4KB)
    c.execute("PRAGMA max_page_count = 2621440")

    c.execute('''CREATE TABLE IF NOT EXISTS queue
                 (id            INTEGER PRIMARY KEY AUTOINCREMENT,
                  type          TEXT,
                  file_path     TEXT,
                  payload       TEXT,
                  priority      INTEGER,
                  timestamp     INTEGER,
                  retry_count   INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS processed_events
                 (event_id TEXT PRIMARY KEY)''')
    conn.commit()
    conn.close()

# ======================================================
# TASK A: SNAPSHOT TRACING (Paket 1) (nunggu mas firza)
# Dijadwalkan: setiap 1 menit
# ======================================================
def take_snapshot():
    print(f"[{datetime.now()}] Task A: Mengambil Snapshot Tracing...")
    for cam in CAMERAS:
        try:
            res = requests.get(f"{FRIGATE_API}/{cam}/latest.jpg?h=1080", timeout=5)
            if res.status_code != 200:
                continue

            ts       = int(time.time())
            filename = f"snapshot_{cam}_{ts}.jpg"
            filepath = os.path.join(QUEUE_STORAGE, filename)

            with open(filepath, 'wb') as f:
                f.write(res.content)

            payload = {
                "ship_code":   SHIP_CODE,
                "camera_id":   cam,
                "captured_at": _ts_to_iso(ts),
                "timestamp":   ts,
                "timezone":    "WIB"
            }

            conn = sqlite3.connect(DB_PATH)
            c    = conn.cursor()
            c.execute(
                "INSERT INTO queue (type, file_path, payload, priority, timestamp) VALUES (?, ?, ?, ?, ?)",
                ("snapshot", filepath, json.dumps(payload), 2, ts)
            )
            conn.commit()
            conn.close()
            print(f"  -> Snapshot {cam} masuk antrean.")

        except Exception as e:
            print(f"  [ERROR] Gagal snapshot {cam}: {e}")

# ======================================================
# TASK B: EVENT LISTENER — AI Alert (Paket 2)
# Dijadwalkan: setiap 10 detik
# ======================================================
def poll_frigate_events():
    """Mengecek event baru dari Frigate API dan mengamankannya ke queue."""
    try:
        res = requests.get(f"{FRIGATE_API}/events?limit=10&has_clip=1", timeout=5)
        if res.status_code != 200:
            return

        events = res.json()
        conn   = sqlite3.connect(DB_PATH)
        c      = conn.cursor()

        for event in events:
            event_id = event['id']

            if event['end_time'] is None:
                continue

            c.execute("SELECT event_id FROM processed_events WHERE event_id=?", (event_id,))
            if c.fetchone():
                continue

            label = event['label']
            cam   = event['camera']
            if label not in ['person', 'boat']:
                continue

            # ── Filter Logic ────────────────────────────────
            # Filter durasi boat kini ditangani oleh Frigate (loitering_time: 3)
            # Agent hanya memfilter berdasarkan zona.
            if label == 'boat':
                zonas  = event.get('entered_zones', [])
                durasi = event['end_time'] - event['start_time']

                if cam != 'kamera_test' and 'zone_kritis' not in zonas:
                    continue

                severity = "critical"
                print(f"[{datetime.now()}] 🚨 BOAT APPROACHING! Cam:{cam} Dur:{durasi:.1f}s Zones:{zonas}")
            else:
                durasi   = event['end_time'] - event['start_time']
                severity = "critical"
                print(f"[{datetime.now()}] 🚨 PERSON DETECTED! Cam:{cam}")
            # ────────────────────────────────────────────────

            print(f"[{datetime.now()}] Mengamankan event {event_id} ({label})...")

            # ── Penamaan File Berbasis Unix Timestamp Milidetik (Revisi Kelompok 2) ──
            # Menggunakan Unix timestamp 64-bit dalam milidetik sebagai nama file.
            # Contoh: start_time 1780896211.014 → file_base "1780896211014"
            # Menjamin keunikan 100% tanpa risiko overwrite.
            event_dt = datetime.fromtimestamp(event['start_time'], tz=WIB)
            file_base = str(int(event['start_time'] * 1000))  # Unix timestamp milidetik
            date_folder = event_dt.strftime('%Y-%m-%d')  # Subfolder tanggal

            # ── Download Video Clip (HD Asli) ──────────────
            hd_path  = os.path.join(QUEUE_STORAGE, f"{file_base}_hd.mp4")
            vid_path = os.path.join(QUEUE_STORAGE, f"{file_base}.mp4")
            clip_res = requests.get(
                f"{FRIGATE_API}/events/{event_id}/clip.mp4",
                stream=True, timeout=60
            )
            if clip_res.status_code == 200:
                with open(hd_path, 'wb') as f:
                    for chunk in clip_res.iter_content(chunk_size=1024 * 1024):
                        if chunk: f.write(chunk)
                hd_size = os.path.getsize(hd_path) / 1024
                print(f"  -> Video HD diunduh: {hd_size:.0f} KB")
            else:
                print(f"  [ERROR] Gagal download clip {event_id}: HTTP {clip_res.status_code}")
                continue

            # ── Kompresi Video HD → Preview (~1MB) ────────
            # Kompresi adalah operasi LOKAL (FFmpeg + iGPU), tidak butuh koneksi server.
            # Retry infinite: 5 putaran → delay 3 menit → ulangi sampai sukses.
            # Tidak ada fallback pengiriman video HD ke server.
            compress_cycle = 0
            while True:
                compress_cycle += 1
                print(f"  -> [Siklus Kompresi {compress_cycle}] Memulai 5 putaran...")
                compressed = compress_video(hd_path, vid_path, max_attempts=5)
                if compressed:
                    break
                print(f"  -> ⏳ Siklus {compress_cycle} gagal. Menunggu {COMPRESS_RETRY_DELAY // 60} menit sebelum retry...")
                time.sleep(COMPRESS_RETRY_DELAY)

            # Kompresi pasti sukses di titik ini
            # Pindahkan HD ke subfolder tanggal di evidence (Revisi Kelompok 2)
            evidence_date_dir = os.path.join(EVIDENCE_DIR, date_folder)
            os.makedirs(evidence_date_dir, exist_ok=True)
            evidence_file = os.path.join(evidence_date_dir, f"{file_base}_hd.mp4")
            prune_evidence()  # Cek dan bersihkan evidence sebelum menyimpan
            os.rename(hd_path, evidence_file)
            print(f"  -> 💾 Video HD disimpan lokal: {evidence_file}")

            # ── Download Snapshot Bukti → Simpan di Subfolder Tanggal ──
            evidence_snap = os.path.join(evidence_date_dir, f"{file_base}.jpg")
            snap_res  = requests.get(
                f"{FRIGATE_API}/events/{event_id}/snapshot.jpg",
                stream=True, timeout=10
            )
            if snap_res.status_code == 200:
                with open(evidence_snap, 'wb') as f:
                    for chunk in snap_res.iter_content(chunk_size=512 * 1024):
                        if chunk: f.write(chunk)
                print(f"  -> 📸 Snapshot bukti disimpan lokal: {evidence_snap}")
            else:
                print(f"  [WARNING] Gagal download snapshot bukti {event_id}")

            # ── Payload (tanpa snap_path, snapshot tidak dikirim ke server) ──
            score = event.get('data', {}).get('score', 0)
            payload = {
                "ship_code":  SHIP_CODE,
                "camera_id":  cam,
                "event_id":   event_id,
                "label":      label,
                "confidence": round(score, 4),
                "severity":   severity,
                "duration":   int(durasi),
                "started_at": _ts_to_iso(event['start_time']),
                "timezone":   "WIB"
            }

            c.execute(
                "INSERT INTO queue (type, file_path, payload, priority, timestamp) VALUES (?, ?, ?, ?, ?)",
                ("event", vid_path, json.dumps(payload), 1, int(event['start_time']))
            )
            c.execute("INSERT INTO processed_events (event_id) VALUES (?)", (event_id,))
            conn.commit()

        conn.close()

    except Exception as e:
        print(f"  [ERROR] Event Listener: {e}")

# ======================================================
# UPLOADER THREAD 1: Khusus SNAPSHOT
# Berjalan mandiri, tidak diblokir oleh upload video
# Menunggu flag koneksi server sebelum upload
# ======================================================
def upload_snapshot_worker():
    """Thread khusus mengirim snapshot ke server.
    Menunggu flag server_connected sebelum mencoba upload.
    Jika upload gagal → flag False, thread idle sampai flag True."""
    auth_headers = {"Authorization": f"Bearer {API_KEY}"}
    worker_name  = "📸 Snapshot"

    while True:
        # ━━ Tunggu flag koneksi True ━━━━━━━━━━━━━━━━━━━━
        server_connected.wait()  # Blok di sini jika flag=False (hemat CPU)

        try:
            conn = sqlite3.connect(DB_PATH)
            c    = conn.cursor()

            # Pruning: hapus snapshot lama jika menumpuk > 5000
            c.execute("SELECT count(*) FROM queue WHERE type='snapshot'")
            if c.fetchone()[0] > 5000:
                print(f"[{worker_name}] PRUNING: Antrean penuh, hapus 500 terlama...")
                c.execute(
                    "DELETE FROM queue WHERE type='snapshot' AND id IN "
                    "(SELECT id FROM queue WHERE type='snapshot' ORDER BY timestamp ASC LIMIT 500)"
                )
                conn.commit()

            # Ambil 1 snapshot terlama
            c.execute(
                "SELECT id, file_path, payload FROM queue WHERE type='snapshot' ORDER BY timestamp ASC LIMIT 1"
            )
            item = c.fetchone()

            if not item:
                conn.close()
                time.sleep(5)
                continue

            q_id, file_path, payload_str = item
            payload = json.loads(payload_str)

            if not os.path.exists(file_path):
                c.execute("DELETE FROM queue WHERE id=?", (q_id,))
                conn.commit()
                conn.close()
                continue

            print(f"[{datetime.now()}] [{worker_name}] Uploading (queue_id={q_id})...")

            with open(file_path, 'rb') as img:
                response = requests.post(
                    f"{SERVER_API_URL}/snapshot",
                    headers=auth_headers,
                    data={
                        "ship_code":   payload["ship_code"],
                        "camera_id":   payload["camera_id"],
                        "captured_at": payload["captured_at"],
                    },
                    files={"image": (os.path.basename(file_path), img, "image/jpeg")},
                    timeout=30
                )

            if response.status_code == 200:
                print(f"  -> [{worker_name}] ✅ Upload sukses!")
                if os.path.exists(file_path):
                    os.remove(file_path)
                c.execute("DELETE FROM queue WHERE id=?", (q_id,))
            else:
                # Upload gagal → Flag False, retry saat koneksi pulih
                server_connected.clear()
                print(f"  -> [{worker_name}] ❌ Gagal (HTTP {response.status_code}). Flag → False.")
                c.execute("UPDATE queue SET retry_count = retry_count + 1 WHERE id=?", (q_id,))

            conn.commit()
            conn.close()
            time.sleep(1)

        except requests.exceptions.RequestException:
            server_connected.clear()  # Flag → False
            print(f"[{datetime.now()}] [{worker_name}] ⚠️  Koneksi gagal. Flag → False.")
            time.sleep(5)
        except Exception as e:
            print(f"  [{worker_name}] [ERROR] {e}")
            time.sleep(5)

# ======================================================
# UPLOADER THREAD 2: Khusus EVENT ALERT
# Berjalan mandiri, tidak memblokir upload snapshot
# Menunggu flag koneksi, retry kompresi HD jika diperlukan
# ======================================================
def upload_event_worker():
    """Thread khusus mengirim event alert (video compressed + metadata) ke server.
    
    Alur:
    1. Tunggu flag server_connected = True
    2. Ambil event terlama dari queue (selalu file terkompresi .mp4)
    3. Upload video terkompresi ke server
       - Sukses → hapus file & queue entry
       - Gagal  → flag False, kembali ke langkah 1
    
    Kompresi sudah dijamin selesai di poll_frigate_events() (retry infinite).
    Snapshot bukti kejadian TIDAK dikirim ke server — disimpan lokal di evidence/."""
    auth_headers = {"Authorization": f"Bearer {API_KEY}"}
    worker_name  = "🚨 Alert"

    while True:
        # ━━ Tunggu flag koneksi True ━━━━━━━━━━━━━━━━━━━━
        server_connected.wait()  # Blok di sini jika flag=False (hemat CPU)

        try:
            conn = sqlite3.connect(DB_PATH)
            c    = conn.cursor()

            # Ambil 1 event terlama
            c.execute(
                "SELECT id, file_path, payload FROM queue WHERE type='event' ORDER BY timestamp ASC LIMIT 1"
            )
            item = c.fetchone()

            if not item:
                conn.close()
                time.sleep(5)
                continue

            q_id, file_path, payload_str = item
            payload = json.loads(payload_str)

            if not os.path.exists(file_path):
                c.execute("DELETE FROM queue WHERE id=?", (q_id,))
                conn.commit()
                conn.close()
                continue

            # ━━ Upload video terkompresi ke server ━━━━━━━━━━━━━━━━━━
            print(f"[{datetime.now()}] [{worker_name}] Uploading event (queue_id={q_id})...")

            try:
                vid_file = open(file_path, 'rb')
                files = {
                    "video": (os.path.basename(file_path), vid_file, "video/mp4")
                }

                response = requests.post(
                    f"{SERVER_API_URL}/event",
                    headers=auth_headers,
                    data={
                        "ship_code":   payload["ship_code"],
                        "camera_id":   payload["camera_id"],
                        "event_id":    payload["event_id"],
                        "label":       payload["label"],
                        "confidence":  str(payload["confidence"]),
                        "severity":    payload["severity"],
                        "duration":    str(payload["duration"]),
                        "started_at":  payload["started_at"],
                    },
                    files=files,
                    timeout=120
                )
            finally:
                vid_file.close()

            if response.status_code == 200:
                print(f"  -> [{worker_name}] ✅ Upload sukses!")
                if os.path.exists(file_path):
                    os.remove(file_path)
                c.execute("DELETE FROM queue WHERE id=?", (q_id,))
            else:
                # Upload gagal → Flag False, retry saat koneksi pulih
                server_connected.clear()
                print(f"  -> [{worker_name}] ❌ Gagal (HTTP {response.status_code}). Flag → False.")
                c.execute("UPDATE queue SET retry_count = retry_count + 1 WHERE id=?", (q_id,))

            conn.commit()
            conn.close()
            time.sleep(1)

        except requests.exceptions.RequestException:
            server_connected.clear()  # Flag → False
            print(f"[{datetime.now()}] [{worker_name}] ⚠️  Koneksi gagal. Flag → False.")
            time.sleep(5)
        except Exception as e:
            print(f"  [{worker_name}] [ERROR] {e}")
            time.sleep(5)

# ======================================================
# CONNECTION CHECKER THREAD
# Memantau koneksi server saat flag=False
# ======================================================
def connection_checker_worker():
    """Thread pemantau koneksi server.
    Saat flag=False: cek koneksi setiap 5 menit ke CONNECTION_CHECK_URL.
    Saat flag=True: idle (tidak cek — kegagalan upload/kompresi akan set False).
    Saat koneksi pulih: set flag=True → semua thread uploader otomatis bangun."""
    worker_name = "🔌 ConnChecker"
    while True:
        if not server_connected.is_set():
            print(f"[{datetime.now()}] [{worker_name}] Mengecek koneksi ke server...")
            if check_server_connection():
                server_connected.set()
                print(f"[{datetime.now()}] [{worker_name}] ✅ Koneksi server pulih! Flag → True")
            else:
                print(f"[{datetime.now()}] [{worker_name}] ❌ Server tidak terjangkau. "
                      f"Cek ulang dalam {CONNECTION_CHECK_INTERVAL // 60} menit...")
        time.sleep(CONNECTION_CHECK_INTERVAL)

# ======================================================
# MAIN
# ======================================================
if __name__ == "__main__":
    print("=" * 50)
    print("  Maritime Agent — Multi-Thread Store & Forward")
    print(f"  Server  : {SERVER_API_URL}")
    print(f"  Ship    : {SHIP_CODE}")
    print(f"  Cameras : {', '.join(CAMERAS)}")
    print("=" * 50)
    init_db()

    # Cek koneksi awal ke server
    print("\n🔌 Mengecek koneksi awal ke server...")
    if check_server_connection():
        server_connected.set()
        print("✅ Koneksi awal ke server berhasil. Flag → True\n")
    else:
        print("❌ Koneksi awal gagal. Flag → False. Menunggu pemulihan...\n")

    # Jalankan pruning & cleanup saat startup
    prune_evidence()
    cleanup_recordings()

    # Task A: Snapshot setiap 1 menit
    schedule.every(1).minutes.do(take_snapshot)

    # Task B: Poll Frigate events setiap 10 detik
    schedule.every(10).seconds.do(poll_frigate_events)

    # Task C: Evidence pruning setiap 30 menit
    schedule.every(30).minutes.do(prune_evidence)

    # Task D: Recordings cleanup setiap 6 jam
    schedule.every(6).hours.do(cleanup_recordings)

    # Thread 0: Connection Checker (cek koneksi setiap 5 menit saat Flag=False)
    t0 = threading.Thread(target=connection_checker_worker, daemon=True, name="ConnChecker")
    t0.start()

    # Thread 1: Upload Snapshot (berjalan mandiri, menunggu flag)
    t1 = threading.Thread(target=upload_snapshot_worker, daemon=True, name="SnapshotUploader")
    t1.start()

    # Thread 2: Upload Event Alert (berjalan mandiri, menunggu flag, retry kompresi)
    t2 = threading.Thread(target=upload_event_worker, daemon=True, name="EventUploader")
    t2.start()

    print("Sistem berjalan (3 thread aktif: ConnChecker + 2 Uploader). Tekan Ctrl+C untuk berhenti.\n")
    while True:
        schedule.run_pending()
        time.sleep(1)
