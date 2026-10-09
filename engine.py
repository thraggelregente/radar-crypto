"""Cálculo de indicadores y señales técnicas en Spot sin contradicciones."""
import numpy as np
import pandas as pd
from data_sources import get_klines, get_live_price

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    rsi[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return rsi

def evaluate_strategy(symbol="SOLUSDT", interval="15m"):
    df = get_klines(symbol, interval, limit=100)
    if len(df) < 40:
        return {"ok": False, "symbol": symbol, "reason": "datos insuficientes"}

    closed = df.iloc[:-1].copy()
    closed["rsi"] = calculate_rsi(closed["close"], 14)
    ema_fast = closed["close"].ewm(span=9, adjust=False).mean().iloc[-1]
    ema_slow = closed["close"].ewm(span=21, adjust=False).mean().iloc[-1]
    last_rsi = closed["rsi"].iloc[-1]

    if pd.isna(last_rsi):
        return {"ok": False, "symbol": symbol, "reason": "RSI no calculable"}
    last_rsi = round(float(last_rsi), 2)

    avg_volume = closed["volume"].iloc[-21:-1].mean()
    last_volume = closed["volume"].iloc[-1]
    volume_ratio = round(float(last_volume / avg_volume), 2) if avg_volume > 0 else 1.0

    price = get_live_price(symbol) or float(df["close"].iloc[-1])

    score = 0
    signals = []

    # Condición 1: Rebote de sobreventa (Mean Reversion)
    if last_rsi <= 32:
        score += 50
        signals.append(f"RSI sobreventa ({last_rsi}) - Oportunidad de rebote")
    elif last_rsi <= 40:
        score += 25
        signals.append(f"RSI en zona baja ({last_rsi})")

    # Condición 2: Impulso alcista de tendencia (Trend Following)
    if ema_fast > ema_slow:
        score += 30
        signals.append("Tendencia alcista (EMA9 > EMA21)")
        if 45 <= last_rsi <= 65:
            score += 20
            signals.append("RSI en zona óptima de impulso")
    
    # Condición 3: Confirmación de volumen
    if volume_ratio >= 1.3:
        score += 15
        signals.append(f"Volumen destacado ({volume_ratio}x)")

    return {
        "ok": True,
        "symbol": symbol,
        "price": float(price),
        "rsi": last_rsi,
        "ema_fast": float(ema_fast),
        "ema_slow": float(ema_slow),
        "volume_ratio": volume_ratio,
        "score": score,
        "signals": signals,
    }
