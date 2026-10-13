@echo off
setlocal
cd /d "%~dp0"
set LIVE_TRADING=false
set ENTRY_MODE=auto
set BOT_CAPITAL_USDT=14.22
set FEE_RATE=0.0005
echo Solo datos publicos. No envia ordenes ni requiere claves.
python download_history.py --directory history_pionex --days 365
if errorlevel 1 (
  echo Pionex no disponible. Probando archivos oficiales de Binance.
  python download_binance.py --directory history_binance --months 36
  if errorlevel 1 goto failed
  powershell -NoProfile -Command "Compress-Archive -Path 'history_binance\*.csv','history_binance\manifest.json' -DestinationPath 'datos_radar.zip' -Force"
) else (
  powershell -NoProfile -Command "Compress-Archive -Path 'history_pionex\*.csv','history_pionex\manifest.json' -DestinationPath 'datos_radar.zip' -Force"
)
if errorlevel 1 goto failed
echo LISTO: datos_radar.zip en esta carpeta. No contiene tu .env ni tus claves.
pause
exit /b 0
:failed
echo No se completo la descarga. Copia el error de esta ventana.
pause
exit /b 1
