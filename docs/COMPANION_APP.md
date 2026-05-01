# Nina Companion (tablet) + Jetson link daemon

This document describes the **Android companion app** under [`android/`](../android/README.md) and the Python **nina-link daemon** under [`nina/link_daemon/`](../nina/link_daemon/).

## Jetson: one-shot install + diagnosis (recommended)

From the repo root on the Jetson (after `git clone` / copy):

```bash
chmod +x scripts/install-nina-link-jetson.sh
./scripts/install-nina-link-jetson.sh --smoke
# Optional: register systemd (needs sudo)
sudo ./scripts/install-nina-link-jetson.sh --with-systemd
```

This creates **`.venv-link`**, installs **`requirements-link.txt`**, verifies imports, checks **`nmcli`/NetworkManager**, and with **`--smoke`** briefly runs the daemon and curls **`/health`**.

---

## Jetson: manual install and run

```bash
cd /path/to/nina-app
python3 -m venv .venv-link && source .venv-link/bin/activate
export PYTHONPATH=.
pip install -r requirements-link.txt
python -m nina.link_daemon.main
```

Environment variables (see [`nina/link_daemon/config.py`](../nina/link_daemon/config.py)):

| Variable | Meaning |
|----------|---------|
| `NINA_LINK_HOST` | Bind address (default `0.0.0.0`) |
| `NINA_LINK_PORT` | HTTP port (default `8787`) |
| `NINA_LINK_AP_SSID` / `NINA_LINK_AP_PASSWORD` | Hotspot credentials when using `nmcli device wifi hotspot` |
| `NINA_LINK_AP_WAIT_SEC` | Boot “window” indicator for status JSON (default 30) |
| `NINA_LINK_BOOT_AP` | If `1` (default), start hotspot on boot unless `user_mode=force_sta` and saved profiles exist |
| `NINA_LINK_TOKEN` | If set, remote clients must send `Authorization: Bearer <token>` for mutating calls (localhost always trusted) |
| `NINA_LINK_MOCK` | If `1`, simulate Wi-Fi (for laptops without NetworkManager) |

Systemd example: [`nina/systemd/nina-link.service`](../nina/systemd/nina-link.service).

## Sirena UI on the robot

Settings → **Network** talks to `http://127.0.0.1:8787` by default (override with `NINA_LINK_URL`). Use **Apply mode** for AP vs home Wi-Fi behavior aligned with the tablet app.

## Tablet: APK flow

1. Join the Jetson **access point** (SSID/password match daemon / Jetson screen).
2. Open **Nina Companion**; default API base URL is `http://192.168.4.1:8787` (typical NM hotspot gateway — adjust on Setup if different).
3. **Save home Wi‑Fi** credentials on the Jetson (sends them to NetworkManager via the daemon).
4. **Connect Jetson to home Wi‑Fi** (STA), then use **Open Android Wi‑Fi settings** to join the **same** SSID on the tablet. Android does not allow silent Wi‑Fi switching; this step is intentional.
5. Change the app **Setup** URL to the Jetson’s new LAN address (or mDNS later) and **Save & test connection**.

### Edge cases

- **Wrong Wi‑Fi password**: Jetson returns an error string; fix credentials and retry.
- **Tablet on AP, Jetson on home**: Status requests fail — switch the tablet Wi‑Fi first.
- **401 Unauthorized**: Set `NINA_LINK_TOKEN` on the Jetson and paste the same token under Setup, or **Pair with PIN** (PIN is visible only on localhost status in Sirena Settings → Network on the Jetson).

## REST API (summary)

- `GET /health` — liveness.
- `GET /v1/status` — Wi‑Fi role, IPv4, saved profiles, errors, boot timer fields.
- `POST /v1/mode` — `{ "mode": "boot_default" | "force_ap" | "force_sta" }`.
- `POST /v1/wifi/home-credentials` — `{ "ssid", "password" }`.
- `POST /v1/wifi/connect-home` — optional `?ssid=`.
- `POST /v1/wifi/start-ap` — bring up hotspot.
- `DELETE /v1/wifi/saved/{id_or_uuid}` — remove saved NM profile.
- `POST /v1/pair` — `{ "pin" }` → `{ "token" }` for session bearer.

Drive commands remain **preview** until wired to `NinaService`; see `GET /v1/robot/capabilities`.
