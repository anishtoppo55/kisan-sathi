#!/usr/bin/env bash
# Update the running app. Run ON THE SERVER:  bash deploy/deploy.sh
set -euo pipefail
cd /home/ubuntu/kisan-saathi
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart kisan-saathi
sleep 2
curl -fsS http://127.0.0.1:8000/health && echo " <- app healthy"
