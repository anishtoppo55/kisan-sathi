# Deploying Kisan Saathi CG on AWS Lightsail

Browsers only allow the microphone over HTTPS, so the app sits behind Caddy, which gets a free
Let's Encrypt certificate for `<static-ip>.sslip.io`. No domain purchase needed.

## 1. Create the instance (Lightsail console)
1. Lightsail → Create instance → Linux/Unix → **OS only → Ubuntu 24.04 LTS**.
2. Region: Mumbai (`ap-south-1`) for low latency to Indian users.
3. Plan: 1 GB RAM is enough (all AI runs on remote APIs); 2 GB gives headroom.
4. Networking → **Create static IP** and attach it to the instance.
5. Networking → IPv4 firewall: keep SSH (22), add **HTTP (80)** and **HTTPS (443)**.

## 2. Push the code to a git remote
The server pulls from git. Push this repo to a private GitHub repo (`.env` is git-ignored).
For a private repo, use a deploy key or a fine-grained token on the server.

## 3. First-time setup
From your laptop (replace `IP` and the key path):

```bash
ssh -i ~/LightsailDefaultKey-ap-south-1.pem ubuntu@IP 'git clone <REPO_URL> kisan-saathi'
scp -i ~/LightsailDefaultKey-ap-south-1.pem .env ubuntu@IP:/home/ubuntu/kisan-saathi/.env
ssh -i ~/LightsailDefaultKey-ap-south-1.pem ubuntu@IP 'bash kisan-saathi/deploy/setup_server.sh <REPO_URL> IP'
```

The script installs python3-venv, ffmpeg and Caddy, creates the venv, sets `.env` to mode 600,
installs the systemd service and configures Caddy. It prints the URL, e.g. `https://13-233-10-20.sslip.io`.

## 4. Updating
```bash
git push            # from your laptop
ssh ubuntu@IP 'bash kisan-saathi/deploy/deploy.sh'
```

## 5. Checks
```bash
curl https://<ip-dashed>.sslip.io/health                 # {"status":"ok"}
ssh ubuntu@IP 'journalctl -u kisan-saathi -n 50'         # app logs
ssh ubuntu@IP 'sudo journalctl -u caddy -n 50'           # certificate / proxy logs
```
Then open the URL on your phone, tap the mic and ask a question.

## Notes
- One uvicorn worker on purpose: session memory, TTS cache and the rate limiter are in-process.
- Rate limit: 20 `/api/*` requests per minute per IP (`RATE_LIMIT_PER_MINUTE` in `.env`).
- Uvicorn trusts `X-Forwarded-For` only from Caddy on 127.0.0.1, so the limit applies per real client IP.
