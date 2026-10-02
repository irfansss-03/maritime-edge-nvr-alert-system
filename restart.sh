#!/bin/bash
# Quick restart script for Frigate NVR
echo "Restarting Frigate NVR..."
cd /home/nvrpc/frigate
sudo docker compose down
sudo docker compose up -d
echo "Waiting for startup..."
sleep 10
sudo docker compose ps
echo ""
echo "Frigate restarted! Check logs with: sudo docker logs -f frigate"
