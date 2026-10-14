"""Qrypt quickstart: generate an AES-256 key from Qrypt quantum entropy.

Requires Python 3.10+ and: pip install requests cryptography python-dotenv
Run: python quickstart.py
"""
import base64
import os
import sys

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

HELP = "https://example.invalid/quickstart/troubleshooting/"
HINTS = {401: "Authorization header missing or malformed",
         403: "key rejected, or monthly entropy quota used up",
         429: "over 30 requests in 10 seconds; wait, then run once"}


def fail(msg, anchor):
    sys.exit(f"✘ {msg}\n  Fix: {HELP}#{anchor}")


load_dotenv()
token = os.getenv("QRYPT_API_KEY", "").strip()
if not token:
    fail("QRYPT_API_KEY is not set. Add QRYPT_API_KEY=<your key> to .env.", "no-key")

url = os.getenv("QRYPT_API_URL", "https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1")
headers = {"Accept": "application/json", "Authorization": f"Bearer {token}",
           "User-Agent": "qrypt-quickstart-python/1.0"}
try:
    r = requests.get(url, headers=headers, timeout=15)
except requests.RequestException as e:
    fail(f"Could not reach Qrypt ({type(e).__name__}).", "network")
hint = HINTS.get(r.status_code, "try again shortly" if r.status_code >= 500 else "unexpected")
if not r.ok:
    fail(f"Qrypt returned HTTP {r.status_code}: {hint}.", r.status_code)

# The first 32 bytes (256 bits) of the 1 KiB block become the key. Never reuse the rest.
aes_key = AESGCM(base64.b64decode(r.json()["random"][0])[:32])
nonce, message = os.urandom(12), b"Hello from Qrypt quantum entropy"
if aes_key.decrypt(nonce, aes_key.encrypt(nonce, message, None), None) != message:
    fail("AES-GCM round trip failed.", "round-trip")
print("✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK).")
