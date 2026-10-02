#!/bin/bash
# ══════════════════════════════════════════════════════════════
# Maritime Edge-NVR System Monitor
# Dell OptiPlex 5050 — Intel Core i7-6700 + HD Graphics 530
# ══════════════════════════════════════════════════════════════

# Warna Terminal
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
WHITE='\033[1;37m'
GRAY='\033[0;90m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# Path
FRIGATE_DIR="/home/nvrpc/frigate"
AGENT_DIR="${FRIGATE_DIR}/agent"
QUEUE_DB="${AGENT_DIR}/queue.db"
EVIDENCE_DIR="${AGENT_DIR}/evidence"
QUEUE_STORAGE="${AGENT_DIR}/queue_storage"

clear

echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${WHITE}${BOLD}🚢 Maritime Edge-NVR System Monitor${NC}                         ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${GRAY}$(date '+%A, %d %B %Y — %H:%M:%S WIB')${NC}              ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── 1. CONTAINER STATUS ──────────────────────────────────────
echo -e "${WHITE}${BOLD}┌─ 📦 CONTAINER STATUS ─────────────────────────────────────┐${NC}"
CONTAINER_STATUS=$(sudo docker inspect --format='{{.State.Status}}' frigate 2>/dev/null)
CONTAINER_UPTIME=$(sudo docker inspect --format='{{.State.StartedAt}}' frigate 2>/dev/null)
HEALTH=$(sudo docker inspect --format='{{.State.Health.Status}}' frigate 2>/dev/null)

if [ "$CONTAINER_STATUS" = "running" ]; then
    echo -e "  ${GREEN}●${NC} Frigate NVR      : ${GREEN}${BOLD}RUNNING${NC}"
else
    echo -e "  ${RED}●${NC} Frigate NVR      : ${RED}${BOLD}${CONTAINER_STATUS:-DOWN}${NC}"
fi

if [ "$HEALTH" = "healthy" ]; then
    echo -e "  ${GREEN}●${NC} Health Check     : ${GREEN}Healthy${NC}"
elif [ "$HEALTH" = "unhealthy" ]; then
    echo -e "  ${RED}●${NC} Health Check     : ${RED}Unhealthy${NC}"
else
    echo -e "  ${YELLOW}●${NC} Health Check     : ${YELLOW}${HEALTH:-N/A}${NC}"
fi

if [ -n "$CONTAINER_UPTIME" ] && [ "$CONTAINER_UPTIME" != "" ]; then
    START_EPOCH=$(date -d "$CONTAINER_UPTIME" +%s 2>/dev/null)
    NOW_EPOCH=$(date +%s)
    if [ -n "$START_EPOCH" ]; then
        UPTIME_SEC=$((NOW_EPOCH - START_EPOCH))
        UPTIME_DAYS=$((UPTIME_SEC / 86400))
        UPTIME_HOURS=$(( (UPTIME_SEC % 86400) / 3600 ))
        UPTIME_MINS=$(( (UPTIME_SEC % 3600) / 60 ))
        echo -e "  ${CYAN}↑${NC} Uptime           : ${UPTIME_DAYS}d ${UPTIME_HOURS}h ${UPTIME_MINS}m"
    fi
fi

