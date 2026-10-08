import os
import time
import hmac
import hashlib
import json
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("PIONEX_API_KEY", "").strip().strip('"').strip("'")
API_SECRET = os.getenv("PIONEX_API_SECRET", "").strip().strip('"').strip("'")
BASE_URL = "https://api.pionex.com"

# Precisión de decimales para órdenes en Pionex Spot
DECIMALS = {
    "SOL_USDT": 4,
    "ETH_USDT": 4,
    "NEAR_USDT": 2,
    "SUI_USDT": 2,
    "AVAX_USDT": 3,
    "BTC3S_USDT": 2,
    "ETH3S_USDT": 2,
    "SOL3S_USDT": 2
}

def _get_server_time():
    try:
        r = requests.get(f"{BASE_URL}/api/v1/common/timestamp", timeout=5).json()
        return r.get("timestamp") or r.get("data", {}).get("timestamp") or int(time.time() * 1000)
    except Exception:
        return int(time.time() * 1000)

def get_account_balances():
    """Consulta balances Spot en Pionex."""
    path = "/api/v1/account/balances"
    timestamp = _get_server_time()
    query = f"timestamp={timestamp}"
    
    signature_payload = f"GET{path}?{query}"
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
    try:
        url = f"{BASE_URL}{path}?{query}"
        res = requests.get(url, headers=headers, timeout=10)
        return res.json()
    except Exception as e:
        return {"result": False, "message": str(e)}

def get_balances_dict():
    """Devuelve saldos libres formateados."""
    res = get_account_balances()
    balances = {"USDT": 0.0, "SOL": 0.0, "ETH": 0.0, "NEAR": 0.0, "SUI": 0.0, "AVAX": 0.0}
    if not res.get("result"):
        return balances
    for b in res.get("data", {}).get("balances", []):
        coin = b.get("coin")
        balances[coin] = float(b.get("free", 0.0))
    return balances

def place_market_order(symbol: str, side: str, amount_usdt: float = 5.0, size_coin: float = None):
    """Envía una orden SPOT a mercado (BUY o SELL)."""
    path = "/api/v1/trade/order"
    timestamp = _get_server_time()
    query = f"timestamp={timestamp}"
    
    body = {
        "symbol": symbol,
        "side": side.upper(),
        "type": "MARKET"
    }
    if side.upper() == "BUY":
        body["amount"] = str(amount_usdt)
    elif side.upper() == "SELL":
        decimals = DECIMALS.get(symbol, 2)
        if size_coin is not None:
            body["size"] = f"{size_coin:.{decimals}f}"
        
    body_str = json.dumps(body, separators=(',', ':'))
    signature_payload = f"POST{path}?{query}{body_str}"
    
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
    try:
        url = f"{BASE_URL}{path}?{query}"
        res = requests.post(url, data=body_str, headers=headers, timeout=10)
        return res.json()
    except Exception as e:
        return {"result": False, "message": str(e)}