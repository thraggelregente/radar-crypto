@echo off
setlocal
cd /d "%~dp0"
set LIVE_TRADING=false
set ENTRY_MODE=auto
set BOT_CAPITAL_USDT=14.22
set FEE_RATE=0.0005
echo Consulta publica de minimos y precios. No necesita API keys ni envia ordenes.
python pionex_snapshot.py
pause
