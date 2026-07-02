# Find_My_Web
<img width="2880" height="1620" alt="image" src="https://github.com/user-attachments/assets/d822bf41-079c-410b-817d-0066992fb5b7" />

Find_My_Web is a self-hosted, multi-provider location history and MQTT engine
for personal Bluetooth trackers. It collects reports from two independent
provider services:

- Apple Find My reports through
  [macless-haystack](https://github.com/dchristl/macless-haystack)
- Google Find Hub reports through
  [traccar/google-find-hub-sync](https://github.com/traccar/google-find-hub-sync),
  a fork of
  [GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools)

The application normalizes both providers into one append-only event store,
shows complete history on a Leaflet map, and publishes source-aware MQTT data
for Home Assistant.

> This project uses unofficial, reverse-engineered provider integrations. It is
> not affiliated with, endorsed by, or certified by Apple or Google. Use it
> only with trackers and accounts you own.

## Architecture

The three runtime stacks are deliberately independent:

```text
Apple devices
    |
    v
macless-haystack endpoint :6176 ----+
                                     |
Google devices                       v
    |                         Find_My_Web :8125
    v                                |
google-find-hub-sync :5500 ----------+----> SQLite history
                                     +----> MQTT / Home Assistant
```

Apple and Google do not share a Compose file, source directory, credentials, or
database. Find_My_Web contacts each provider over HTTP using the IP address and
port configured in the web interface.

## Features

- Apple, Google, and combined `All` map views
- Persistent map detail selector: latest positions only, or complete history
  with separate Apple and Google tracks
- One logical device with Apple, Google, or both provider identities
- Append-only SQLite time-series history with deduplication
- Absolute latest position across enabled providers
- Source badge, timestamp, received time, accuracy, and coordinates per point
- Google Maps and Apple Maps navigation links
- Persistent device names, colors, visibility, and provider mappings
- Apple-only P-224 key generation
- Google canonical device and advertisement-key import
- Selective JSON import and export
- Background polling while the browser is closed
- Raw MQTT event topics and Home Assistant MQTT discovery
- English and Italian interface with persistent language selection
- Responsive map, mobile bottom sheet, and collapsible device panel

## Important Key Distinction

Apple and Google use unrelated identities:

| Provider | Value stored by Find_My_Web | Firmware value |
|---|---|---|
| Apple | 28-byte P-224 private key in Base64 | Matching 28-byte advertisement key in Base64 |
| Google | Canonical device ID and optional public/advertisement value | 20-byte advertisement EID as 40 hexadecimal characters |

Every P-224 generation control in Find_My_Web is **Apple only**. Google
identities are created by GoogleFindMyTools or the Traccar fork and imported;
Find_My_Web never generates Google keys.

## Prerequisites

- A Linux server, ZimaOS, CasaOS, NAS, or other Docker host
- Docker Engine and Docker Compose
- Python 3 and an up-to-date Google Chrome installation on an x86-64 desktop
  for the one-time Google authentication
- An Apple account with two-factor authentication for macless-haystack
- A Google account with Find Hub offline finding enabled
- An MQTT broker only if MQTT or Home Assistant integration is required

Use dedicated provider accounts where practical. Both integrations are
unofficial and store credentials that can access location reports.

## Recommended Server Layout

```text
/DATA/AppData/apple-find-provider/
|-- docker-compose.yml
`-- Docker volumes managed by Compose

/DATA/AppData/google-find-hub-sync/
|-- Auth/
|   `-- secrets.json
|-- microservice.py
|-- requirements.txt
|-- docker-compose.yml
|-- .env
`-- remaining traccar/google-find-hub-sync files

/DATA/AppData/find-my-web/
|-- backend/
|-- web/
|-- data/
|-- docker-compose.yml
`-- .env
```

Changing these paths is supported. Keep all three directories separate.

## Part 1: Configure the Apple Provider

The upstream setup consists of an anisette service and the macless-haystack
endpoint. The included template is
[`deploy/apple-provider/docker-compose.yml`](deploy/apple-provider/docker-compose.yml).

### 1. Create the Apple stack directory

```bash
mkdir -p /DATA/AppData/apple-find-provider
cd /DATA/AppData/apple-find-provider
```

Copy `deploy/apple-provider/docker-compose.yml` into this directory.

### 2. Start anisette

```bash
docker compose up -d anisette
docker compose logs -f anisette
```

The service listens on port `6969` and persists its state in a Docker volume.

### 3. Run macless-haystack interactively

The first run must have an interactive terminal because it asks for the Apple
ID, password, and two-factor code:

```bash
docker compose run --rm macless-haystack
```

Complete the prompts. The authentication data is written to the persistent
`apple-auth` volume. When the endpoint reports that it is serving on port
`6176`, stop the temporary interactive container with `Ctrl+C`.

The upstream project currently documents SMS/text-message two-factor
authentication. A detached first run cannot read the prompts and normally ends
with `EOFError`.

### 4. Start the persistent endpoint

```bash
docker compose up -d macless-haystack
docker compose logs -f macless-haystack
```

Verify from another LAN machine:

```bash
curl http://SERVER_IP:6176
```

An HTTP response confirms network reachability. The exact response body is not
important at this stage.

### 5. Apple authentication troubleshooting

- `401 Unauthorized` from Apple's gateway usually means the stored
  macless-haystack authentication expired or was revoked.
- Stop only the Apple endpoint, rerun the interactive command, and then start
  it in the background again.
- Do not delete the entire Find_My_Web data directory. Apple authentication,
  Google authentication, and the event database are independent.
- If anisette cannot be resolved, confirm both Apple services are in the same
  Compose stack and that the service is named `anisette`.

## Part 2: Configure Google Find Hub

Google setup has two different secrets:

1. `Auth/secrets.json`: generated by the Chrome login flow and used to access
   the Google account.
2. `GOOGLE_TOKEN`: a random bearer token protecting the local HTTP sidecar.

They are not interchangeable.

> **Environment variable names:** both Compose templates use `GOOGLE_TOKEN`.
> The standalone Google sidecar maps it internally to the microservice's
> `AUTH_TOKEN`. Use the same token value in the sidecar and in Find_My_Web.

### 1. Enable Find Hub on Android

On an Android device logged into the Google account:

1. Open **Settings**.
2. Open **Google**.
3. Open **All services**.
4. Open **Find My Device** or **Find Hub**.
5. Enable offline finding using **With network in all areas** or
   **With network in high-traffic areas only**.

If the offline option is absent, install Google's Find Hub / Find My Device app.
The upstream project notes that some accounts need a real compatible tracker
paired once before the offline network becomes available.

### 2. Authenticate on a desktop with Chrome

The Traccar repository contains the sidecar used by Find_My_Web and is based on
GoogleFindMyTools:

```bash
git clone https://github.com/traccar/google-find-hub-sync.git
cd google-find-hub-sync
python -m venv .venv
```

Activate the environment:

```bash
# Linux or macOS
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

Install dependencies and start the tool:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

Complete the Chrome authentication. The resulting credential file is:

```text
Auth/secrets.json
```

The upstream projects do not support the initial authentication flow on ARM
Linux. Generate this file on an x86-64 desktop with current Python and Chrome,
then copy it to the server. Chrome is not required by the running sidecar.

### 3. Register a custom Google tracker

When `main.py` displays the device list:

1. Press `r`.
2. Follow the registration prompts.
3. Save the canonical device identity created in the account.
4. Copy the 20-byte advertisement key/EID printed by the tool.

The firmware expects that EID as exactly 40 hexadecimal characters without
`0x`, commas, or spaces.

### 4. Copy the Traccar repository to the server

Copy the complete authenticated checkout to:

```text
/DATA/AppData/google-find-hub-sync
```

Confirm this file exists on the server:

```text
/DATA/AppData/google-find-hub-sync/Auth/secrets.json
```

Never commit `Auth/secrets.json`.

### 5. Install the standalone Google Compose template

Copy these repository files into the Google checkout:

```text
deploy/google-provider/docker-compose.yml
deploy/google-provider/.env.example
```

Rename the environment template:

```bash
cd /DATA/AppData/google-find-hub-sync
cp .env.example .env
```

Generate the local API bearer token:

```bash
openssl rand -hex 32
```

Place it in `.env`:

```dotenv
GOOGLE_TOKEN=paste-the-generated-random-token
GOOGLE_APP_ROOT=/DATA/AppData/google-find-hub-sync
```

### 6. Start and verify the Google sidecar

```bash
docker compose up -d
docker compose logs -f
```

Test authentication:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://SERVER_IP:5500/devices
```

A successful response contains a `devices` array. A `401` means the bearer
token sent by the client does not match `AUTH_TOKEN`. Authentication failures
inside the Google tooling instead indicate a stale or invalid
`Auth/secrets.json`.

Keep port `5500` private to the LAN.

## Part 3: Deploy Find_My_Web

### 1. Copy the application

Copy this repository to:

```text
/DATA/AppData/find-my-web
```

The directory must contain `backend`, `web`, `docker-compose.yml`, and
`.env.example`.

### 2. Create the persistent data directory

```bash
mkdir -p /DATA/AppData/find-my-web/data
cd /DATA/AppData/find-my-web
cp .env.example .env
```

### 3. Configure provider URLs

Edit `.env`:

```dotenv
APPLE_ENDPOINT_URL=http://SERVER_IP:6176
APPLE_ENDPOINT_USER=
APPLE_ENDPOINT_PASS=
APPLE_HISTORY_DAYS=7

GOOGLE_ENDPOINT_URL=http://SERVER_IP:5500
GOOGLE_TOKEN=

APP_ROOT=/DATA/AppData/find-my-web
```

Use the Docker host's real LAN address. Docker service names from one Compose
stack do not resolve inside another independent stack unless an external Docker
network has been configured.

`GOOGLE_TOKEN` is optional in the Find_My_Web `.env`. It can instead be
saved from the web interface. The Google sidecar still requires
`GOOGLE_TOKEN` in its own `.env`; use the same value in both places.

### 4. Start the application

```bash
docker compose up -d
docker compose logs -f
```

Open:

```text
http://SERVER_IP:8125
```

The Compose template uses an official Python image and bind-mounted source, so
ZimaOS and CasaOS do not need to build a custom image.

### 5. Configure providers from the UI

Open **Provider connections**:

- Apple host: server IP or DNS name
- Apple port: `6176`
- Google host: server IP or DNS name
- Google port: `5500`
- Google API bearer token: the same token used by the standalone Google sidecar

These values are stored in `/data/settings.json` and take effect on the next
manual or background refresh. They change where Find_My_Web connects; they do
not change Docker's published ports. A token entered in the UI is stored
server-side with restricted file permissions. The API reports only whether a
token exists and never returns its value to the browser. A saved UI token takes
precedence over the environment default.

## Part 4: Add Trackers

### Apple-only device

1. Open **Generate Apple P-224 keys**, or use a compatible OpenHaystack
   generator.
2. Store the private key securely.
3. Paste the private key into **Add a device**.
4. Copy the displayed Base64 advertisement key into the Apple firmware field.
5. Flash the tracker.

The private key decrypts reports and must never be placed in firmware. The
advertisement key is public and is intentionally broadcast over BLE.

After generating a key, Find_My_Web asks whether to create a new logical device.
Declining leaves the generated values available for copying. You can also choose
an existing device and apply the generated Apple identity to it. To replace an
existing identity, open its **Apple P-224 advertisement key** section. The UI
shows the current advertisement string and lets you enter the replacement
advertisement plus its matching private key. The backend verifies the pair
before saving it. Replacing only the public key would make report decryption
impossible and is therefore rejected.

### Google-only device

1. Register the tracker with `main.py` by pressing `r`.
2. Paste the 40-character advertisement EID into the Google firmware field.
3. Flash the tracker.
4. In Find_My_Web, add a device and select or paste its canonical Google device
   ID.
5. Store the Google advertisement EID in the device's **Google advertisement
   EID** field. Find_My_Web then keeps the exact firmware string with the
   logical device.

### Dual-provider device

Create one logical device and configure both:

- Apple private key
- Google canonical device ID
- Google advertisement EID

Apple and Google reports remain separate events in SQLite. The `All` view sorts
both sources together only for display. The latest event by device timestamp is
used as the combined Home Assistant position.

Use the **Map** selector above the device list to choose:

- **Latest positions**: one current marker per selected provider and device.
- **All positions + tracks**: every retained point plus chronological provider
  tracks. Google tracks are dashed; Apple tracks are solid.

## MQTT and Home Assistant

Open **MQTT / Home Assistant** and configure:

- broker host and port
- optional username and password
- base topic, default `Find_My_Web`
- background refresh interval

For each logical device, enable Apple MQTT, Google MQTT, or both.

Each new event is published to:

```text
Find_My_Web/events/<device_id>/<source>
```

Payload schema:

```json
{
  "device_id": "logical-device-id",
  "tracker_id": "provider-specific-id",
  "source": "apple",
  "latitude": 45.4642,
  "longitude": 9.1900,
  "accuracy": 12,
  "timestamp": 1782921000000,
  "received_at": 1782921010000
}
```

Home Assistant MQTT discovery creates:

- one Apple `device_tracker` when Apple MQTT is enabled
- one Google `device_tracker` when Google MQTT is enabled
- one combined `device_tracker` using the newest enabled source

Publishing and refresh run in the backend even when no browser is open.

## Persistent Data and Backups

| Path | Contents |
|---|---|
| `/data/tags.json` | logical devices, provider identities, names, colors, visibility, MQTT selections |
| `/data/settings.json` | provider addresses, Google bearer token, refresh interval, and MQTT configuration |
| `/data/events.db` | normalized append-only position history |

Back up the complete `/DATA/AppData/find-my-web/data` directory. It contains
Apple private keys and location history.

Also back up separately:

- Apple provider authentication volume
- Google `Auth/secrets.json`
- each Google advertisement EID

## Import and Export

The frontend can export all devices or a selected subset as JSON. Imports show
a selection dialog before writing anything. Exports can contain Apple private
keys, so handle them as credentials rather than ordinary configuration files.

## HTTP API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/config` | provider status and effective endpoint URLs |
| `GET` | `/api/settings` | persistent UI settings without passwords |
| `POST` | `/api/settings` | update provider, refresh, and MQTT settings |
| `GET` | `/api/devices` | logical devices with combined history |
| `GET` | `/api/devices?source=apple` | Apple-only history |
| `GET` | `/api/devices?source=google` | Google-only history |
| `POST` | `/api/devices` | create a logical device |
| `PATCH` | `/api/devices/<id>` | update identity, name, color, visibility, or MQTT selection |
| `DELETE` | `/api/devices/<id>` | permanently delete a device and its history |
| `GET` | `/api/events` | query normalized time-series events |
| `POST` | `/api/refresh` | poll both configured providers immediately |
| `POST` | `/api/generate` | generate an Apple-only P-224 key pair |
| `GET` | `/api/google/devices` | list devices returned by the Google sidecar |
| `GET` | `/api/export` | export device configuration |

## Updating

1. Back up all persistent data and provider credentials.
2. Stop only the stack being updated.
3. Replace its source files or pull the repository.
4. Start that stack again.
5. Check its logs and then trigger a manual refresh.

Updating Find_My_Web does not require recreating either provider container.
Updating a provider does not require deleting `events.db`.

## Troubleshooting

### Reports exist but no positions are shown

- Confirm the correct Apple private key or Google canonical ID is assigned.
- Check provider logs before changing the event database.
- Verify that timestamps and coordinates are present in `/api/events`.

### Google device list is empty

- Test `/devices` directly with the bearer token.
- Confirm `Auth/secrets.json` exists inside the Google container.
- Confirm offline finding is enabled on the Google account.
- Re-run desktop authentication if the credential file was revoked.

### Apple returns 401

Re-authenticate only the macless-haystack endpoint interactively. Do not delete
Find_My_Web or Google data.

### A container cannot reach a provider by service name

The stacks are intentionally separate. Use `http://SERVER_IP:PORT`, or create
and attach an explicit external Docker network.

### ZimaOS reports `/root/.docker` as read-only

Use the graphical stack installer or the supplied no-build Compose templates.
All application state is bind-mounted under `/DATA/AppData`.

## Development and Verification

```bash
python -m pip install -r backend/requirements.txt
python -m unittest discover -s tests -v
python backend/server.py
```

Then open `http://127.0.0.1:8000`.

The integration test encrypts a real Apple report, mocks a Google report,
refreshes both providers, and verifies normalized SQLite events, source
filtering, configurable endpoint routing, and MQTT payloads.

## Security

- Never commit `.env`, `/data`, `Auth/secrets.json`, Apple private keys, or
  exported tracker JSON files.
- Treat `/data/settings.json` as a credential because it can contain the Google
  sidecar bearer token.
- Keep ports `6176`, `5500`, `6969`, and `8125` on a trusted LAN or behind an
  authenticated reverse proxy.
- Use a long random Google bearer token.
- Do not expose the Google sidecar directly to the internet.
- Use these tools only for devices and property you own.

## Credits and Licensing

Find_My_Web is licensed under the [MIT License](LICENSE). Third-party projects
retain their own licenses:

- [macless-haystack](https://github.com/dchristl/macless-haystack), AGPL-3.0
- [OpenHaystack](https://github.com/seemoo-lab/openhaystack), Apple network research
- [GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools), GPL-3.0
- [traccar/google-find-hub-sync](https://github.com/traccar/google-find-hub-sync), GPL-3.0
- [Leaflet](https://leafletjs.com/), map rendering
- [OpenStreetMap](https://www.openstreetmap.org/), map data
