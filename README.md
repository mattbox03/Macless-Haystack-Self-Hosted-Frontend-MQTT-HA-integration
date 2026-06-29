# FindMy Web — Self-Hosted Find My Tracker

A privacy-friendly, self-hosted web app to **track your DIY [OpenHaystack](https://github.com/seemoo-lab/openhaystack) / [macless-haystack](https://github.com/dchristl/macless-haystack) tags** on a beautiful map — with full position history, key management, Home Assistant integration, and more.

It decrypts Apple's *Find My* location reports **on your own server** and shows them on an interactive map. Your private keys never leave your machine.

> **Disclaimer** — This is an independent project and is **not affiliated with, authorized, or endorsed by Apple Inc.** "Find My" and "AirTag" are trademarks of Apple Inc. Use it only with hardware and accounts you own, and in compliance with your local laws.

---

## ✨ Features

- 🗺️ **Interactive map** (Leaflet) with the full location history and track of every tag.
- 🕓 **History navigator** — step through positions from newest to oldest, with timestamp and coordinates.
- 🔓 **Server-side decryption** — P-224 ECDH → ANSI X9.63 KDF → AES-128-GCM, done in Python. No fragile browser crypto, no CORS, no mixed-content.
- ♻️ **Background auto-refresh** — the server keeps pulling reports on a schedule, so history accumulates (well beyond Apple's ~7-day window) even when the site is closed.
- 🔑 **Key management** — add a tag from its private key, or generate a fresh P-224 key pair and copy the public key ready to paste into firmware (C array **and** Base64).
- 📍 **Share & navigate** — click any position to see its coordinates and open **Google Maps** or **Apple Maps** directions.
- 🏠 **Home Assistant integration** — publishes each tag (name + last position) over **MQTT discovery** as a `device_tracker`.
- 👁️ **Hide devices** without deleting their data, and 🎨 **custom color per device** — all persistent.
- 🌍 **Multi-language** (English / Italian) with a persistent choice.
- 📱 **Mobile friendly** — responsive layout with a collapsible panel.
- ⬇️⬆️ **Import / Export** tags as JSON (with per-tag selection) for backup or migration.
- 🔒 **Protected delete**, accuracy circles, relative timestamps, and live UI auto-refresh.

---

## 🧩 How it works

```
┌──────────┐     ┌──────────────────────────────----┐       ┌───────────────────────┐     ┌───────┐
│  Browser │ ──► │Self-Hosted-Frontend(this project)│ ──►   │  macless-haystack     │ ──► │ Apple │
│  (map)   │ ◄── │  config · tags · history ·       │ ◄──   │  endpoint (:6176)     │ ◄── │ servers│
└──────────┘     │  DECRYPTION · MQTT publish       │       └───────────────────────┘     └───────┘
                 └──────────────────────────────----┘
```

FindMy Web sits **on top of** a running macless-haystack endpoint. It sends the *hashed* public keys of your tags to that endpoint, receives the **encrypted** location reports Apple collected, and decrypts them locally with each tag's **private key**.

### End-to-end encryption, in short

Every tag uses one elliptic-curve (P-224) key pair:

| Key | Size | Who holds it | Purpose |
|-----|------|--------------|---------|
| **Private key** | 28 B | only your server | **decrypts** the locations |
| **Advertisement (public) key** | 28 B | broadcast by the device | nearby iPhones use it to **encrypt** |
| **Hashed key** | 32 B (SHA-256 of public) | sent to Apple as a lookup id | indexes the encrypted reports |

A passing iPhone encrypts the location with your **public** key; Apple stores the ciphertext but **cannot read it**; only you, with the **private** key, can decrypt it. Apple is a blind relay.

---

## 📋 Prerequisites

- A working **[macless-haystack](https://github.com/dchristl/macless-haystack)** endpoint (anisette + endpoint, usually on port `6176`). FindMy Web does **not** replace it — it queries it.
- One or more DIY Find My tags (e.g. ESP32 / nRF52 running OpenHaystack-style firmware).
- **Docker** (recommended) or Python 3.11+.

---

## 🚀 Quick start (Docker Compose)

```yaml
# docker-compose.yml
services:
  findmy-web:
    image: python:3.12-slim
    container_name: findmy-web
    working_dir: /app
    command: sh -c "pip install --no-cache-dir -r requirements.txt && python server.py"
    ports:
      - "8000:8000"                       # http://<host>:8000
    environment:
      - ENDPOINT_URL=http://<macless-haystack-host>:6176   # ← the only required setting
    volumes:
      - ./backend:/app                    # server.py + requirements.txt
      - ./web:/web                        # index.html + leaflet.*
      - ./data:/data                      # tags + history + settings (persistent)
    restart: unless-stopped
```

```bash
git clone https://github.com/<you>/findmy-web.git
cd findmy-web
# edit ENDPOINT_URL in docker-compose.yml
docker compose up -d
```

Then open **`http://<host>:8000`**.

> Running without Docker: `pip install -r backend/requirements.txt` then
> `ENDPOINT_URL=http://<host>:6176 WEB_DIR=web DATA_DIR=data python backend/server.py`.

---

## ⚙️ Configuration

Most options have sensible defaults. **MQTT and the refresh interval are configured from the web UI** (and stored in `data/settings.json`); everything else is set via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ENDPOINT_URL` | — | **Required.** URL of your macless-haystack endpoint. |
| `ENDPOINT_USER` / `ENDPOINT_PASS` | — | Optional Basic Auth for the endpoint. |
| `DAYS` | `7` | Days of reports to request from Apple (max ~7). |
| `RETENTION_DAYS` | `21` | How long to keep local history. |
| `HISTORY_CAP` | `10000` | Max stored points per tag. |
| `REFRESH_INTERVAL` | `1800` | Default background refresh interval (seconds). |
| `WEB_DIR` / `DATA_DIR` | `/web` / `/data` | Paths inside the container. |
| `MQTT_HOST` `MQTT_PORT` `MQTT_USER` `MQTT_PASS` `MQTT_BASE` | — | Optional MQTT defaults (also editable in the UI). |

---

## 🔑 Adding tags & generating keys

1. **Add a tag** → paste its **private key** (Base64). The server derives the hashed key to query Apple; the private key stays on your server and is only used to decrypt.
2. **Generate a new key** → get a fresh P-224 pair: the **private key** (to add as a tag) and the **public/advertisement key** as a ready-to-paste **C array** and **Base64** string for your firmware.
3. Flash the **advertisement key** into your device firmware, power it on, and wait — positions appear once nearby iPhones report the beacon (minutes to hours).

Use **Import / Export** to back up or move tags between instances (JSON, with per-tag selection).

---

## 🏠 Home Assistant (MQTT)

Enable MQTT in **⚙ MQTT / Home Assistant** in the UI (broker host/port, optional credentials, base topic, update interval). The server then publishes each tag via **MQTT discovery**:

- Discovery: `homeassistant/device_tracker/findmy_<id>/config`
- Attributes: `<base>/<id>/attributes` → `{ latitude, longitude, gps_accuracy, last_seen }`

Your tags show up automatically in Home Assistant as `device_tracker` entities with their last position on the map. Make sure HA's MQTT integration points to the **same broker** (discovery is on by default). Publishing runs on the server, so it keeps working with the site closed.

---

## 💾 Data & persistence

All state lives in the mounted `data/` directory:

- `data/tags.json` — tags (private key, name, color, hidden flag) and their position history.
- `data/settings.json` — MQTT and refresh settings configured from the UI.

Back it up by copying that folder.

---

## 🔒 Security & privacy

- **Private keys never leave your server** — keep `data/tags.json` safe and don't share it.
- The decryption is end-to-end: Apple and the network only ever see ciphertext.
- This app has **no authentication** of its own. Run it on a **trusted LAN** or behind a reverse proxy / VPN if you expose it.

---

## 🗂️ Project structure

```
findmy-web/
├── backend/
│   ├── server.py          # Flask API + P-224 decryption + history + MQTT
│   └── requirements.txt
├── web/
│   ├── index.html         # single-page UI (vanilla JS)
│   ├── leaflet.js         # vendored (no CDN)
│   └── leaflet.css
└── docker-compose.yml
```

---

## 🛠️ Tech stack

- **Backend:** Python, Flask, `cryptography`, `paho-mqtt`, served by Waitress.
- **Frontend:** vanilla JavaScript + [Leaflet](https://leafletjs.com/) (self-hosted), no build step.
- **Map tiles:** OpenStreetMap (requires internet); all libraries are local.

---

## 🤝 Contributing

Issues and pull requests are welcome. Adding a new UI language is easy — just add a block to the `I18N` dictionary in `web/index.html`.

## 📄 License

Released under the **MIT License**. See [`LICENSE`](LICENSE).

## 🙏 Acknowledgments

- [OpenHaystack](https://github.com/seemoo-lab/openhaystack) by the Secure Mobile Networking Lab.
- [macless-haystack](https://github.com/dchristl/macless-haystack) by dchristl.
- [Leaflet](https://leafletjs.com/) and [OpenStreetMap](https://www.openstreetmap.org/) contributors.
