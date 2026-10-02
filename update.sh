#!/bin/bash
# Update Frigate NVR to latest stable
echo "Updating Frigate NVR..."
cd /home/nvrpc/frigate
echo "1. Pulling latest image..."
sudo docker compose pull
echo "2. Restarting with new image..."
sudo docker compose up -d
echo "3. Cleaning old images..."
sudo docker image prune -f
echo "4. Checking version..."
sleep 15
curl -sf http://localhost:5000/api/version
echo ""
echo "Update complete!"
