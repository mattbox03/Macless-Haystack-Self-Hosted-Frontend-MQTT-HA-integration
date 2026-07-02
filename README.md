# Find_My_Web

Find_My_Web is a self-hosted, multi-provider location-history engine for:

- Apple Find My reports through
  [macless-haystack](https://github.com/dchristl/macless-haystack) and
  [anisette-v3-server](https://github.com/Dadoum/anisette-v3-server)
- Google Find Hub reports through
  [traccar/google-find-hub-sync](https://github.com/traccar/google-find-hub-sync)
- MQTT and Home Assistant
- a responsive Leaflet map with chronological history
- a low-power nRF52832 firmware that broadcasts Apple and Google frames
  simultaneously

The repository is designed for people who do not want to manually assemble
four unrelated container projects. One Compose project builds and connects the
services, while each provider remains isolated in its own container.

This repository does not copy the macless-haystack, anisette, or
GoogleFindMyTools source trees. Compose downloads their published images or
clones their public repository during the image build. Their original projects,
authors, copyright notices, and licenses remain independent. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

> This project is unofficial and experimental. It is not affiliated with,
> endorsed by, or certified by Apple or Google. Use it only to locate property
> you own or are authorized to track.

## What Is Included

| Service | Container | Purpose |
|---|---|---|
| Anisette | `find-my-web-anisette` | Apple authentication metadata |
| macless-haystack | `find-my-web-apple` | Apple report endpoint |
| Google sidecar | `find-my-web-google` | Google authentication, FCM, and location decryption |
| Find_My_Web | `find-my-web` | Event storage, map, MQTT, and provider polling |

Only the web interface is exposed by default:

```text
http://SERVER_IP:8125
```

The providers communicate over a private Docker network:

```text
anisette:6969 <--- macless-haystack:6176 ---+
                                             |
                                             +---> Find_My_Web:8000
                                             |
google-provider:5500 ------------------------+
```

Apple credentials, Google credentials, provider data, and the Find_My_Web
database are stored separately.

## Requirements

### Server

- Linux, ZimaOS, CasaOS, a NAS, or another Docker host
- Docker Engine
- Docker Compose v2 (`docker compose`)
- internet access while images and source dependencies are downloaded

### One-time Google authentication computer

- x86-64 Windows, macOS, or Linux desktop
- Python 3
- current Google Chrome

GoogleFindMyTools does not currently support performing its authentication flow
on ARM Linux. The resulting `Auth/secrets.json` can still be copied to and used
by an ARM server.

### Optional tracker tools

- nRF52832 module with exposed `SWDIO`, `SWCLK`, `VDD`, and `GND`
- CMSIS-DAP/DAPLink or J-Link programmer
- Visual Studio Code with PlatformIO, or PlatformIO Core

## Quick Start

The complete installation is:

1. run the bootstrap script;
2. complete the interactive macless-haystack Apple login;
3. generate Google `Auth/secrets.json` with GoogleFindMyTools on a desktop;
4. copy that file to `data/google/secrets.json`;
5. start the stack and open `http://SERVER_IP:8125`.

### Linux, ZimaOS, CasaOS, or NAS

Clone or download this repository, open a terminal in it, and run:

```bash
bash scripts/bootstrap.sh
```

The script:

1. creates the persistent host directories;
2. creates `.env` with a random 64-character Google API token;
3. pulls the Apple provider images;
4. builds Find_My_Web and the Google sidecar;
5. starts anisette for the Apple login step.

It does not ask for Apple or Google account passwords.

### Windows with Docker Desktop

Open PowerShell in the repository:

```powershell
.\scripts\bootstrap.ps1
```

If PowerShell blocks local scripts, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

Continue with the provider authentication sections below.

## Environment Configuration

The bootstrap script creates `.env`. To configure it manually:

```bash
cp .env.example .env
```

Then edit:

```dotenv
WEB_PORT=8125
TZ=Europe/Rome
GOOGLE_TOKEN=replace-with-a-random-64-character-token
GOOGLE_FIND_HUB_REF=main
RETENTION_DAYS=21
REFRESH_INTERVAL=1800
```

| Variable | Default | Meaning |
|---|---:|---|
| `WEB_PORT` | `8125` | Host port for the web interface |
| `TZ` | `UTC` | Container timezone |
| `GOOGLE_TOKEN` | required | Local bearer token between Find_My_Web and the Google sidecar |
| `GOOGLE_FIND_HUB_REF` | `main` | Traccar branch, tag, or commit to build |
| `RETENTION_DAYS` | `21` | Local position-history retention |
| `REFRESH_INTERVAL` | `1800` | Initial polling interval in seconds |

`GOOGLE_TOKEN` is not a Google account credential. It protects the local
sidecar API. The same value is passed automatically to both local containers.

Generate one manually with:

```bash
openssl rand -hex 32
```

Do not commit `.env`.

## Apple Provider Setup

macless-haystack requires an interactive first login. The upstream project
currently supports Apple two-factor authentication through SMS/text message.

Use a dedicated Apple account where practical. This is an unofficial
integration and its persistent volume contains reusable authentication data.

### 1. Start anisette

The bootstrap script already does this. Otherwise run:

```bash
docker compose up -d anisette
```

### 2. Run the interactive Apple login

```bash
docker compose run --rm macless-haystack
```

Enter the Apple ID, password, and verification code when requested.

When the output reports that the endpoint is serving on port `6176`, the
authentication data has been saved in the `find-my-web-apple-auth` Docker
volume. Press `Ctrl+C` to stop the temporary interactive container.

### 3. Start the normal Apple container

```bash
docker compose up -d macless-haystack
```

Check it:

```bash
docker compose logs --tail=100 macless-haystack
```

The service name `anisette` resolves automatically because both containers are
on the same Compose network.

### 4. Confirm the Apple connection

No Apple IP, port, or URL must be entered in Find_My_Web. Compose already sets:

```text
Find_My_Web -> http://macless-haystack:6176
macless-haystack -> http://anisette:6969
```

Open the web interface, expand **Provider connections**, and confirm that the
Apple host is `macless-haystack` and the port is `6176`. Press **Refresh** after
adding an Apple device.

The Apple tracker identity itself is configured separately:

1. generate an Apple P-224 pair in Find_My_Web;
2. keep the private key in Find_My_Web;
3. copy only the Base64 advertisement key to the tracker firmware.

## Google Provider Setup

Google authentication must be generated on a desktop with an up-to-date Chrome
installation. Do not attempt the initial browser authentication inside the
headless server container.

### Understand the three Google values

| Value | Where it comes from | Where it goes |
|---|---|---|
| `GOOGLE_TOKEN` | Randomly generated by `bootstrap.sh` or `bootstrap.ps1` | `.env`; protects only the local sidecar API |
| `Auth/secrets.json` | Generated by running GoogleFindMyTools with Chrome | `data/google/secrets.json`; authenticates the Google account |
| 40-character advertisement EID | Generated when pressing `r` in GoogleFindMyTools | Find_My_Web device field and nRF52 firmware |

`GOOGLE_TOKEN` is not downloaded from GoogleFindMyTools. The Google account
credential and the tracker advertisement EID are.

### 1. Enable Find Hub offline finding

On an Android device logged into the intended Google account:

1. open **Settings**;
2. open **Google**;
3. open **All services**;
4. open **Find My Device** or **Find Hub**;
5. enable offline finding in all areas or high-traffic areas.

Some accounts require pairing a commercial Find Hub tracker once before the
encryption data becomes available.

### 2. Generate `Auth/secrets.json`

Download GoogleFindMyTools on the desktop. Using Git:

```bash
git clone https://github.com/leonboe1/GoogleFindMyTools.git
cd GoogleFindMyTools
python -m venv .venv
```

Without Git, open
[GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools), select
**Code > Download ZIP**, extract it, and open a terminal in the extracted
directory before running `python -m venv .venv`.

Activate the environment.

Windows:

```powershell
.\.venv\Scripts\activate
```

Linux or macOS:

```bash
source .venv/bin/activate
```

Install and start:

```bash
pip install -r requirements.txt
python main.py
```

Complete the Chrome login. The tool stores the reusable result at:

```text
GoogleFindMyTools/Auth/secrets.json
```

Treat this file as a password.

### 3. Register a custom Google tracker

While `main.py` is running, press `r` when offered the registration action.
Store both values returned by the tool:

- the canonical Google device ID;
- the 20-byte advertisement EID represented by 40 hexadecimal characters.

The EID is the string used by the nRF52 firmware.

### 4. Copy the Google authentication file

Copy:

```text
GoogleFindMyTools/Auth/secrets.json
```

to:

```text
Find_My_Web_Complete_Stack/data/google/secrets.json
```

The Google container deliberately waits without restarting when this file is
missing. After copying it:

```bash
docker compose restart google-provider
```

Check it:

```bash
docker compose logs --tail=100 google-provider
```

## Start the Complete Stack

After both provider setup procedures:

```bash
docker compose up -d
```

Show status:

```bash
docker compose ps
```

Open:

```text
http://SERVER_IP:8125
```

Find_My_Web is already configured to contact:

```text
http://macless-haystack:6176
http://google-provider:5500
```

No provider IP addresses are required for the bundled Compose stack. The
provider settings remain editable in the web interface for advanced external
deployments.

## ZimaOS and CasaOS GUI Installation

If terminal access is available, run the bootstrap script first and then import
`compose.yaml` in the graphical Docker application.

If only the GUI is available:

1. upload the complete repository to an AppData directory;
2. duplicate `.env.example` as `.env`;
3. replace `GOOGLE_TOKEN` with a long random hexadecimal value;
4. ensure these host directories exist:
   - `data/google`
   - `data/web`
5. import `compose.yaml`;
6. initially start only `anisette`;
7. use a terminal once for the interactive Apple command:

   ```bash
   docker compose run --rm macless-haystack
   ```

8. copy Google `secrets.json` to `data/google/secrets.json`;
9. start the complete Compose application.

The Apple data uses named Docker volumes because Docker seeds the files supplied
by the upstream image. The web database and Google authentication use visible
directories inside this repository for straightforward backup.

## Add Devices in Find_My_Web

### Apple identity

1. open **Generate Apple P-224 keys**;
2. generate a pair;
3. store the private key in Find_My_Web;
4. copy the displayed Base64 advertisement key to the firmware.

P-224 generation is Apple-only. The Apple private key decrypts reports and must
never be placed in firmware.

For an existing logical device, open **Apple P-224 advertisement key**. The UI
shows the current firmware string. To replace it, enter the new advertisement
string and its matching private key. The backend verifies the pair before
saving.

### Google identity

1. add or open the same logical device;
2. select or paste the Google canonical device ID;
3. paste the 40-character Google advertisement EID;
4. save provider settings.

Find_My_Web stores the Google EID for firmware convenience. It does not generate
Google identities.

### Dual-network identity

One logical device can contain:

- one Apple private identity;
- one Apple advertisement string;
- one Google canonical device ID;
- one Google advertisement EID.

Apple and Google positions remain separate time-series events. The `All` view
combines them only for display.

## Map Controls

The source selector supports:

- `Apple`
- `Google`
- `All`

The persistent map-detail selector supports:

- **Latest positions**: one current marker per selected provider;
- **All positions + tracks**: every retained point and chronological tracks.

Apple tracks are solid and Google tracks are dashed. Clicking a point displays
its source, timestamp, received time, accuracy, coordinates, and navigation
links.

## MQTT and Home Assistant

Open **MQTT / Home Assistant** in the web interface and configure:

- broker host and port;
- optional username and password;
- base topic;
- automatic refresh interval.

Polling and MQTT publication happen in the backend, even when no browser is
open. Every normalized event includes:

```json
{
  "tracker_id": "logical-device-id",
  "source": "apple",
  "latitude": 45.000000,
  "longitude": 9.000000,
  "accuracy": 12,
  "timestamp": 1780000000000,
  "received_at": 1780000005000
}
```

Per-device checkboxes control whether Apple, Google, or both sources are
published. Home Assistant MQTT discovery creates per-source trackers and a
combined latest tracker.

## nRF52832 Dual-Network Firmware

The firmware is in:

```text
firmware/nrf52
```

It creates two independent controller-managed advertising sets:

- Apple manufacturer data;
- Google Find Hub `FEAA` service data.

If both strings are configured, both sets remain active simultaneously. The CPU
does not wake up to alternate protocols.

### 1. Insert the strings

Edit:

```text
firmware/nrf52/include/tracker_keys.h
```

Apple, copied from Find_My_Web:

```c
#define APPLE_ADVERTISEMENT_KEY_BASE64 "PASTE_40_CHARACTER_BASE64_STRING"
```

Google, copied from GoogleFindMyTools or the Find_My_Web device field:

```c
#define GOOGLE_ADVERTISEMENT_KEY_HEX "00112233445566778899aabbccddeeff00112233"
```

Do not add `0x`, commas, spaces, or C byte arrays. Leave one string empty to
disable only that provider.

### 2. Select interval and power

In the same file:

```c
#define ADVERTISING_INTERVAL_MS 2000U
#define ADVERTISING_TX_POWER_DBM 0
```

Recommended profiles:

| Goal | Interval | Power | Trade-off |
|---|---:|---:|---|
| Recommended | `2000U` | `0` | Google-compatible balance |
| Faster sightings | `1000U` | `0` | More radio activity |
| Maximum range | `2000U` | `4` | Higher peak current |
| Indoor saving | `2000U` | `-4` | Reduced range and below Google's 0 dBm recommendation |
| Aggressive saving | `4000U` | `-8` | Slower and outside Google's two-second recommendation |

Google recommends at least one frame every two seconds and at least 0 dBm
conducted transmit power. Start with the defaults.

### 3. Build

Install the PlatformIO IDE extension in Visual Studio Code, open
`firmware/nrf52`, and select **Build**.

Or:

```bash
cd firmware/nrf52
pio run
```

Output:

```text
.pio/build/nrf52_dk/firmware.hex
```

A universal precompiled tracking binary is not provided because the two
personal advertisement strings are compiled into the image.

### 4. Wire the programmer

| Programmer | nRF52832 |
|---|---|
| `SWDIO` | `SWDIO` |
| `SWCLK` | `SWCLK` |
| `GND` | `GND` |
| `VTref` | `VDD` |

Remove the coin cell while the programmer supplies power. Never power the board
from two sources.

### 5. Flash

The default uploader is CMSIS-DAP/DAPLink:

```bash
pio run -t upload
```

For J-Link, change `firmware/nrf52/platformio.ini`:

```ini
debug_tool = jlink
upload_protocol = jlink
```

Then upload again.

### 6. Verify

Use nRF Connect for Mobile or another BLE scanner.

Apple frame prefix:

```text
4C 00 12 19
```

Google service UUID:

```text
FEAA
```

The firmware is intentionally non-connectable and has no advertised friendly
name.

## Low-Power Hardware Advice

For coin-cell operation, choose a board with:

- no permanent power LED;
- no USB-to-serial converter;
- no high-quiescent-current regulator;
- exposed SWD pads;
- an optional 32.768 kHz crystal.

Firmware cannot compensate for an always-on LED or inefficient regulator.
Measure the assembled board instead of relying on theoretical battery-life
figures.

## Backup

Back up visible data:

```bash
cp -a data backups/data
cp .env backups/find-my-web.env
```

Back up the Apple named volume:

```bash
mkdir -p backups
docker run --rm \
  -v find-my-web-apple-auth:/source:ro \
  -v "$PWD/backups":/backup \
  alpine sh -c "cd /source && tar czf /backup/apple-auth.tgz ."
```

Back up anisette similarly:

```bash
docker run --rm \
  -v find-my-web-anisette-data:/source:ro \
  -v "$PWD/backups":/backup \
  alpine sh -c "cd /source && tar czf /backup/anisette-data.tgz ."
```

Never publish:

- `.env`
- `data/google/secrets.json`
- Apple private keys
- exported device JSON backups
- MQTT passwords

## Updating

```bash
docker compose pull
docker compose build --pull
docker compose up -d
```

Back up first. Setting `GOOGLE_FIND_HUB_REF` to a commit hash makes Google
sidecar builds reproducible; `main` follows current upstream development.

## Troubleshooting

### Apple endpoint asks for login after restart

Confirm the `find-my-web-apple-auth` volume still exists:

```bash
docker volume ls
```

Do not delete the complete volume. Re-run:

```bash
docker compose run --rm macless-haystack
```

### Apple returns HTTP 401

The Apple session has expired or been revoked. Back up the Apple volume, stop
the service, and repeat the interactive login. If the upstream application
continues using an invalid token, remove only its cached `auth.json`:

```bash
docker compose run --rm --entrypoint sh macless-haystack \
  -lc "rm -f /app/endpoint/data/auth.json"
docker compose run --rm macless-haystack
```

### Google container says it is waiting

Copy a non-empty file to:

```text
data/google/secrets.json
```

Then:

```bash
docker compose restart google-provider
```

### Google returns HTTP 401

The local `GOOGLE_TOKEN` values do not match or the container was not recreated
after `.env` changed:

```bash
docker compose up -d --force-recreate google-provider find-my-web
```

### Google account authentication fails

Regenerate `Auth/secrets.json` with current Chrome and current
GoogleFindMyTools. Verify offline finding is enabled on Android.

### The map has reports but no positions

Check provider logs:

```bash
docker compose logs --tail=200 macless-haystack
docker compose logs --tail=200 google-provider
docker compose logs --tail=200 find-my-web
```

Confirm the device uses matching firmware and backend identities.

### The nRF52 does not advertise

- verify the key string lengths;
- rebuild after editing `tracker_keys.h`;
- confirm the programmer flashed the new `firmware.hex`;
- scan for raw Apple manufacturer data and Google `FEAA` service data;
- confirm the module is an nRF52832 and not a pin-incompatible board.

## Repository Layout

```text
.
├── app/
│   ├── backend/
│   └── web/
├── data/
│   ├── google/
│   └── web/
├── docker/
├── firmware/
│   └── nrf52/
├── scripts/
├── tests/
├── compose.yaml
└── README.md
```

## Development

Run backend tests:

```bash
python -m pip install -r app/backend/requirements.txt
python -m unittest discover -s tests -v
```

Build the web image:

```bash
docker build -f docker/find-my-web.Dockerfile -t find-my-web .
```

GitHub Actions tests the Python engine and compiles the nRF52832 firmware.

## Security

- Keep the provider network private.
- Do not expose provider ports directly to the internet.
- Put the web UI behind authenticated HTTPS before exposing it outside the LAN.
- Use dedicated provider accounts where practical.
- Keep `.env`, `secrets.json`, private keys, and backups outside Git.
- Review upstream changes before rebuilding from a new Google ref.

## Upstream Projects and Licenses

This repository contains original Find_My_Web and nRF52 integration code under
the MIT License. External containers and source fetched while building retain
their own licenses. Referencing public images and repositories from a Compose
file is a normal integration pattern, but it does not transfer ownership or
relicense third-party code.

- [macless-haystack](https://github.com/dchristl/macless-haystack), AGPL-3.0,
  by Denis Christl and contributors
- [anisette-v3-server](https://github.com/Dadoum/anisette-v3-server), by Dadoum
  and contributors
- [GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools), GPL-3.0,
  by Leon Boettger and contributors
- [traccar/google-find-hub-sync](https://github.com/traccar/google-find-hub-sync),
  GPL-3.0, maintained by Traccar and based on GoogleFindMyTools
- [OpenHaystack](https://github.com/seemoo-lab/openhaystack)
- [Leaflet](https://leafletjs.com/), BSD-2-Clause
- [OpenStreetMap](https://www.openstreetmap.org/)

The Compose project references upstream software; it does not vendor those
source repositories. If you later publish modified or prebuilt third-party
images, review the corresponding GPL/AGPL source-distribution obligations.
This summary is practical project guidance, not legal advice.

Third-party names and trademarks belong to their respective owners.
