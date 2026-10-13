"""Portfolio limits and evidence gates; no doubling down or leverage."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from pionex_client import floor_string


ACTIVE = {"PREPARED", "UNKNOWN", "OPEN"}


def active_orders(state):
    return [o for o in state["orders"].values() if o["status"] in ACTIVE]


def equity(state, prices, cfg):
    total = state["cash"]
    for symbol, pos in state["positions"].items():
        if symbol not in prices:
            raise ValueError("No se puede valorar la cartera con precios faltantes")
        total += pos["qty"] * prices[symbol] * (1 - cfg.fee)
    for symbol, pos in state.get("dust", {}).items():
        if symbol not in prices:
            raise ValueError("No se puede valorar residuo sin precio")
        total += pos["qty"] * prices[symbol] * (1 - cfg.fee)
    return total


def update_limits(state, value, cfg, now=None):
    stamp = time.time() if now is None else now
    day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
    if state["day"] != day:
        state["day"], state["day_start"] = day, value
        state["daily_block"] = False
    state["high_water"] = max(state["high_water"], value)
    if value < state["high_water"] * (1 - cfg.max_drawdown):
        state["halt"] = "Drawdown máximo: requiere revisión; /resume no borra este bloqueo"
    # Daily loss blocks buys; existing positions still have automatic exits.
    state["daily_block"] = state.get("daily_block", False) or value <= state["day_start"] * (1 - cfg.daily_loss)


def position_size(state, signal, rules, cfg, prices, free_usdt=None):
    if state["halt"] or state["manual_pause"] or state.get("daily_block") or active_orders(state):
        return 0.0
    if len(state["positions"]) >= cfg.max_positions or signal["symbol"] in state["positions"]:
        return 0.0
    value = equity(state, prices, cfg)
    exposure = sum(p["qty"] * prices[s] for s, p in state["positions"].items())
    exposure += sum(p["qty"] * prices[s] for s, p in state.get("dust", {}).items())
    cost = 2 * (cfg.fee + cfg.slippage) + cfg.max_spread
    risk_used = sum(p["cost"] * (p["stop_pct"] + cost) for p in state["positions"].values())
    risk_budget = min(value * cfg.risk_per_trade, max(0, value * cfg.portfolio_risk - risk_used))
    amount = min(cfg.order_cap, risk_budget / (signal["stop_pct"] + cost),
                 max(0, value * cfg.max_exposure - exposure), max(0, state["cash"] / (1 + cfg.fee)))
    if free_usdt is not None:
        amount = min(amount, free_usdt / (1 + cfg.fee))
    amount = float(floor_string(max(0, amount), rules["amountPrecision"]))
    # Never increase capital/risk to satisfy an exchange minimum.
    if amount < rules["minAmount"]:
        return 0.0
    return amount


def evidence_gate(cfg, auto=False, now=None):
    if not cfg.live:
        return True, "paper"
    now = time.time() if now is None else now
    try:
        r = json.loads(Path(cfg.validation_file).read_text())
        base = r["oos"]
        checks = [r["source"] == "Pionex public API", r["fingerprint"] == cfg.fingerprint(),
                  r["approved"] is True, r["trials"] == 1, 0 <= now - r["generated"] <= 30 * 86400,
                  0 <= now - r["data_end"] <= 7 * 86400,
                  r["history_days"] >= 180, base["trades"] >= 100, base["expectancy_lower_95"] > 0,
                  base["profit_factor"] >= 1.2, base["max_drawdown"] <= 0.10,
                  r["stress"]["net_return"] > 0, r["positive_folds"] >= 2]
        if not all(checks):
            return False, "Evidencia histórica insuficiente o vencida"
        if auto:
            f = json.loads(Path(cfg.forward_file).read_text())
            checks = [f["source"] == "paper journal", f["fingerprint"] == cfg.fingerprint(), f["approved"] is True,
                      0 <= now - f["generated"] <= 7 * 86400, f["days"] >= 90, f["observed_days"] >= 90,
                      f["trades"] >= 50, f["expectancy_lower_95"] > 0, f["profit_factor"] >= 1.2,
                      f["max_drawdown"] <= cfg.max_drawdown]
            if not all(checks):
                return False, "Evidencia forward insuficiente o vencida"
    except (OSError, ValueError, KeyError, TypeError):
        return False, "Falta evidencia verificable: compras LIVE bloqueadas"
    return True, "validación vigente (no garantiza rentabilidad)"
