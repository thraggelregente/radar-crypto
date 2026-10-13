import json
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from backtest import fetch_ohlcv


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    true_range = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def to_4h(df: pd.DataFrame) -> pd.DataFrame:
    out = df.resample("4h").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    })
    return out


def trend_filter_4h(df_1h: pd.DataFrame):
    df_4h = to_4h(df_1h)
    close_4h = df_4h["Close"]
    ema_fast_4h = ema(close_4h, 20)
    ema_slow_4h = ema(close_4h, 50)
    trend_up = ema_fast_4h > ema_slow_4h
    trend_down = ema_fast_4h < ema_slow_4h
    return trend_up, trend_down, df_4h


def strategy_4h_trend_1h_breakout(df: pd.DataFrame):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # 4h trend filter
    trend_up, trend_down, df_4h = trend_filter_4h(df)

    # align trend to 1h data using last value of each 4h bar
    trend_up_1h = pd.Series(False, index=df.index)
    trend_down_1h = pd.Series(False, index=df.index)

    # map trend status from 4h to 1h
    for ts, val in trend_up.items():
        idx = df.index[df.index >= ts]
        if len(idx) == 0:
            continue
        trend_up_1h.loc[idx] = val
        trend_down_1h.loc[idx] = False

    for ts, val in trend_down.items():
        idx = df.index[df.index >= ts]
        if len(idx) == 0:
            continue
        trend_down_1h.loc[idx] = val
        trend_up_1h.loc[idx] = False

    # 1h technicals
    ema_fast = ema(close, 20)
    ema_slow = ema(close, 50)
    atr14 = atr(high, low, close, period=14)
    atr_med = atr14.rolling(50, min_periods=20).median()

    rolling_high = close.rolling(20, min_periods=10).max()
    rolling_low = close.rolling(20, min_periods=10).min()

    long_entry = (
        (close > rolling_high.shift(1))
        & (ema_fast > ema_slow)
        & trend_up_1h
        & (atr14 > atr_med * 0.8)
    )

    short_entry = (
        (close < rolling_low.shift(1))
        & (ema_fast < ema_slow)
        & trend_down_1h
        & (atr14 > atr_med * 0.8)
    )

    # exit when trend fades or against signal
    long_exit = (ema_fast < ema_slow) | (close < ema_fast)
    short_exit = (ema_fast > ema_slow) | (close > ema_fast)

    entries = np.where(long_entry.to_numpy(), 1, np.where(short_entry.to_numpy(), -1, 0))
    exits = np.where(long_exit.to_numpy(), 1, np.where(short_exit.to_numpy(), -1, 0))

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        init_cash=10000,
        fees=0.001,
        slippage=0.0005,
        freq="1h",
    )

    stats = pf.stats()
    trades = int(stats.get("Total Trades", 0) or 0)
    return {
        "strategy": "4h_trend_1h_breakout",
        "total_return": float(stats.get("Total Return [%]", 0.0)),
        "cagr": float(stats.get("CAGR [%]", 0.0)),
        "sharpe": float(stats.get("Sharpe Ratio", 0.0)),
        "max_drawdown": float(stats.get("Max Drawdown [%]", 0.0)),
        "win_rate": float(stats.get("Win Rate [%]", 0.0)),
        "trades": trades,
        "profit_factor": float(stats.get("Profit Factor", 0.0)),
    }


def walkforward_backtest(df: pd.DataFrame, folds: int = 4):
    n = len(df)
    if n < 500:
        raise ValueError(f"Not enough data: {n}")

    test_size = max(200, n // 6)
    test_size = min(test_size, n // 3)
    base_start = int(n * 0.35)
    end_anchor = n - test_size
    step = max(1, (end_anchor - base_start) // max(1, folds - 1))

    out = []
    for i in range(folds):
        train_end = min(base_start + i * step, end_anchor)
        test_start = train_end
        test_end = min(test_start + test_size, n)
        if test_end <= test_start:
            continue

        train = df.iloc[:train_end]
        test = df.iloc[test_start:test_end]

        out.append({
            "fold": i + 1,
            "train": strategy_4h_trend_1h_breakout(train),
            "test": strategy_4h_trend_1h_breakout(test),
        })

    return out


def main():
    df = fetch_ohlcv("BTC/USDT", "1h", 2000)
    results = walkforward_backtest(df, folds=4)
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
