import requests
import pandas as pd

BASE_URL = "https://api.pionex.com"

def get_klines(symbol="SOL_USDT", interval="15m", limit=100):
    """Obtiene velas klines directamente desde Pionex (sin bloqueo de EE.UU.)."""
    try:
        # Formato Pionex: SOL_USDT
        sym = symbol if "_" in symbol else f"{symbol[:-4]}_{symbol[-4:]}"
        url = f"{BASE_URL}/api/v1/market/klines?symbol={sym}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=5).json()
        
        if not res.get("result", False):
            return pd.DataFrame()
            
        data = res.get("data", {}).get("klines", [])
        if not data:
            return pd.DataFrame()
            
        df = pd.DataFrame(data)
        df["close"] = df["close"].astype(float)
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except Exception as e:
        print(f"Error klines Pionex: {e}")
        return pd.DataFrame()

def get_current_price(symbol="SOL_USDT"):
    """Obtiene el precio actual en vivo desde Pionex."""
    try:
        sym = symbol if "_" in symbol else f"{symbol[:-4]}_{symbol[-4:]}"
        url = f"{BASE_URL}/api/v1/market/trades?symbol={sym}&limit=1"
        res = requests.get(url, timeout=5).json()
        trades = res.get("data", {}).get("trades", [])
        if trades:
            return float(trades[0]["price"])
        return 0.0
    except Exception as e:
        print(f"Error precio Pionex: {e}")
        return 0.0

def get_funding_rate(symbol="SOL_USDT"):
    return 0.01  # Valor neutral por defecto para Spot