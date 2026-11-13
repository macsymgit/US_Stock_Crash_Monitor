# Deploying to a Linux server with Docker Compose

This guide runs both services on a Linux host:

- `monitor`: the Streamlit dashboard, on port 8082.
- `notifier`: sends the weekday Telegram risk report at `NOTIFY_TIME`, in New York time.

Both services use the same image. The repository folder is mounted into the containers at `/app`. As a result, code and `.env` changes are read from the server's checkout, and `risk_history.csv` persists on the host.

## 1. Before you start

- **Push your code.** The server deploys from Git, so commit and push the notifier changes to `origin` first.
- **Check network access.** The server must reach yfinance/Yahoo, CNBC, Eastmoney, GuruFocus, Multpl, CNN, `api.stlouisfed.org` and `api.telegram.org` directly. The notifier has no proxy setting. A server where Telegram or Yahoo is blocked can't send reports or score the technical factor.
- **Check the server clock.** Run `timedatectl` and confirm `System clock synchronized: yes`. The server's time zone doesn't matter, because the notifier always uses New York time.

## 2. Install Docker

Use Docker Engine with the Compose plugin. For Debian/Ubuntu:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker      # start now and on every boot
sudo usermod -aG docker "$USER"         # optional: run docker without sudo (log out and back in)
docker compose version                  # must print v2.x
```

## 3. Get the code

```bash
git clone https://github.com/<your-account>/US_Stock_Crash_Monitor.git
cd US_Stock_Crash_Monitor
```

## 4. Create `.env`

`.env` isn't in Git, and `.dockerignore` keeps it out of the image. Copy it from your laptop:

```bash
# run on the laptop
scp .env user@server:~/US_Stock_Crash_Monitor/.env
```

Alternatively, create it from `.env.example` on the server. Then set the file permissions and the server-only setting:

```bash
chmod 600 .env
echo 'MONITOR_BIND=127.0.0.1' >> .env   # dashboard only reachable from the server itself (see step 7)
```

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | for `notifier` | Where reports are sent. Without them the notifier exits at startup. |
| `NOTIFY_TIME` | no, default `10:00` | Weekday send time, `HH:MM`, New York time |
| `FRED_API_KEY` | no | Credit-spread factor and Treasury fallback. Without it, credit is missing and every report is marked provisional and not saved. |
| `MONITOR_BIND` | no, default `0.0.0.0` | Host address the dashboard port binds to |

## 5. Optional: bring your history

`risk_history.csv` isn't in Git either. To keep the trend you built up on the laptop:

```bash
# run on the laptop
scp risk_history.csv user@server:~/US_Stock_Crash_Monitor/
```

## 6. Start

```bash
docker compose up -d --build
docker compose ps                       # monitor: healthy, notifier: Up
docker compose logs --tail 5 notifier   # expect: 下次扫描 YYYY-MM-DD 10:00 EST
```

Send one report now to confirm Telegram works. This also saves the current reading to history:

```bash
docker compose exec notifier python notifier.py --now
```

**Stop the laptop's notifier** once the server is running, or you'll get two reports every weekday:

```bash
# run on the laptop
docker compose stop notifier
```

## 7. Reach the dashboard

With `MONITOR_BIND=127.0.0.1`, the dashboard isn't exposed to the network. Open it through an SSH tunnel:

```bash
# run on the laptop, then browse http://localhost:8082
ssh -N -L 8082:127.0.0.1:8082 user@server
```

The dashboard has no login. If you want it public, keep `MONITOR_BIND=127.0.0.1` and put a reverse proxy with authentication and HTTPS in front of it, such as Caddy or nginx. Setting `MONITOR_BIND=0.0.0.0` publishes it to anyone who can reach the server. Docker's published ports bypass `ufw` rules, so the firewall won't block it.

## Day-to-day

| Task | Command |
|---|---|
| Update to the latest code | `git pull && docker compose up -d --build` |
| Change `NOTIFY_TIME` or other `.env` values | edit `.env`, then `docker compose up -d` (not `restart`, which keeps the old values) |
| Follow notifier logs | `docker compose logs -f notifier` |
| Stop everything | `docker compose down` |
| Back up history | copy `risk_history.csv` |

Both containers use `restart: unless-stopped`, and Docker is enabled at boot, so the services come back after a reboot. If you stopped them with `docker compose down` or `stop`, they stay stopped.

## Notes

- **History file ownership.** The containers run as root, so `risk_history.csv` becomes root-owned after the first write. Reading and copying it still work. Use `sudo` to edit or delete it.
- **File watcher setting.** `STREAMLIT_SERVER_FILE_WATCHER_TYPE: poll` in `docker-compose.yml` is there for Windows hot reload. It's harmless on Linux.
- **Holidays and missed runs.** Reports still go out on market holidays. A run more than 30 minutes late is skipped, which matters less on a server that never sleeps.
