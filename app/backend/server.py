"""
Find_My_Web self-hosted multi-provider tracking engine.

- Provider endpoints can be configured through environment variables or the UI.
- Device identities and position history persist in the /data volume.
- Apple reports are decrypted server-side (P-224 ECDH -> X9.63 KDF -> AES-GCM).
- Background polling builds a local history beyond each provider's query window.
"""
import os, json, base64, struct, hashlib, threading, time
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, request, jsonify, send_from_directory
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import google_provider
from event_store import EventStore

WEB_DIR        = os.environ.get("WEB_DIR", "/web")
DATA           = Path(os.environ.get("DATA_DIR", "/data"))
ENDPOINT_URL   = os.environ.get("ENDPOINT_URL", "").strip()
ENDPOINT_USER  = os.environ.get("ENDPOINT_USER", "").strip()
ENDPOINT_PASS  = os.environ.get("ENDPOINT_PASS", "")
DAYS_DEFAULT   = int(os.environ.get("DAYS", "7"))              # Apple query window
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "21"))   # local retention
HISTORY_CAP    = int(os.environ.get("HISTORY_CAP", "10000"))   # points per device
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "1800"))  # auto-refresh (s)

DATA.mkdir(parents=True, exist_ok=True)
TAGS_FILE = DATA / "tags.json"
SETTINGS_FILE = DATA / "settings.json"
lock = threading.Lock()
app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
EVENTS = EventStore(DATA / "events.db")


# ---------- settings: editable in the UI and persisted under /data ----------
def _endpoint_parts(value, default_port):
    raw = str(value or "").strip()
    if not raw:
        return "", default_port
    try:
        parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
        return parsed.hostname or "", parsed.port or default_port
    except ValueError:
        return "", default_port


def _endpoint_url(host, port):
    host = str(host or "").strip()
    if not host:
        return ""
    parsed = urlsplit(host if "://" in host else f"http://{host}")
    clean_host = parsed.hostname or ""
    if not clean_host:
        return ""
    if ":" in clean_host and not clean_host.startswith("["):
        clean_host = f"[{clean_host}]"
    return f"{parsed.scheme or 'http'}://{clean_host}:{int(port)}"


APPLE_ENV_HOST, APPLE_ENV_PORT = _endpoint_parts(ENDPOINT_URL, 6176)
GOOGLE_ENV_HOST, GOOGLE_ENV_PORT = _endpoint_parts(google_provider.GOOGLE_URL, 5500)

