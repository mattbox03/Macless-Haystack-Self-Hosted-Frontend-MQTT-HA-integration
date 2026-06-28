# Find My · Self-Hosted Web UI (per SERVEROS)

Mappa self-hosted per i tuoi beacon Find My DIY, con **storico delle posizioni**.
Pensata per **SERVEROS** (Docker). Tutto il lavoro pesante è **lato server**, quindi è
robusta e si usa da qualsiasi dispositivo.

- **Config via variabile d'ambiente** (`ENDPOINT_URL`) → uguale ovunque, persiste ai riavvii.
- **Tag e storico salvati sul server** (volume) → non legati al browser/dispositivo.
- **Decifratura lato server** (Python, P-224 → X9.63 → AES-GCM) → niente crypto fragile nel
  browser, niente CORS, niente mixed-content. ✅ *verificata con test round-trip.*
- Frontend = **solo una mappa** (Leaflet, in locale) che disegna posizioni + scie storiche.

> ⚠️ Questa UI è un **livello sopra** il tuo endpoint macless-haystack (anisette + endpoint,
> porta 6176), che deve già girare. Non lo sostituisce: lo interroga e ne decifra i report.

## Architettura

```
Mappa (browser)  →  findmy-web (questo)            →  endpoint macless-haystack  →  Apple
                    config + tag + storico + decifra   (auth Apple ID, :6176)
```

## Deploy su SERVER

1. Copia questa cartella sul tuo server.
2. In `docker-compose.yml` imposta **`ENDPOINT_URL`** = l'URL del tuo endpoint macless-haystack
   (es. `http://192.168.1.50:6176`).
3. Avvia:

```bash
docker compose up -d --build
```

4. Apri **`http://IP-DI-SERVER:8123`**.

## Come si usa

1. **+ Aggiungi un tag** → incolla la sua **private key** (Base64). Dal server ricavo l'hash
   per interrogare Apple; la privata resta sul *tuo* server, serve solo a decifrare.
2. **↻ Aggiorna** → scarica i report, li decifra e disegna ogni tag sulla mappa con la sua
   **scia** (tutte le posizioni note) e il punto più recente in evidenza.
3. Lo storico si **accumula** a ogni aggiornamento (salvato nel volume).

Niente tag ancora? Usa **🔑 Genera nuova chiave**: ti dà la private key (per l'app) e
l'**array C** già pronto per `src/main.cpp` dell'ESP32.

**Backup / trasferimento** — «⇅ Importa / Esporta tag»: esporti i tag scelti (con selezione
tramite checkbox) in un file `.json`, e li reimporti — selezionando quali — su un'altra
istanza o dispositivo. Formato proprio (`{type, version, tags:[{name, privateKey}]}`);
l'import è tollerante e prova ad accettare anche altri formati JSON.

## Variabili d'ambiente

| Variabile | Cosa |
|---|---|
| `ENDPOINT_URL` | **obbligatoria** — URL dell'endpoint macless-haystack |
| `ENDPOINT_USER` / `ENDPOINT_PASS` | opzionali — se l'endpoint ha Basic Auth |
| `DAYS` | giorni di storico da chiedere (default 7) |

## File

| File | Cosa |
|---|---|
| `backend/server.py` | Flask: API + decifratura P-224 + storico su volume |
| `web/index.html` + `leaflet.*` | mappa (locale, niente CDN) |
| `Dockerfile` / `docker-compose.yml` | build + config + volume |

## Note

- I report compaiono solo dopo che **altri** iPhone sono passati vicino al beacon (da minuti
  a ore). La decifratura è verificata, ma la prima volta serve **pazienza + traffico**.
- Le **tile** della mappa arrivano da OpenStreetMap (serve internet sullo SERVEROS); le librerie
  JS sono invece in locale.

---

### Bonus: convertire a mano una Advertisement key (Base64) → array C

Dato dal sito https://dchristl.github.io/macless-haystack/ (oppure usa «Genera chiave» nella UI):

```python
import base64
k = base64.b64decode("INCOLLA_QUI_LA_ADVERTISEMENT_KEY")
assert len(k) == 28, f"Errore: {len(k)} byte invece di 28 (forse hai copiato la chiave sbagliata)"
print("static uint8_t findmy_public_key[28] = {")
print("    " + ",\n    ".join(", ".join(f"0x{b:02x}" for b in k[i:i+8]) for i in range(0,28,8)))
print("};")
```
