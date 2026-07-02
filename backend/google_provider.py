"""
Google Find Hub provider, parallel to the Apple provider.

This module contains no Google cryptography or protocol logic. It talks only
to the `google-find-hub-sync` HTTP sidecar (microservice.py), which handles
authentication, FCM, and decryption. The backend uses two focused functions:

    list_available()      -> Google trackers registered to the account
    fetch_locations(id)   -> normalized position events

Environment configuration:
    GOOGLE_URL    e.g. http://192.168.1.50:5500
    GOOGLE_TOKEN  bearer token matching the sidecar AUTH_TOKEN
"""
import os
import time
import requests

GOOGLE_URL   = os.environ.get("GOOGLE_URL", "").strip().rstrip("/")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN", "")


def _base_url(base_url=None):
    return (base_url if base_url is not None else GOOGLE_URL).strip().rstrip("/")


def _token(token=None):
    return token if token is not None else GOOGLE_TOKEN


def configured(base_url=None, token=None):
    return bool(_base_url(base_url) and _token(token))


def _headers(token=None):
    return {"Authorization": f"Bearer {_token(token)}"}


def list_available(timeout=20, base_url=None, token=None):
    """Return account trackers from the sidecar, or [] when not configured."""
    url = _base_url(base_url)
    auth_token = _token(token)
    if not configured(url, auth_token):
        return []
    r = requests.get(f"{url}/devices", headers=_headers(auth_token), timeout=timeout)
    r.raise_for_status()
    return r.json().get("devices", [])


def fetch_locations(device_id, timeout=35, base_url=None, token=None):
    """Fetch and normalize every location returned by the Google sidecar."""
    url = _base_url(base_url)
    auth_token = _token(token)
    if not configured(url, auth_token):
        return None
    r = requests.get(f"{url}/devices/{device_id}/location",
                     headers=_headers(auth_token), timeout=timeout)
    r.raise_for_status()
    received_at = int(time.time() * 1000)
    normalized = []
    for location in r.json().get("locations", []):
        if "latitude" not in location or "longitude" not in location:
            continue
        normalized.append(
            {
                "latitude": float(location["latitude"]),
                "longitude": float(location["longitude"]),
                "accuracy": float(location.get("accuracy", 0) or 0),
                "timestamp": int(location.get("time", 0)) * 1000,
                "received_at": received_at,
                "altitude": location.get("altitude"),
                "metadata": {
                    "status": location.get("status"),
                    "is_own_report": location.get("is_own_report"),
                },
            }
        )
    return normalized
