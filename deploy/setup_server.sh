#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 24.04 Lightsail instance. Run ON THE SERVER as user ubuntu:
#   bash deploy/setup_server.sh <REPO_URL> <STATIC_IP> [EXTRA_DOMAIN]
# EXTRA_DOMAIN (optional): your own name pointing at STATIC_IP, e.g. kisan-saathi.duckdns.org
# Example:
#   bash deploy/setup_server.sh https://github.com/anishtoppo55/kisan-sathi.git 35.154.167.239 kisan-saathi.duckdns.org
set -euo pipefail

REPO_URL="${1:?repo url required}"
STATIC_IP="${2:?static ip required}"
APP_DIR=/home/ubuntu/kisan-saathi
SITE_HOST="${STATIC_IP//./-}.sslip.io"   # dashed form also resolves on sslip.io
EXTRA_DOMAIN="${3:-}"
SITE_HOSTS="$SITE_HOST${EXTRA_DOMAIN:+, $EXTRA_DOMAIN}"

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ffmpeg git debian-keyring debian-archive-keyring apt-transport-https curl

# Caddy (official apt repo)
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update && sudo apt-get install -y caddy
fi

# App
[ -d "$APP_DIR/.git" ] || git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo "!! Copy your .env first:  scp .env ubuntu@$STATIC_IP:$APP_DIR/.env  then re-run."
  exit 1
fi
chmod 600 .env

# SearXNG (free web search) in Docker, reachable only from this machine on 127.0.0.1:8888
if ! command -v docker >/dev/null; then
  sudo apt-get install -y docker.io
  sudo systemctl enable --now docker
fi
sudo mkdir -p /etc/searxng
if [ ! -f /etc/searxng/settings.yml ]; then
  sed "s|__SEARXNG_SECRET__|$(openssl rand -hex 32)|" deploy/searxng/settings.yml | sudo tee /etc/searxng/settings.yml >/dev/null
fi
if ! sudo docker ps -a --format '{{.Names}}' | grep -qx searxng; then
  sudo docker run -d --name searxng --restart unless-stopped -p 127.0.0.1:8888:8080 \
    -v /etc/searxng/settings.yml:/etc/searxng/settings.yml:ro searxng/searxng:latest
fi

sudo cp deploy/kisan-saathi.service /etc/systemd/system/kisan-saathi.service
sudo systemctl daemon-reload
sudo systemctl enable --now kisan-saathi

sed "s|{\$SITE_HOSTS}|$SITE_HOSTS|" deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl reload caddy || sudo systemctl restart caddy

echo "Done. Open: https://${EXTRA_DOMAIN:-$SITE_HOST}  (also https://$SITE_HOST)"
