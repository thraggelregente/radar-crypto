"""Configuration is validated before any worker or trading request starts."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path


def load_env(path=".env"):
    if Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.removeprefix("export ").split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    live: bool = False
    entry_mode: str = "auto"
    data_dir: str = "data"
    persistent_storage: bool = False
    local_stop_ack: bool = False
    watchlist: tuple = ("SOL_USDT", "ETH_USDT", "NEAR_USDT", "SUI_USDT", "AVAX_USDT")
    capital: float = 14.22
    order_cap: float = 5.0
    fee: float = 0.0005
    slippage: float = 0.0005
    max_spread: float = 0.001
    risk_per_trade: float = 0.005
    portfolio_risk: float = 0.01
    max_exposure: float = 0.30
    max_positions: int = 2
    daily_loss: float = 0.02
    max_drawdown: float = 0.08
    interval_sec: int = 3600
    fast: int = 50
    slow: int = 200
    breakout: int = 20
    atr_period: int = 14
    atr_stop: float = 2.0
    atr_target: float = 3.0
    min_stop: float = 0.01
    max_stop: float = 0.06
    min_net_rr: float = 1.2
    min_volume_ratio: float = 1.2
    btc_brake: float = -0.015
    max_hold_hours: int = 72
    signal_ttl: int = 180
    cooldown_sec: int = 3600
    max_quote_age: int = 30
    poll_sec: int = 10
    validation_file: str = "validation.json"
    forward_file: str = "forward_validation.json"
    token: str = ""
    chat_id: str = ""
    allowed_users: tuple = ()
    api_key: str = ""
    api_secret: str = ""

    @classmethod
    def from_env(cls, research=False):
        load_env()
        e = os.environ
        live = e.get("LIVE_TRADING", "false").strip().lower() == "true"
        obj = cls(live=live, entry_mode=e.get("ENTRY_MODE", "semi" if live else "auto"),
                  data_dir=e.get("DATA_DIR", "data"),
                  persistent_storage=e.get("PERSISTENT_STORAGE", "false").lower() == "true",
                  local_stop_ack=e.get("ACK_LOCAL_STOPS", "false").lower() == "true",
                  capital=float(e.get("BOT_CAPITAL_USDT", "14.22")), order_cap=float(e.get("ORDER_USDT", "5")),
                  fee=float(e.get("FEE_RATE", "0.0005")), slippage=float(e.get("SLIPPAGE_RATE", "0.0005")),
                  validation_file=e.get("VALIDATION_FILE", "validation.json"), forward_file=e.get("FORWARD_FILE", "forward_validation.json"),
                  token=e.get("TELEGRAM_BOT_TOKEN", "").strip(), chat_id=e.get("TELEGRAM_CHAT_ID", "").strip(),
                  allowed_users=tuple(x.strip() for x in e.get("TELEGRAM_USER_IDS", e.get("TELEGRAM_CHAT_ID", "")).split(",") if x.strip()),
                  api_key=e.get("PIONEX_API_KEY", "").strip().strip('"').strip("'"),
                  api_secret=e.get("PIONEX_API_SECRET", "").strip().strip('"').strip("'"))
        if research:
            from dataclasses import replace
            obj = replace(obj, live=False, entry_mode="auto", token="", api_key="", api_secret="")
        obj.validate()
        return obj

    def validate(self):
        if self.entry_mode not in {"semi", "auto"}:
            raise ValueError("ENTRY_MODE debe ser semi o auto")
        for name in ("capital", "order_cap", "risk_per_trade", "portfolio_risk", "max_exposure"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} debe ser positivo y finito")
        for name in ("fee", "slippage"):
            if not math.isfinite(getattr(self, name)) or not 0 <= getattr(self, name) <= 0.01:
                raise ValueError(f"{name} fuera de rango")
        if self.live and not (self.api_key and self.api_secret):
            raise ValueError("Faltan credenciales Pionex")
        if self.live and (not self.persistent_storage or not Path(self.data_dir).is_absolute()):
            raise ValueError("LIVE requiere DATA_DIR absoluto en disco persistente y PERSISTENT_STORAGE=true")
        if self.live and not self.local_stop_ack:
            raise ValueError("LIVE requiere ACK_LOCAL_STOPS=true: stops locales dependen del proceso")
        if self.entry_mode == "semi" and not (self.token and self.chat_id and self.allowed_users):
            raise ValueError("Modo semi requiere Telegram y usuarios autorizados")

    def fingerprint(self):
        values = asdict(self)
        for key in ("live", "entry_mode", "data_dir", "persistent_storage", "local_stop_ack", "token", "chat_id", "allowed_users",
                    "api_key", "api_secret", "validation_file", "forward_file"):
            values.pop(key)
        payload = json.dumps(values, sort_keys=True).encode()
        for name in ("config.py", "engine.py", "risk.py", "backtest.py", "execution.py", "data_sources.py",
                     "pionex_client.py", "main.py", "net.py", "state.py", "telegram_client.py", "forward_report.py"):
            path = Path(__file__).with_name(name)
            if path.exists():
                payload += path.read_bytes()
        return hashlib.sha256(payload).hexdigest()