SETTINGS_DEFAULTS = {
    "mqtt_enabled": False,
    "mqtt_host":    os.environ.get("MQTT_HOST", ""),
    "mqtt_port":    int(os.environ.get("MQTT_PORT", "1883")),
    "mqtt_user":    os.environ.get("MQTT_USER", ""),
    "mqtt_pass":    os.environ.get("MQTT_PASS", ""),
    "mqtt_base":    os.environ.get("MQTT_BASE", "Find_My_Web"),
    "refresh_min":  max(1, REFRESH_INTERVAL // 60),
    "days":         DAYS_DEFAULT,
    "apple_host":   APPLE_ENV_HOST,
    "apple_port":   APPLE_ENV_PORT,
    "google_host":  GOOGLE_ENV_HOST,
    "google_port":  GOOGLE_ENV_PORT,
    "google_token": google_provider.GOOGLE_TOKEN,
}

def load_settings():
    s = dict(SETTINGS_DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig")))
        except Exception:
            pass
    if s.get("mqtt_base") == "Macless_Haystack":
        s["mqtt_base"] = "Find_My_Web"
    return s

def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    try:
        SETTINGS_FILE.chmod(0o600)
    except OSError:
        pass

def effective_refresh_sec():
    return max(60, int(load_settings().get("refresh_min", 30)) * 60)

def effective_days():
    return int(load_settings().get("days", DAYS_DEFAULT))


def effective_apple_url():
    s = load_settings()
    return _endpoint_url(s.get("apple_host", ""), s.get("apple_port", 6176))


def effective_google_url():
    s = load_settings()
    return _endpoint_url(s.get("google_host", ""), s.get("google_port", 5500))


def effective_google_token():
    return str(load_settings().get("google_token", "") or "")


# ---------- storage ----------
def load_tags():
    if TAGS_FILE.exists():
        try:
            data = json.loads(TAGS_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("tags", [data])
            return []
        except Exception:
            return []
    return []

def save_tags(tags):
    TAGS_FILE.write_text(json.dumps(tags, indent=2), encoding="utf-8")


# ---------- crypto ----------
def adv_key_bytes(priv_bytes):
    """Derive the 28-byte P-224 X coordinate broadcast by Apple trackers."""
    priv = ec.derive_private_key(int.from_bytes(priv_bytes, "big"), ec.SECP224R1())
    return priv.public_key().public_numbers().x.to_bytes(28, "big")

def hashed_key(priv_bytes):
    adv = adv_key_bytes(priv_bytes)
    return base64.b64encode(hashlib.sha256(adv).digest()).decode()

def decrypt_report(payload_b64, priv_bytes):
    d = base64.b64decode(payload_b64)
    if len(d) > 88:                            # current 89-byte format: remove byte 4
        d = d[:4] + d[5:]
    ts  = struct.unpack(">I", d[0:4])[0]      # seconds since 2001-01-01 UTC
    eph = d[5:62]                              # 57-byte ephemeral public key
    enc = d[62:72]                             # 10 encrypted bytes
    tag = d[72:]                               # 16-byte GCM tag
    priv = ec.derive_private_key(int.from_bytes(priv_bytes, "big"), ec.SECP224R1())
    eph_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP224R1(), eph)
    shared = priv.exchange(ec.ECDH(), eph_pub)
    derived = hashlib.sha256(shared + b"\x00\x00\x00\x01" + eph).digest()
    dec = Cipher(algorithms.AES(derived[:16]), modes.GCM(derived[16:], tag)).decryptor()
    pt = dec.update(enc) + dec.finalize()
    return {
        "lat": struct.unpack(">i", pt[0:4])[0] / 1e7,
        "lon": struct.unpack(">i", pt[4:8])[0] / 1e7,
        "acc": pt[8],
        "ts":  (ts + 978307200) * 1000,
    }


# ---------- logical devices + event views ----------
def _source_filter(value):
    return value if value in ("apple", "google") else None


def _apple_configured(device):
    return bool(device.get("priv") and device.get("hashed"))


def _google_configured(device):
    return bool(device.get("google_id"))


def _mqtt_sources(device):
    sources = []
    if _apple_configured(device) and bool(device.get("mqtt_apple", device.get("mqtt", True))):
        sources.append("apple")
    if _google_configured(device) and bool(device.get("mqtt_google", device.get("mqtt", True))):
        sources.append("google")
    return sources


def device_view(device, source=None):
    """Public logical-device view backed exclusively by the event store."""
    source = _source_filter(source)
    history = EVENTS.list(device["id"], source=source, limit=HISTORY_CAP)
    apple = {"configured": _apple_configured(device)}
    if apple["configured"]:
        advertisement = adv_key_bytes(base64.b64decode(device["priv"]))
        apple.update(
            {
                "advertisement": base64.b64encode(advertisement).decode(),
            }
        )
    google = {
        "configured": _google_configured(device),
        "id": device.get("google_id", ""),
        "public_key": device.get("google_public_key", ""),
    }
    latest = max(
        history,
        key=lambda event: (event["timestamp"], event["received_at"]),
        default=None,
    )
    return {
        "id": device["id"],
        "device_id": device["id"],
        "name": device.get("name", "tracker"),
        "hidden": bool(device.get("hidden", False)),
        "color": device.get("color"),
        "history": history,
        "latest": latest,
        "sources": [
            source_name
            for source_name, configured in (
                ("apple", apple["configured"]),
                ("google", google["configured"]),
            )
            if configured
        ],
        "apple": apple,
        "google": google,
        "mqtt": {
            "apple": bool(device.get("mqtt_apple", device.get("mqtt", True))),
            "google": bool(device.get("mqtt_google", device.get("mqtt", True))),
        },
        # Compatibility fields used by older Apple-only clients.
        "advertisement": apple.get("advertisement", ""),
    }


def all_devices_view(source=None):
    return [device_view(device, source=source) for device in load_tags()]


def migrate_legacy_histories():
    """Move legacy per-tag history into SQLite once, without losing data."""
    with lock:
        devices = load_tags()
        changed = False
        events = []
        for device in devices:
            if "mqtt_apple" not in device:
                device["mqtt_apple"] = bool(device.get("mqtt", True))
                changed = True
            if "mqtt_google" not in device:
                device["mqtt_google"] = bool(device.get("mqtt", True))
                changed = True
            legacy = device.pop("history", None)
            if legacy is not None:
                changed = True
                for point in legacy:
                    events.append(
                        {
                            "device_id": device["id"],
                            "tracker_id": device.get("hashed", device["id"]),
                            "source": "apple",
                            "latitude": point["lat"],
                            "longitude": point["lon"],
                            "accuracy": point.get("acc", 0),
                            "timestamp": point["ts"],
                            "received_at": point.get("received_at", point["ts"]),
                        }
                    )
        EVENTS.append(events)
        if changed:
            save_tags(devices)


# ---------- MQTT (events + Home Assistant device_tracker discovery) ----------
def _mqtt_event_msgs(events):
    base = (load_settings().get("mqtt_base") or "Find_My_Web").strip("/")
    messages = []
    for event in events:
        payload = {
            "device_id": event["device_id"],
            "tracker_id": event["tracker_id"],
            "source": event["source"],
            "latitude": event["latitude"],
            "longitude": event["longitude"],
            "accuracy": event["accuracy"],
            "timestamp": event["timestamp"],
            "received_at": event["received_at"],
        }
        messages.append(
            {
                "topic": f"{base}/events/{event['device_id']}/{event['source']}",
                "payload": json.dumps(payload),
                "retain": False,
            }
        )
    return messages


def _mqtt_state_msgs(devices):
    """Publish per-source trackers plus one absolute-latest logical tracker."""
    base = (load_settings().get("mqtt_base") or "Find_My_Web").strip("/")
    messages = []
    for device in devices:
        enabled_sources = _mqtt_sources(device)
        source_latest = {}
        for source in enabled_sources:
            latest = EVENTS.latest(device["id"], [source])
            if latest:
                source_latest[source] = latest

        for source, event in source_latest.items():
            uid = f"Find_My_Web_{source}_{device['id']}"
            attr_topic = f"{base}/{device['id']}/{source}/attributes"
            discovery = {
                "name": f"{device.get('name', 'tracker')} ({source})",
                "unique_id": uid,
                "json_attributes_topic": attr_topic,
                "source_type": "gps",
                "device": {
                    "identifiers": [f"Find_My_Web_{device['id']}"],
                    "name": device.get("name", "tracker"),
                    "manufacturer": "Find_My_Web",
                },
            }
            messages.extend(
                [
                    {
                        "topic": f"homeassistant/device_tracker/{uid}/config",
                        "payload": json.dumps(discovery),
                        "retain": True,
                    },
                    {
                        "topic": attr_topic,
                        "payload": json.dumps(_ha_attributes(event)),
                        "retain": True,
                    },
                ]
            )

        if source_latest:
            # If both providers are enabled, the newest device timestamp wins.
            best = max(
                source_latest.values(),
                key=lambda event: (event["timestamp"], event["received_at"]),
            )
            uid = f"Find_My_Web_{device['id']}"
            attr_topic = f"{base}/{device['id']}/attributes"
            discovery = {
                "name": device.get("name", "tracker"),
                "unique_id": uid,
                "json_attributes_topic": attr_topic,
                "source_type": "gps",
                "device": {
                    "identifiers": [uid],
                    "name": device.get("name", "tracker"),
                    "manufacturer": "Find_My_Web",
                },
            }
            messages.extend(
                [
                    {
                        "topic": f"homeassistant/device_tracker/{uid}/config",
                        "payload": json.dumps(discovery),
                        "retain": True,
                    },
                    {
                        "topic": attr_topic,
                        "payload": json.dumps(_ha_attributes(best)),
                        "retain": True,
                    },
                ]
            )
    return messages


def _ha_attributes(event):
    return {
        "device_id": event["device_id"],
        "tracker_id": event["tracker_id"],
        "latitude": event["latitude"],
        "longitude": event["longitude"],
        "gps_accuracy": int(event.get("accuracy", 0)),
        "timestamp": event["timestamp"],
        "received_at": event["received_at"],
        "source": event["source"],
    }

def _mqtt_send(msgs):
    s = load_settings()
    if not s.get("mqtt_enabled") or not s.get("mqtt_host") or not msgs:
        return
    try:
        import paho.mqtt.publish as publish
    except Exception as e:
        print(f"[mqtt] paho-mqtt non disponibile: {e}", flush=True)
        return
    auth = {"username": s["mqtt_user"], "password": s["mqtt_pass"]} if s.get("mqtt_user") else None
    try:
        publish.multiple(msgs, hostname=s["mqtt_host"], port=int(s.get("mqtt_port", 1883)), auth=auth)
        print(f"[mqtt] inviati {len(msgs)} messaggi a {s['mqtt_host']}:{s.get('mqtt_port')}", flush=True)
    except Exception as e:
        print(f"[mqtt] errore publish: {e}", flush=True)

def publish_mqtt(events=None):
    devices = load_tags()
    messages = _mqtt_state_msgs(devices)
    messages.extend(_mqtt_event_msgs(events or []))
    if messages:
        threading.Thread(target=_mqtt_send, args=(messages,), daemon=True).start()


def mqtt_remove(device_id):
    """Remove the combined and both source entities from HA discovery."""
    uids = [
        f"Find_My_Web_{device_id}",
        f"Find_My_Web_apple_{device_id}",
        f"Find_My_Web_google_{device_id}",
    ]
    messages = [
        {
            "topic": f"homeassistant/device_tracker/{uid}/config",
            "payload": "",
            "retain": True,
        }
        for uid in uids
    ]
    threading.Thread(target=_mqtt_send, args=(messages,), daemon=True).start()


# ---------- provider polling + normalization ----------
def poll_apple(devices, days):
    endpoint_url = effective_apple_url()
    configured = [device for device in devices if _apple_configured(device)]
    if not configured:
        return {"configured": bool(endpoint_url), "reports": 0, "inserted": 0}, []
    if not endpoint_url:
        raise RuntimeError("Apple provider endpoint is not configured")

    auth = (ENDPOINT_USER, ENDPOINT_PASS) if ENDPOINT_USER else None
    response = requests.post(
        endpoint_url,
        json={"ids": [device["hashed"] for device in configured], "days": days},
        auth=auth,
        timeout=40,
    )
    response.raise_for_status()
    reports = response.json().get("results", [])
    received_at = int(time.time() * 1000)
    events = []
    for device in configured:
        private_key = base64.b64decode(device["priv"])
        for report in reports:
            payload = report.get("payload")
            if not payload:
                continue
            try:
                position = decrypt_report(payload, private_key)
            except Exception:
                continue
            events.append(
                {
                    "device_id": device["id"],
                    "tracker_id": device["hashed"],
                    "source": "apple",
                    "latitude": position["lat"],
                    "longitude": position["lon"],
                    "accuracy": position.get("acc", 0),
                    "timestamp": position["ts"],
                    "received_at": received_at,
                    "metadata": {"payload_length": len(base64.b64decode(payload))},
                }
            )
    inserted = EVENTS.append(events)
    return {
        "configured": True,
        "reports": len(reports),
        "inserted": len(inserted),
    }, inserted


def poll_google(devices):
    endpoint_url = effective_google_url()
    token = effective_google_token()
    configured = [device for device in devices if _google_configured(device)]
    if not configured:
        return {
            "configured": google_provider.configured(endpoint_url, token),
            "reports": 0,
            "inserted": 0,
        }, []
    if not google_provider.configured(endpoint_url, token):
        raise RuntimeError("Google provider endpoint or GOOGLE_TOKEN is not configured")

    events = []
    reports = 0
    errors = {}
    for device in configured:
        try:
            locations = google_provider.fetch_locations(
                device["google_id"], base_url=endpoint_url, token=token
            )
            reports += len(locations)
            for location in locations:
                events.append(
                    {
                        **location,
                        "device_id": device["id"],
                        "tracker_id": device["google_id"],
                        "source": "google",
                    }
                )
        except Exception as exc:
            errors[device["id"]] = str(exc)
    inserted = EVENTS.append(events)
    status = {
        "configured": True,
        "reports": reports,
        "inserted": len(inserted),
    }
    if errors:
        status["errors"] = errors
    return status, inserted


def do_refresh(days):
    devices = load_tags()
    status = {}
    inserted = []
    for source, poller in (
        ("apple", lambda: poll_apple(devices, days)),
        ("google", lambda: poll_google(devices)),
    ):
        try:
            provider_status, provider_events = poller()
            status[source] = provider_status
            inserted.extend(provider_events)
        except Exception as exc:
            status[source] = {
                "configured": bool(
                    effective_apple_url()
                    if source == "apple"
                    else google_provider.configured(
                        effective_google_url(), effective_google_token()
                    )
                ),
                "reports": 0,
                "inserted": 0,
                "error": str(exc),
            }

    cutoff = int(time.time() * 1000 - RETENTION_DAYS * 86400000)
    EVENTS.prune(cutoff)
    publish_mqtt(inserted)
    print(f"[refresh] {json.dumps(status, separators=(',', ':'))}", flush=True)
    return status


# ---------- API ----------
@app.get("/api/config")
def api_config():
    apple_url = effective_apple_url()
    google_url = effective_google_url()
    google_token = effective_google_token()
    return jsonify(
        {
            "apple": {"endpoint": apple_url, "configured": bool(apple_url)},
            "google": {
                "endpoint": google_url,
                "configured": google_provider.configured(google_url, google_token),
            },
        }
    )


@app.get("/api/devices")
@app.get("/api/tags")
def api_get_devices():
    return jsonify(all_devices_view(request.args.get("source")))

@app.get("/api/export")
def api_export():
    return jsonify(
        [
            {
                "name": device.get("name", "tracker"),
                "privateKey": device.get("priv", ""),
                "googleId": device.get("google_id", ""),
                "googlePublicKey": device.get("google_public_key", ""),
                "mqttApple": bool(device.get("mqtt_apple", True)),
                "mqttGoogle": bool(device.get("mqtt_google", True)),
                "hidden": bool(device.get("hidden", False)),
                "color": device.get("color"),
            }
            for device in load_tags()
        ]
    )


def _parse_apple_key(value):
    if not value:
        return None, None
    private_b64 = str(value).strip()
    private_bytes = base64.b64decode(private_b64, validate=True)
    if len(private_bytes) != 28:
        raise ValueError("Apple private key must be exactly 28 bytes")
    return private_b64, hashed_key(private_bytes)


def _parse_apple_advertisement(value):
    if not value:
        return None
    advertisement_b64 = str(value).strip()
    advertisement = base64.b64decode(advertisement_b64, validate=True)
    if len(advertisement) != 28:
        raise ValueError("Apple advertisement key must decode to exactly 28 bytes")
    return advertisement


def _validate_apple_identity(private_b64, advertisement_value):
    private_key, apple_hash = _parse_apple_key(private_b64)
    expected_advertisement = _parse_apple_advertisement(advertisement_value)
    if expected_advertisement is not None:
        if private_key is None:
            raise ValueError(
                "A matching Apple private key is required when changing the "
                "advertisement key"
            )
        derived = adv_key_bytes(base64.b64decode(private_key))
        if derived != expected_advertisement:
            raise ValueError(
                "Apple advertisement and private keys do not belong to the same identity"
            )
    return private_key, apple_hash


@app.post("/api/devices")
@app.post("/api/tags")
def api_add_device():
    body = request.get_json(force=True, silent=True) or {}
    try:
        private_key, apple_hash = _validate_apple_identity(
            body.get("applePrivateKey", body.get("privateKey")),
            body.get("appleAdvertisementKey"),
        )
    except Exception as e:
        return jsonify({"error": f"Invalid Apple key: {e}"}), 400
    google_id = str(body.get("googleId", "")).strip()
    google_public_key = str(body.get("googlePublicKey", "")).strip()
    if not private_key and not google_id and not google_public_key:
        return jsonify({"error": "Configure at least one Apple or Google identity"}), 400

    with lock:
        devices = load_tags()
        device_id = base64.urlsafe_b64encode(os.urandom(6)).decode().rstrip("=")
        device = {
            "id": device_id,
            "name": str(body.get("name", "tracker")).strip() or "tracker",
            "mqtt_apple": bool(body.get("mqttApple", True)),
            "mqtt_google": bool(body.get("mqttGoogle", True)),
            "hidden": bool(body.get("hidden", False)),
        }
        if body.get("color"):
            device["color"] = str(body["color"])
        if private_key:
            device.update({"priv": private_key, "hashed": apple_hash})
        if google_id:
            device["google_id"] = google_id
        if google_public_key:
            device["google_public_key"] = google_public_key
        devices.append(device)
        save_tags(devices)
    return jsonify({"ok": True, "id": device_id})


@app.delete("/api/devices/<device_id>")
@app.delete("/api/tags/<tid>")
def api_del_device(tid=None, device_id=None):
    device_id = device_id or tid
    with lock:
        save_tags([device for device in load_tags() if device["id"] != device_id])
    EVENTS.delete_device(device_id)
    mqtt_remove(device_id)
    return jsonify({"ok": True})


@app.patch("/api/devices/<device_id>")
@app.patch("/api/tags/<tid>")
def api_update_device(tid=None, device_id=None):
    device_id = device_id or tid
    body = request.get_json(silent=True) or {}
    with lock:
        devices = load_tags()
        for device in devices:
            if device["id"] == device_id:
                if "name" in body:
                    name = str(body["name"]).strip()
                    if name:
                        device["name"] = name
                if "hidden" in body:
                    device["hidden"] = bool(body["hidden"])
                if "color" in body:
                    if body["color"]:
                        device["color"] = str(body["color"])
                    else:
                        device.pop("color", None)
                if "mqttApple" in body:
                    device["mqtt_apple"] = bool(body["mqttApple"])
                if "mqttGoogle" in body:
                    device["mqtt_google"] = bool(body["mqttGoogle"])
                if "googleId" in body:
                    google_id = str(body["googleId"] or "").strip()
                    if google_id:
                        device["google_id"] = google_id
                    else:
                        device.pop("google_id", None)
                if "googlePublicKey" in body:
                    google_key = str(body["googlePublicKey"] or "").strip()
                    if google_key:
                        device["google_public_key"] = google_key
                    else:
                        device.pop("google_public_key", None)
                if "applePrivateKey" in body:
                    try:
                        private_key, apple_hash = _validate_apple_identity(
                            body["applePrivateKey"],
                            body.get("appleAdvertisementKey"),
                        )
                    except Exception as exc:
                        return jsonify({"error": f"Invalid Apple key: {exc}"}), 400
                    if private_key:
                        device.update({"priv": private_key, "hashed": apple_hash})
                    else:
                        device.pop("priv", None)
                        device.pop("hashed", None)
                elif "appleAdvertisementKey" in body:
                    return jsonify(
                        {
                            "error": (
                                "Invalid Apple key: a matching private key is "
                                "required to change the advertisement key"
                            )
                        }
                    ), 400
                save_tags(devices)
                return jsonify({"ok": True})
    return jsonify({"error": "Device not found"}), 404


@app.get("/api/google/devices")
def api_google_devices():
    endpoint_url = effective_google_url()
    token = effective_google_token()
    try:
        return jsonify(
            {
                "configured": google_provider.configured(endpoint_url, token),
                "devices": google_provider.list_available(
                    base_url=endpoint_url, token=token
                ),
            }
        )
    except Exception as exc:
        return jsonify({"configured": True, "error": str(exc), "devices": []}), 502


@app.get("/api/events")
def api_events():
    source = _source_filter(request.args.get("source"))
    limit = request.args.get("limit", HISTORY_CAP)
    try:
        return jsonify(
            EVENTS.list(
                device_id=request.args.get("device_id"),
                source=source,
                limit=int(limit),
            )
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

@app.post("/api/refresh")
def api_refresh():
    days = int((request.get_json(silent=True) or {}).get("days", DAYS_DEFAULT))
    status = do_refresh(days)
    return jsonify({"devices": all_devices_view(), "providers": status})

@app.post("/api/generate")
def api_generate():
    priv = ec.generate_private_key(ec.SECP224R1())
    d = priv.private_numbers().private_value.to_bytes(28, "big")
    x = priv.public_key().public_numbers().x.to_bytes(28, "big")
    return jsonify({"private": base64.b64encode(d).decode(),
                    "advertisement": base64.b64encode(x).decode()})

@app.get("/api/settings")
def api_get_settings():
    s = load_settings()
    return jsonify({"mqtt_enabled": bool(s["mqtt_enabled"]), "mqtt_host": s["mqtt_host"],
                    "mqtt_port": int(s["mqtt_port"]), "mqtt_user": s["mqtt_user"],
                    "mqtt_base": s["mqtt_base"], "mqtt_pass_set": bool(s["mqtt_pass"]),
                    "refresh_min": int(s["refresh_min"]), "days": int(s["days"]),
                    "apple_host": s.get("apple_host", ""),
                    "apple_port": int(s.get("apple_port", 6176)),
                    "google_host": s.get("google_host", ""),
                    "google_port": int(s.get("google_port", 5500)),
                    "google_token_set": bool(s.get("google_token"))})

@app.post("/api/settings")
def api_set_settings():
    body = request.get_json(force=True, silent=True) or {}
    s = load_settings()
    for provider, default_port in (("apple", 6176), ("google", 5500)):
        host_key = f"{provider}_host"
        port_key = f"{provider}_port"
        if host_key in body:
            host, _ = _endpoint_parts(body[host_key], default_port)
            if str(body[host_key] or "").strip() and not host:
                return jsonify({"error": f"Invalid {provider} host"}), 400
            s[host_key] = host
        if port_key in body:
            try:
                port = int(body[port_key])
                if not 1 <= port <= 65535:
                    raise ValueError
                s[port_key] = port
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid {provider} port"}), 400
    if body.get("google_token"):
        s["google_token"] = str(body["google_token"]).strip()
    if body.get("clear_google_token"):
        s["google_token"] = ""
    if "mqtt_enabled" in body: s["mqtt_enabled"] = bool(body["mqtt_enabled"])
    if "mqtt_host" in body:    s["mqtt_host"] = str(body["mqtt_host"]).strip()
    if "mqtt_port" in body:
        try: s["mqtt_port"] = int(body["mqtt_port"])
        except Exception: pass
    if "mqtt_user" in body:    s["mqtt_user"] = str(body["mqtt_user"]).strip()
    if "mqtt_base" in body:    s["mqtt_base"] = str(body["mqtt_base"]).strip() or "Find_My_Web"
    if body.get("mqtt_pass"):  s["mqtt_pass"] = str(body["mqtt_pass"])
    if "refresh_min" in body:
        try: s["refresh_min"] = max(1, int(body["refresh_min"]))
        except Exception: pass
    if "days" in body:
        try: s["days"] = max(1, min(7, int(body["days"])))
        except Exception: pass
    save_settings(s)
    publish_mqtt()                                      # publish current states immediately
    return jsonify({"ok": True})

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


def background_loop():
    # primo giro dopo poco, poi all'intervallo impostato -> storico + MQTT sempre aggiornati
    time.sleep(20)
    while True:
        try:
            do_refresh(effective_days())
        except Exception as e:
            print(f"[auto-refresh] errore: {e}", flush=True)
        time.sleep(effective_refresh_sec())


if __name__ == "__main__":
    migrate_legacy_histories()
    threading.Thread(target=background_loop, daemon=True).start()
    print(
        f"[start] refresh={effective_refresh_sec()}s retention={RETENTION_DAYS}d "
        f"apple={bool(effective_apple_url())} "
        f"google={google_provider.configured(effective_google_url(), effective_google_token())}",
        flush=True,
    )
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=8000)
    except ImportError:
        app.run(host="0.0.0.0", port=8000, threaded=True)
