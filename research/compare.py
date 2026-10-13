import json
from pathlib import Path
from backtest import fetch_ohlcv, walkforward_backtest, summarize_walkforward

OUT = Path(__file__).resolve().parent / "results.json"

def run_all():
    df = fetch_ohlcv("BTC/USDT", "1h", 2000)
    strategies = ["ema_cross", "rsi_ema", "pullback"]
    out = {}

    for s in strategies:
        folds = walkforward_backtest(df, s, folds=4)
        out[s] = {
            "walkforward": folds,
            "summary": summarize_walkforward(folds),
        }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    for s, v in out.items():
        print(f"{s}: {v['summary']}")

if __name__ == "__main__":
    run_all()
