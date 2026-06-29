"""
Find My · self-hosted backend.

- Config (endpoint macless-haystack) via variabile d'ambiente ENDPOINT_URL.
- Tag (private key) + STORICO posizioni salvati su volume (/data) -> persistono
  ai riavvii e sono uguali da qualsiasi dispositivo.
- Decifratura dei report lato server (P-224 ECDH -> X9.63 KDF -> AES-128-GCM).
- AUTO-REFRESH in background: Apple tiene ~7 giorni di report; accumulando in
  continuo, lo storico locale arriva a >= 2 settimane (RETENTION_DAYS).
"""
import os, json, base64, struct, hashlib, threading, time
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

WEB_DIR        = os.environ.get("WEB_DIR", "/web")
DATA           = Path(os.environ.get("DATA_DIR", "/data"))
ENDPOINT_URL   = os.environ.get("ENDPOINT_URL", "").strip()
ENDPOINT_USER  = os.environ.get("ENDPOINT_USER", "").strip()
ENDPOINT_PASS  = os.environ.get("ENDPOINT_PASS", "")
DAYS_DEFAULT   = int(os.environ.get("DAYS", "7"))              # Apple ne dà max ~7
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "21"))   # quanto storico tenere (>= 2 sett.)
HISTORY_CAP    = int(os.environ.get("HISTORY_CAP", "10000"))   # tetto punti per tag
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "1800"))  # auto-refresh (s)

DATA.mkdir(parents=True, exist_ok=True)
TAGS_FILE = DATA / "tags.json"
SETTINGS_FILE = DATA / "settings.json"
lock = threading.Lock()
app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")


# ---------- settings (MQTT + intervallo) — modificabili da frontend, su /data ----------
SETTINGS_DEFAULTS = {
    "mqtt_enabled": False,
    "mqtt_host":    os.environ.get("MQTT_HOST", ""),
    "mqtt_port":    int(os.environ.get("MQTT_PORT", "1883")),
    "mqtt_user":    os.environ.get("MQTT_USER", ""),
    "mqtt_pass":    os.environ.get("MQTT_PASS", ""),
    "mqtt_base":    os.environ.get("MQTT_BASE", "findmy"),
    "refresh_min":  max(1, REFRESH_INTERVAL // 60),     # ogni quanto aggiornare (minuti)
    "days":         DAYS_DEFAULT,
}

def load_settings():
    s = dict(SETTINGS_DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass
    return s

def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))

def effective_refresh_sec():
    return max(60, int(load_settings().get("refresh_min", 30)) * 60)

def effective_days():
    return int(load_settings().get("days", DAYS_DEFAULT))


# ---------- storage ----------
def load_tags():
    if TAGS_FILE.exists():
        try:
            return json.loads(TAGS_FILE.read_text())
        except Exception:
            return []
    return []

def save_tags(tags):
    TAGS_FILE.write_text(json.dumps(tags, indent=2))


# ---------- crypto ----------
def adv_key_bytes(priv_bytes):
    """Chiave advertisement/pubblica (X di P-224, 28 byte): quella da mettere nell'ESP."""
    priv = ec.derive_private_key(int.from_bytes(priv_bytes, "big"), ec.SECP224R1())
    return priv.public_key().public_numbers().x.to_bytes(28, "big")

def carray_decl(x_bytes):
    h = [f"0x{b:02x}" for b in x_bytes]
    rows = ["    " + ", ".join(h[i:i+8]) for i in range(0, 28, 8)]
    return "static uint8_t public_key[28] = {\n" + ",\n".join(rows) + "\n};"

def hashed_key(priv_bytes):
    adv = adv_key_bytes(priv_bytes)
    return base64.b64encode(hashlib.sha256(adv).digest()).decode()

def decrypt_report(payload_b64, priv_bytes):
    d = base64.b64decode(payload_b64)
    if len(d) > 88:                            # formato recente (89 byte): togli il byte 4
        d = d[:4] + d[5:]
    ts  = struct.unpack(">I", d[0:4])[0]      # secondi dal 2001-01-01 UTC
    eph = d[5:62]                              # chiave effimera (57 byte)
    enc = d[62:72]                             # 10 byte cifrati
    tag = d[72:]                               # 16 byte tag GCM
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


