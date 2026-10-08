import time
import requests
import pandas as pd

BASE_PIONEX = "https://api.pionex.com"

def get_market_data(symbol="SOLUSDT"):
    """
    Obtiene velas y volumen directamente de Pionex Spot.
    Transforma símbolos como 'SOLUSDT' a 'SOL_USDT'.
    """
    try:
        sym = f"{symbol[:-4]}_{symbol[-4:]}" if "_" not in symbol else symbol
        url = f"{BASE_PIONEX}/api/v1/market/klines?symbol={sym}&interval=15m&limit=100"
        res = requests.get(url, timeout=6).json()
        
        klines = res.get("data", {}).get("klines", [])
        if not klines:
            return pd.DataFrame()
            
        df = pd.DataFrame(klines)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
            
        return df
    except Exception as e:
        print(f"Error en get_market_data({symbol}): {e}")
        return pd.DataFrame()

def get_funding_and_sentiment(symbol="SOLUSDT"):
    """Devuelve tasa neutral y volumen relativo para evaluar sesgo."""
    return {
        "funding_rate": 0.01,
        "bias": "NEUTRAL",
        "funding_raw": "0.0100%"
    }

def get_fear_and_greed():
    """Consulta el índice macro Fear & Greed de Alternative.me."""
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        res = requests.get(url, timeout=5).json()
        val = int(res["data"][0]["value"])
        clas = res["data"][0]["value_classification"]
        return {"value": val, "sentiment": clas}
    except Exception:
        return {"value": 50, "sentiment": "Neutral"}

def check_btc_health():
    """Comprueba si Bitcoin está en tendencia favorable vía Pionex."""
    try:
        df = get_market_data("BTCUSDT")
        if df.empty:
            return {"healthy": True, "btc_price": 0.0}
            
        current_p = float(df["close"].iloc[-1])
        ema_50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
        
        return {
            "healthy": current_p >= ema_50,
            "btc_price": current_p
        }
    except Exception:
        return {"healthy": True, "btc_price": 0.0}
