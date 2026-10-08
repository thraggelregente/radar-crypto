import os
import time
import hmac
import hashlib
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("PIONEX_API_KEY", "").strip().strip('"').strip("'")
API_SECRET = os.getenv("PIONEX_API_SECRET", "").strip().strip('"').strip("'")

BASE_URL = "https://api.pionex.com"
PATH = "/api/v1/account/balances"

# Timestamp actual en ms
timestamp = int(time.time() * 1000)
query = f"timestamp={timestamp}"

# Cadena canónica oficial: GET + PATH + ? + QUERY
signature_payload = f"GET{PATH}?{query}"

signature = hmac.new(
    API_SECRET.encode("utf-8"),
    signature_payload.encode("utf-8"),
    hashlib.sha256
).hexdigest()

headers = {
    "PIONEX-KEY": API_KEY,
    "PIONEX-SIGNATURE": signature,
    "Content-Type": "application/json"
}

url = f"{BASE_URL}{PATH}?{query}"

print(f"Payload firmado: {signature_payload}")
response = requests.get(url, headers=headers)

print("Status code:", response.status_code)
print("Respuesta:", response.json())