# ---------- MQTT (per Home Assistant: discovery -> device_tracker GPS) ----------
def _mqtt_msgs_for(tags):
    base = (load_settings().get("mqtt_base") or "findmy").strip("/")
    msgs = []
    for t in tags:
        hist = t.get("history", [])
        if not hist:
            continue
        p = hist[-1]                                   # ultima posizione
        oid = t["id"]
        attr_topic = f"{base}/{oid}/attributes"
        disc = {
            "name": t["name"],
            "unique_id": f"findmy_{oid}",
            "json_attributes_topic": attr_topic,
            "source_type": "gps",
            "device": {"identifiers": [f"findmy_{oid}"], "name": t["name"],
                       "manufacturer": "findmy-web"},
        }
        attrs = {"latitude": p["lat"], "longitude": p["lon"],
                 "gps_accuracy": int(p.get("acc", 0)),
                 "last_seen": int(p["ts"] // 1000)}
        msgs.append({"topic": f"homeassistant/device_tracker/findmy_{oid}/config",
                     "payload": json.dumps(disc), "retain": True})
        msgs.append({"topic": attr_topic, "payload": json.dumps(attrs), "retain": True})
    return msgs

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

def publish_positions(tags):
    """Nome + ultima posizione di ogni tag su MQTT, in un thread (non blocca)."""
    msgs = _mqtt_msgs_for(tags)
    if msgs:
        threading.Thread(target=_mqtt_send, args=(msgs,), daemon=True).start()

def mqtt_remove(oid):
    """Rimuove l'entità da Home Assistant (config discovery vuota e ritenuta)."""
    msg = [{"topic": f"homeassistant/device_tracker/findmy_{oid}/config", "payload": "", "retain": True}]
    threading.Thread(target=_mqtt_send, args=(msg,), daemon=True).start()


# ---------- refresh (usata da /api/refresh e dall'auto-refresh) ----------
def do_refresh(days):
    with lock:
        tags = load_tags()
        if not tags:
            return tags, 0
        ids = [t["hashed"] for t in tags]
        auth = (ENDPOINT_USER, ENDPOINT_PASS) if ENDPOINT_USER else None
        r = requests.post(ENDPOINT_URL, json={"ids": ids, "days": days}, auth=auth, timeout=40)
        r.raise_for_status()
        results = r.json().get("results", [])
        lengths = sorted({len(base64.b64decode(x["payload"])) for x in results if x.get("payload")})
        cutoff = time.time() * 1000 - RETENTION_DAYS * 86400000
        decoded = 0
        for t in tags:
            priv = base64.b64decode(t["priv"])
            seen = {p["ts"] for p in t.get("history", [])}
            for x in results:                          # provo OGNI report con la chiave del tag
                payload = x.get("payload")
                if not payload:
                    continue
                try:
                    pos = decrypt_report(payload, priv)    # se non e' di questo tag -> eccezione
                except Exception:
                    continue
                if pos["ts"] not in seen:
                    t.setdefault("history", []).append(pos)
                    seen.add(pos["ts"]); decoded += 1
            hist = [p for p in t.get("history", []) if p["ts"] >= cutoff]   # ritenzione per eta'
            t["history"] = sorted(hist, key=lambda p: p["ts"])[-HISTORY_CAP:]
        save_tags(tags)
        print(f"[refresh] report={len(results)} payload_len={lengths} decifrate={decoded}", flush=True)
        n = len(results)
    publish_positions(tags)                            # MQTT fuori dal lock (non blocca)
    return tags, n


# ---------- API ----------
@app.get("/api/config")
def api_config():
    return jsonify({"endpoint": ENDPOINT_URL, "configured": bool(ENDPOINT_URL)})

def tag_view(t):
    """Vista pubblica di un tag: include la chiave advertisement + array C per l'ESP."""
    x = adv_key_bytes(base64.b64decode(t["priv"]))
    return {"id": t["id"], "name": t["name"], "history": t.get("history", []),
            "advertisement": base64.b64encode(x).decode(),
            "carray": carray_decl(x),
            "hidden": bool(t.get("hidden", False)),
            "color": t.get("color")}

@app.get("/api/tags")
def api_get_tags():
    return jsonify([tag_view(t) for t in load_tags()])

@app.get("/api/export")
def api_export():
    return jsonify([{"name": t["name"], "privateKey": t["priv"]} for t in load_tags()])

@app.post("/api/tags")
def api_add_tag():
    body = request.get_json(force=True, silent=True) or {}
    try:
        priv_b64 = body["privateKey"].strip()
        priv_b = base64.b64decode(priv_b64)
        if len(priv_b) != 28:
            return jsonify({"error": "la private key deve essere 28 byte"}), 400
        h = hashed_key(priv_b)
    except Exception as e:
        return jsonify({"error": f"chiave non valida: {e}"}), 400
    with lock:
        tags = load_tags()
        tid = base64.urlsafe_b64encode(os.urandom(6)).decode().rstrip("=")
        tags.append({"id": tid, "name": body.get("name", "tag"),
                     "priv": priv_b64, "hashed": h, "history": []})
        save_tags(tags)
    return jsonify({"ok": True, "id": tid})

@app.delete("/api/tags/<tid>")
def api_del_tag(tid):
    with lock:
        save_tags([t for t in load_tags() if t["id"] != tid])
    mqtt_remove(tid)
    return jsonify({"ok": True})

@app.patch("/api/tags/<tid>")
def api_update_tag(tid):
    body = request.get_json(silent=True) or {}
    with lock:
        tags = load_tags()
        for t in tags:
            if t["id"] == tid:
                if "name" in body:
                    name = str(body["name"]).strip()
                    if name:
                        t["name"] = name
                if "hidden" in body:
                    t["hidden"] = bool(body["hidden"])
                if "color" in body:
                    if body["color"]:
                        t["color"] = str(body["color"])
                    else:
                        t.pop("color", None)        # null/"" -> torna al colore di default
                save_tags(tags)
                return jsonify({"ok": True})
    return jsonify({"error": "tag non trovato"}), 404

@app.post("/api/refresh")
def api_refresh():
    if not ENDPOINT_URL:
        return jsonify({"error": "ENDPOINT_URL non configurato (variabile d'ambiente)"}), 400
    days = int((request.get_json(silent=True) or {}).get("days", DAYS_DEFAULT))
    try:
        tags, n = do_refresh(days)
    except Exception as e:
        return jsonify({"error": f"endpoint non raggiungibile: {e}"}), 502
    return jsonify({"tags": [tag_view(t) for t in tags], "reports": n})

@app.post("/api/generate")
def api_generate():
    priv = ec.generate_private_key(ec.SECP224R1())
    d = priv.private_numbers().private_value.to_bytes(28, "big")
    x = priv.public_key().public_numbers().x.to_bytes(28, "big")
    return jsonify({"private": base64.b64encode(d).decode(),
                    "advertisement": base64.b64encode(x).decode(),
                    "carray": carray_decl(x)})

@app.get("/api/settings")
def api_get_settings():
    s = load_settings()
    return jsonify({"mqtt_enabled": bool(s["mqtt_enabled"]), "mqtt_host": s["mqtt_host"],
                    "mqtt_port": int(s["mqtt_port"]), "mqtt_user": s["mqtt_user"],
                    "mqtt_base": s["mqtt_base"], "mqtt_pass_set": bool(s["mqtt_pass"]),
                    "refresh_min": int(s["refresh_min"]), "days": int(s["days"])})

@app.post("/api/settings")
def api_set_settings():
    body = request.get_json(force=True, silent=True) or {}
    s = load_settings()
    if "mqtt_enabled" in body: s["mqtt_enabled"] = bool(body["mqtt_enabled"])
    if "mqtt_host" in body:    s["mqtt_host"] = str(body["mqtt_host"]).strip()
    if "mqtt_port" in body:
        try: s["mqtt_port"] = int(body["mqtt_port"])
        except Exception: pass
    if "mqtt_user" in body:    s["mqtt_user"] = str(body["mqtt_user"]).strip()
    if "mqtt_base" in body:    s["mqtt_base"] = str(body["mqtt_base"]).strip() or "findmy"
    if body.get("mqtt_pass"):  s["mqtt_pass"] = str(body["mqtt_pass"])   # vuoto = invariata
    if "refresh_min" in body:
        try: s["refresh_min"] = max(1, int(body["refresh_min"]))
        except Exception: pass
    if "days" in body:
        try: s["days"] = max(1, min(7, int(body["days"])))
        except Exception: pass
    save_settings(s)
    publish_positions(load_tags())                     # pubblica subito lo stato attuale
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
    if ENDPOINT_URL:
        threading.Thread(target=background_loop, daemon=True).start()
        print(f"[start] auto-refresh ogni {effective_refresh_sec()}s · ritenzione {RETENTION_DAYS} giorni", flush=True)
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=8000)
    except ImportError:
        app.run(host="0.0.0.0", port=8000, threaded=True)
