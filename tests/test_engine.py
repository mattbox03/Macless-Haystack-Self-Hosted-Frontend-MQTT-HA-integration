import base64
import importlib
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def apple_payload(private_key, latitude, longitude, timestamp):
    receiver_public = private_key.public_key()
    ephemeral_private = ec.generate_private_key(ec.SECP224R1())
    ephemeral_public = ephemeral_private.public_key().public_bytes(
        encoding=__import__(
            "cryptography.hazmat.primitives.serialization",
            fromlist=["Encoding"],
        ).Encoding.X962,
        format=__import__(
            "cryptography.hazmat.primitives.serialization",
            fromlist=["PublicFormat"],
        ).PublicFormat.UncompressedPoint,
    )
    shared = ephemeral_private.exchange(ec.ECDH(), receiver_public)
    digest = hashes.Hash(hashes.SHA256())
    digest.update(shared + b"\x00\x00\x00\x01" + ephemeral_public)
    derived = digest.finalize()
    plaintext = (
        struct.pack(">i", round(latitude * 1e7))
        + struct.pack(">i", round(longitude * 1e7))
        + bytes([12, 0])
    )
    encryptor = Cipher(
        algorithms.AES(derived[:16]),
        modes.GCM(derived[16:]),
    ).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    apple_epoch = int(timestamp / 1000) - 978307200
    payload = (
        struct.pack(">I", apple_epoch)
        + b"\x00"
        + ephemeral_public
        + ciphertext
        + encryptor.tag
    )
    return base64.b64encode(payload).decode()


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class MultiProviderEngineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["DATA_DIR"] = self.temp.name
        os.environ["WEB_DIR"] = str(ROOT / "web")
        os.environ["ENDPOINT_URL"] = "http://apple.test"
        os.environ["GOOGLE_URL"] = "http://google.test"
        os.environ["GOOGLE_TOKEN"] = "test-token"
        for module in ("server", "google_provider", "event_store"):
            sys.modules.pop(module, None)
        self.server = importlib.import_module("server")
        self.client = self.server.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_dual_provider_refresh_normalizes_and_stores_events(self):
        apple_private = ec.generate_private_key(ec.SECP224R1())
        private_bytes = apple_private.private_numbers().private_value.to_bytes(28, "big")
        private_b64 = base64.b64encode(private_bytes).decode()

        response = self.client.post(
            "/api/devices",
            json={
                "name": "Scooter",
                "applePrivateKey": private_b64,
                "googleId": "google-canonic-id",
                "googlePublicKey": "google-public-key",
            },
        )
        self.assertEqual(response.status_code, 200)
        device_id = response.get_json()["id"]
        settings = self.client.post(
            "/api/settings",
            json={
                "apple_host": "10.0.0.21",
                "apple_port": 6177,
                "google_host": "10.0.0.22",
                "google_port": 5501,
            },
        )
        self.assertEqual(settings.status_code, 200)
        config = self.client.get("/api/config").get_json()
        self.assertEqual(config["apple"]["endpoint"], "http://10.0.0.21:6177")
        self.assertEqual(config["google"]["endpoint"], "http://10.0.0.22:5501")

        now = int(time.time() * 1000)
        encrypted = apple_payload(apple_private, 45.4642, 9.19, now - 2000)
        google_locations = [
            {
                "latitude": 45.465,
                "longitude": 9.191,
                "accuracy": 8,
                "timestamp": now - 1000,
                "received_at": now,
                "altitude": 110,
                "metadata": {"is_own_report": False},
            }
        ]

        with patch.object(
            self.server.requests,
            "post",
            return_value=FakeResponse({"results": [{"payload": encrypted}]}),
        ) as apple_request, patch.object(
            self.server.google_provider,
            "fetch_locations",
            return_value=google_locations,
        ) as google_request:
            refresh = self.client.post("/api/refresh", json={"days": 7})

        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(apple_request.call_args.args[0], "http://10.0.0.21:6177")
        self.assertEqual(
            google_request.call_args.kwargs["base_url"], "http://10.0.0.22:5501"
        )
        body = refresh.get_json()
        self.assertEqual(body["providers"]["apple"]["inserted"], 1)
        self.assertEqual(body["providers"]["google"]["inserted"], 1)

        devices = self.client.get("/api/devices").get_json()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["id"], device_id)
        self.assertEqual({event["source"] for event in devices[0]["history"]}, {"apple", "google"})
        self.assertEqual(devices[0]["google"]["public_key"], "google-public-key")
        self.assertNotIn("carray", devices[0])

        generated = self.client.post("/api/generate").get_json()
        self.assertEqual(set(generated), {"private", "advertisement"})
        self.assertEqual(len(base64.b64decode(generated["advertisement"])), 28)

        apple_only = self.client.get("/api/devices?source=apple").get_json()
        self.assertEqual([event["source"] for event in apple_only[0]["history"]], ["apple"])
        google_only = self.client.get("/api/devices?source=google").get_json()
        self.assertEqual([event["source"] for event in google_only[0]["history"]], ["google"])

        events = self.client.get(f"/api/events?device_id={device_id}").get_json()
        self.assertEqual(len(events), 2)
        required = {
            "device_id",
            "tracker_id",
            "source",
            "latitude",
            "longitude",
            "accuracy",
            "timestamp",
            "received_at",
        }
        self.assertTrue(all(required.issubset(event) for event in events))

        mqtt = self.server._mqtt_event_msgs(events)
        payloads = [json.loads(message["payload"]) for message in mqtt]
        self.assertEqual({payload["source"] for payload in payloads}, {"apple", "google"})

    def test_google_ui_settings_drive_real_provider_client(self):
        response = self.client.post(
            "/api/settings",
            json={
                "google_host": "10.20.30.40",
                "google_port": 5510,
                "google_token": "token-from-ui",
            },
        )
        self.assertEqual(response.status_code, 200)

        public_settings = self.client.get("/api/settings").get_json()
        self.assertTrue(public_settings["google_token_set"])
        self.assertNotIn("google_token", public_settings)

        with patch.object(
            self.server.google_provider.requests,
            "get",
            return_value=FakeResponse({"devices": [{"id": "g1", "name": "Tracker"}]}),
        ) as google_http:
            devices = self.client.get("/api/google/devices")

        self.assertEqual(devices.status_code, 200)
        self.assertEqual(
            google_http.call_args.args[0], "http://10.20.30.40:5510/devices"
        )
        self.assertEqual(
            google_http.call_args.kwargs["headers"]["Authorization"],
            "Bearer token-from-ui",
        )

    def test_apple_advertisement_requires_and_accepts_matching_private_key(self):
        first_private = ec.generate_private_key(ec.SECP224R1())
        first_private_b64 = base64.b64encode(
            first_private.private_numbers().private_value.to_bytes(28, "big")
        ).decode()
        created = self.client.post(
            "/api/devices",
            json={"name": "Replaceable", "applePrivateKey": first_private_b64},
        )
        self.assertEqual(created.status_code, 200)
        device_id = created.get_json()["id"]

        replacement = ec.generate_private_key(ec.SECP224R1())
        replacement_private_b64 = base64.b64encode(
            replacement.private_numbers().private_value.to_bytes(28, "big")
        ).decode()
        replacement_advertisement = base64.b64encode(
            replacement.public_key().public_numbers().x.to_bytes(28, "big")
        ).decode()
        updated = self.client.patch(
            f"/api/devices/{device_id}",
            json={
                "applePrivateKey": replacement_private_b64,
                "appleAdvertisementKey": replacement_advertisement,
            },
        )
        self.assertEqual(updated.status_code, 200)
        device = self.client.get("/api/devices").get_json()[0]
        self.assertEqual(device["advertisement"], replacement_advertisement)

        advertisement_only = self.client.patch(
            f"/api/devices/{device_id}",
            json={"appleAdvertisementKey": replacement_advertisement},
        )
        self.assertEqual(advertisement_only.status_code, 400)

        unrelated = ec.generate_private_key(ec.SECP224R1())
        unrelated_advertisement = base64.b64encode(
            unrelated.public_key().public_numbers().x.to_bytes(28, "big")
        ).decode()
        mismatch = self.client.patch(
            f"/api/devices/{device_id}",
            json={
                "applePrivateKey": replacement_private_b64,
                "appleAdvertisementKey": unrelated_advertisement,
            },
        )
        self.assertEqual(mismatch.status_code, 400)


if __name__ == "__main__":
    unittest.main()