VERSION=$(curl -sf http://localhost:5000/api/version 2>/dev/null)
echo -e "  ${BLUE}ℹ${NC} Version          : ${VERSION:-N/A}"
echo -e "${WHITE}${BOLD}└───────────────────────────────────────────────────────────┘${NC}"
echo ""

# ── 2. CPU & MEMORY ──────────────────────────────────────────
echo -e "${WHITE}${BOLD}┌─ 📊 RESOURCE USAGE ───────────────────────────────────────┐${NC}"

# Docker stats
DOCKER_STATS=$(sudo docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}' frigate 2>/dev/null)
if [ -n "$DOCKER_STATS" ]; then
    CPU_PCT=$(echo "$DOCKER_STATS" | cut -d'|' -f1)
    MEM_USAGE=$(echo "$DOCKER_STATS" | cut -d'|' -f2)
    MEM_PCT=$(echo "$DOCKER_STATS" | cut -d'|' -f3)
    echo -e "  ${CYAN}⚡${NC} Frigate CPU      : ${CPU_PCT}"
    echo -e "  ${CYAN}🧠${NC} Frigate Memory   : ${MEM_USAGE} (${MEM_PCT})"
fi

# System-wide CPU & Load
LOAD_AVG=$(cat /proc/loadavg | awk '{print $1, $2, $3}')
CPU_CORES=$(nproc)
echo -e "  ${BLUE}📈${NC} System Load      : ${LOAD_AVG} ${GRAY}(${CPU_CORES} cores)${NC}"

# System Memory
MEM_INFO=$(free -h | awk '/^Mem:/ {print $3 " used / " $2 " total"}')
echo -e "  ${BLUE}💻${NC} System Memory    : ${MEM_INFO}"

# CPU Temperature (jika tersedia)
if command -v sensors &>/dev/null; then
    CPU_TEMP=$(sensors 2>/dev/null | grep -i 'package id 0\|Core 0' | head -1 | grep -oP '\+\K[0-9.]+' | head -1)
    if [ -n "$CPU_TEMP" ]; then
        TEMP_INT=${CPU_TEMP%.*}
        if [ "$TEMP_INT" -ge 80 ]; then
            echo -e "  ${RED}🌡️${NC} CPU Temperature  : ${RED}${BOLD}${CPU_TEMP}°C ⚠️ OVERHEAT${NC}"
        elif [ "$TEMP_INT" -ge 65 ]; then
            echo -e "  ${YELLOW}🌡️${NC} CPU Temperature  : ${YELLOW}${CPU_TEMP}°C${NC}"
        else
            echo -e "  ${GREEN}🌡️${NC} CPU Temperature  : ${GREEN}${CPU_TEMP}°C${NC}"
        fi
    fi
fi
echo -e "${WHITE}${BOLD}└───────────────────────────────────────────────────────────┘${NC}"
echo ""

# ── 3. INTEL GPU & VAAPI STATUS ──────────────────────────────
echo -e "${WHITE}${BOLD}┌─ 🎮 INTEL GPU & VAAPI STATUS ────────────────────────────┐${NC}"

# Cek apakah device GPU tersedia
if [ -e /dev/dri/renderD128 ]; then
    echo -e "  ${GREEN}●${NC} GPU Device       : ${GREEN}/dev/dri/renderD128 tersedia${NC}"
else
    echo -e "  ${RED}●${NC} GPU Device       : ${RED}/dev/dri/renderD128 TIDAK DITEMUKAN${NC}"
fi

# VAAPI Driver Info
VAAPI_INFO=$(sudo docker exec frigate vainfo 2>&1)
VAAPI_DRIVER=$(echo "$VAAPI_INFO" | grep -i "driver version" | head -1 | sed 's/^[ ]*//')
if [ -n "$VAAPI_DRIVER" ]; then
    echo -e "  ${GREEN}●${NC} VAAPI Driver     : ${GREEN}${VAAPI_DRIVER}${NC}"
else
    echo -e "  ${RED}●${NC} VAAPI Driver     : ${RED}Driver tidak terdeteksi${NC}"
fi

# Cek encoder H.264 tersedia
H264_ENC=$(echo "$VAAPI_INFO" | grep -c "VAEntrypointEncSlice")
if [ "$H264_ENC" -gt 0 ]; then
    echo -e "  ${GREEN}●${NC} H.264 Encoder    : ${GREEN}Tersedia (${H264_ENC} entrypoint)${NC}"
else
    echo -e "  ${RED}●${NC} H.264 Encoder    : ${RED}TIDAK TERSEDIA${NC}"
fi

# ── GPU HANG DETECTION ───────────────────────────────────────
echo -e "  ${GRAY}─── GPU Health Check ───${NC}"

GPU_HANG=false
GPU_ISSUES=""

# Cek 1: Pesan error GPU hang di kernel log (dmesg)
# Filter ketat: hanya tangkap pesan hang/reset yang serius, abaikan pesan boot normal (vgaarb, etc.)
GPU_HANG_DMESG=$(sudo dmesg 2>/dev/null | grep -iE "gpu hang detected|gpu reset|i915.*hang|i915.*wedged|render ring.*hang" | grep -vi "vgaarb" | tail -3)
if [ -n "$GPU_HANG_DMESG" ]; then
    GPU_HANG=true
    GPU_ISSUES="${GPU_ISSUES}\n  ${RED}  ⚠ Kernel GPU Hang terdeteksi di dmesg!${NC}"
    while IFS= read -r line; do
        GPU_ISSUES="${GPU_ISSUES}\n  ${RED}    → ${line}${NC}"
    done <<< "$GPU_HANG_DMESG"
fi

# Cek 2: Pesan error GPU di log Docker Frigate (hanya error nyata, bukan info biasa)
GPU_DOCKER_ERR=$(sudo docker logs --since 30m frigate 2>&1 | grep -iE "gpu.*error|hwaccel.*fail|device.*busy|failed.*encode|vaapi.*error|vaapi.*fail" | grep -vi "using vaapi\|vaapi device\|hwaccel vaapi" | tail -3)
if [ -n "$GPU_DOCKER_ERR" ]; then
    GPU_HANG=true
    GPU_ISSUES="${GPU_ISSUES}\n  ${YELLOW}  ⚠ GPU error di log Frigate:${NC}"
    while IFS= read -r line; do
        GPU_ISSUES="${GPU_ISSUES}\n  ${YELLOW}    → ${line}${NC}"
    done <<< "$GPU_DOCKER_ERR"
fi

# Cek 3: GPU utilization via intel_gpu_top (jika tersedia)
if command -v intel_gpu_top &>/dev/null; then
    GPU_BUSY=$(sudo timeout 2 intel_gpu_top -s 1000 -o - 2>/dev/null | awk 'NR==2 {print $2}')
    # Pastikan hasilnya numerik sebelum membandingkan
    if [ -n "$GPU_BUSY" ] && [[ "$GPU_BUSY" =~ ^[0-9]+\.?[0-9]*$ ]]; then
        GPU_BUSY_INT=${GPU_BUSY%.*}
        if [ "$GPU_BUSY_INT" -ge 95 ]; then
            GPU_HANG=true
            GPU_ISSUES="${GPU_ISSUES}\n  ${RED}  ⚠ GPU Usage ${GPU_BUSY}% — Kemungkinan HANG/Overload!${NC}"
        elif [ "$GPU_BUSY_INT" -ge 75 ]; then
            echo -e "  ${YELLOW}⚡${NC} GPU Utilization  : ${YELLOW}${GPU_BUSY}% (Beban tinggi)${NC}"
        else
            echo -e "  ${GREEN}⚡${NC} GPU Utilization  : ${GREEN}${GPU_BUSY}%${NC}"
        fi
    fi
fi

# Cek 4: Test encode VAAPI cepat di HOST (bukan di container Docker)
# Maritime Agent menjalankan ffmpeg di host, jadi test harus di host juga
VAAPI_TEST_FILE="/tmp/.vaapi_test_$$.mp4"
ffmpeg -y -vaapi_device /dev/dri/renderD128 \
    -f lavfi -i "color=black:s=160x90:d=1:rate=1" \
    -vf "format=nv12,hwupload" \
    -c:v h264_vaapi -frames:v 1 \
    "$VAAPI_TEST_FILE" </dev/null >/dev/null 2>&1
if [ -f "$VAAPI_TEST_FILE" ] && [ -s "$VAAPI_TEST_FILE" ]; then
    echo -e "  ${GREEN}✓${NC} Encode Test      : ${GREEN}VAAPI h264_vaapi OK (host ffmpeg)${NC}"
    rm -f "$VAAPI_TEST_FILE"
else
    GPU_HANG=true
    GPU_ISSUES="${GPU_ISSUES}\n  ${RED}  ✗ VAAPI encode test GAGAL di host! GPU mungkin hang.${NC}"
    rm -f "$VAAPI_TEST_FILE"
fi

# Tampilkan hasil GPU
if [ "$GPU_HANG" = true ]; then
    echo -e "  ${RED}${BOLD}⚠️  GPU STATUS: BERMASALAH${NC}"
    echo -e "$GPU_ISSUES"
    echo -e "  ${YELLOW}  💡 Solusi: sudo systemctl restart docker ${GRAY}atau${NC} ${YELLOW}reboot${NC}"
else
    echo -e "  ${GREEN}${BOLD}✅ GPU STATUS: SEHAT${NC}"
fi

echo -e "${WHITE}${BOLD}└───────────────────────────────────────────────────────────┘${NC}"
echo ""

# ── 4. AI DETECTION & CAMERAS ────────────────────────────────
echo -e "${WHITE}${BOLD}┌─ 🤖 AI DETECTION & CAMERAS ─────────────────────────────┐${NC}"
STATS_JSON=$(curl -sf http://localhost:5000/api/stats 2>/dev/null)
if [ -n "$STATS_JSON" ]; then
    echo "$STATS_JSON" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    # Detectors
    det = d.get('detectors', {})
    for name, info in det.items():
        speed = info.get('inference_speed', 0)
        pid = info.get('pid', '?')
        if speed > 100:
            color = '\033[0;31m'  # red
            status = '⚠️ LAMBAT'
        elif speed > 50:
            color = '\033[1;33m'  # yellow
            status = ''
        else:
            color = '\033[0;32m'  # green
            status = ''
        print(f'  {color}🧠\033[0m {name}: \033[1m{speed:.1f}ms\033[0m/inference (PID:{pid}) {status}')
    
    # Cameras
    cams = d.get('cameras', {})
    print()
    for cam_name, cam_info in cams.items():
        fps = cam_info.get('camera_fps', 0)
        det_fps = cam_info.get('detection_fps', 0)
        proc_fps = cam_info.get('process_fps', 0)
        if fps > 0:
            status = '\033[0;32m● LIVE\033[0m'
        else:
            status = '\033[0;31m● OFFLINE\033[0m'
        print(f'  {status}  {cam_name}')
        print(f'       Camera: {fps:.1f} FPS | Detect: {det_fps:.1f} FPS | Process: {proc_fps:.1f} FPS')
except Exception as e:
    print(f'  Stats error: {e}')
" 2>/dev/null
else
    echo -e "  ${RED}Stats tidak tersedia (Frigate mungkin belum siap)${NC}"
fi
echo -e "${WHITE}${BOLD}└───────────────────────────────────────────────────────────┘${NC}"
echo ""

# ── 5. MARITIME AGENT STATUS ─────────────────────────────────
echo -e "${WHITE}${BOLD}┌─ 🚢 MARITIME AGENT STATUS ────────────────────────────────┐${NC}"

# Cek apakah agent berjalan
AGENT_PID=$(pgrep -f "maritime_agent.py" 2>/dev/null)
if [ -n "$AGENT_PID" ]; then
    echo -e "  ${GREEN}●${NC} Agent Process    : ${GREEN}RUNNING${NC} (PID: ${AGENT_PID})"
else
    echo -e "  ${RED}●${NC} Agent Process    : ${RED}NOT RUNNING${NC}"
fi

# Queue stats dari SQLite
if [ -f "$QUEUE_DB" ]; then
    SNAP_QUEUE=$(sqlite3 "$QUEUE_DB" "SELECT COUNT(*) FROM queue WHERE type='snapshot';" 2>/dev/null || echo "0")
    EVENT_QUEUE=$(sqlite3 "$QUEUE_DB" "SELECT COUNT(*) FROM queue WHERE type='event';" 2>/dev/null || echo "0")
    TOTAL_PROCESSED=$(sqlite3 "$QUEUE_DB" "SELECT COUNT(*) FROM processed_events;" 2>/dev/null || echo "0")
    
    if [ "$EVENT_QUEUE" -gt 0 ]; then
        EVENT_COLOR="${YELLOW}"
    else
        EVENT_COLOR="${GREEN}"
    fi
    if [ "$SNAP_QUEUE" -gt 100 ]; then
        SNAP_COLOR="${RED}"
    elif [ "$SNAP_QUEUE" -gt 20 ]; then
        SNAP_COLOR="${YELLOW}"
    else
        SNAP_COLOR="${GREEN}"
    fi
    
    echo -e "  ${SNAP_COLOR}📸${NC} Snapshot Queue   : ${SNAP_COLOR}${SNAP_QUEUE} menunggu${NC}"
    echo -e "  ${EVENT_COLOR}🚨${NC} Alert Queue      : ${EVENT_COLOR}${EVENT_QUEUE} menunggu${NC}"
    echo -e "  ${BLUE}✅${NC} Total Processed  : ${TOTAL_PROCESSED} events"
else
    echo -e "  ${YELLOW}⚠${NC}  Database queue.db belum dibuat"
fi
echo -e "${WHITE}${BOLD}└───────────────────────────────────────────────────────────┘${NC}"
echo ""

# ── 6. STORAGE ───────────────────────────────────────────────
echo -e "${WHITE}${BOLD}┌─ 💾 STORAGE ──────────────────────────────────────────────┐${NC}"

CLIPS_SIZE=$(du -sh ${FRIGATE_DIR}/storage/clips 2>/dev/null | awk '{print $1}' || echo "N/A")
EVIDENCE_SIZE=$(du -sh ${EVIDENCE_DIR} 2>/dev/null | awk '{print $1}' || echo "0")
QUEUE_SIZE=$(du -sh ${QUEUE_STORAGE} 2>/dev/null | awk '{print $1}' || echo "0")
EVIDENCE_COUNT=$(find ${EVIDENCE_DIR} -type f 2>/dev/null | wc -l)
QUEUE_FILE_COUNT=$(find ${QUEUE_STORAGE} -type f 2>/dev/null | wc -l)

echo -e "  ${BLUE}📁${NC} Frigate Clips    : ${CLIPS_SIZE}"
echo -e "  ${BLUE}📁${NC} Evidence (HD)    : ${EVIDENCE_SIZE} ${GRAY}(${EVIDENCE_COUNT} files)${NC}"
echo -e "  ${BLUE}📁${NC} Queue Storage    : ${QUEUE_SIZE} ${GRAY}(${QUEUE_FILE_COUNT} files)${NC}"

# Disk usage
DISK_INFO=$(df -h / | tail -1)
DISK_USED=$(echo "$DISK_INFO" | awk '{print $3}')
DISK_TOTAL=$(echo "$DISK_INFO" | awk '{print $2}')
DISK_PCT=$(echo "$DISK_INFO" | awk '{print $5}' | tr -d '%')
DISK_AVAIL=$(echo "$DISK_INFO" | awk '{print $4}')

if [ "$DISK_PCT" -ge 90 ]; then
    echo -e "  ${RED}${BOLD}⚠️${NC} Disk Usage       : ${RED}${BOLD}${DISK_USED} / ${DISK_TOTAL} (${DISK_PCT}%) — KRITIS!${NC}"
    echo -e "  ${RED}  Tersisa: ${DISK_AVAIL} — Segera bersihkan evidence atau clips!${NC}"
elif [ "$DISK_PCT" -ge 75 ]; then
    echo -e "  ${YELLOW}💿${NC} Disk Usage       : ${YELLOW}${DISK_USED} / ${DISK_TOTAL} (${DISK_PCT}%)${NC} — Tersisa: ${DISK_AVAIL}"
else
    echo -e "  ${GREEN}💿${NC} Disk Usage       : ${GREEN}${DISK_USED} / ${DISK_TOTAL} (${DISK_PCT}%)${NC} — Tersisa: ${DISK_AVAIL}"
fi
echo -e "${WHITE}${BOLD}└───────────────────────────────────────────────────────────┘${NC}"
echo ""

# ── 7. RECENT ERRORS ─────────────────────────────────────────
echo -e "${WHITE}${BOLD}┌─ ⚠️  RECENT ERRORS (30 menit terakhir) ──────────────────┐${NC}"
ERRORS=$(sudo docker logs --since 30m frigate 2>&1 | grep -iE "error|fail|exception" | grep -viE "no error|success" | tail -5)
if [ -n "$ERRORS" ]; then
    while IFS= read -r line; do
        echo -e "  ${RED}→${NC} ${GRAY}${line}${NC}"
    done <<< "$ERRORS"
else
    echo -e "  ${GREEN}✅ Tidak ada error dalam 30 menit terakhir${NC}"
fi
echo -e "${WHITE}${BOLD}└───────────────────────────────────────────────────────────┘${NC}"
echo ""

# ── FOOTER ───────────────────────────────────────────────────
IP_ADDR=$(hostname -I | awk '{print $1}')
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${WHITE}Web UI${NC}  : ${BOLD}http://${IP_ADDR}:5000${NC}                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${WHITE}API${NC}     : ${BOLD}http://${IP_ADDR}:5000/api/${NC}                     ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
