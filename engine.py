"""Cálculo bidireccional (LONG y SHORT) con lógica técnica no contradictoria."""
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

    score_long = 0
    signals_long = []
    score_short = 0
    signals_short = []

    # --- Evaluación LONG ---
    if last_rsi <= 32:
        score_long += 40
        signals_long.append(f"RSI sobreventa ({last_rsi})")
    elif ema_fast > ema_slow and 45 <= last_rsi <= 65:
        score_long += 35
        signals_long.append("Tendencia alcista (EMA9 > EMA21)")
        if volume_ratio >= 1.2:
            score_long += 15
            signals_long.append(f"Volumen confirmando ({volume_ratio}x)")

    # --- Evaluación SHORT ---
    if last_rsi >= 68:
        score_short += 40
        signals_short.append(f"RSI sobrecompra ({last_rsi})")
    elif ema_fast < ema_slow and 35 <= last_rsi <= 55:
        score_short += 35
        signals_short.append("Tendencia bajista (EMA9 < EMA21)")
        if volume_ratio >= 1.2:
            score_short += 15
            signals_short.append(f"Volumen bajista ({volume_ratio}x)")

    # Selección de dirección predominante
    if score_long >= score_short:
        direction = "LONG"
        net_score = score_long
        active_signals = signals_long
    else:
        direction = "SHORT"
        net_score = score_short
        active_signals = signals_short

    return {
        "ok": True,
        "symbol": symbol,
        "price": float(price),
        "rsi": last_rsi,
        "ema_fast": float(ema_fast),
        "ema_slow": float(ema_slow),
        "volume_ratio": volume_ratio,
        "score": net_score,
        "direction": direction,
        "signals": active_signals,
    }
