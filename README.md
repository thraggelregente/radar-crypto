# Radar Crypto: ejecución verificable y estrategia experimental

Python 3.11+, sin dependencias externas. Pionex Spot, solo LONG, sin apalancamiento.

**Estado de investigación: rentabilidad no demostrada.** La descarga pública de Pionex devolvió HTTP 403 desde el entorno de desarrollo. Las pruebas offline verifican comportamiento del programa, no retornos futuros. No se incluyen resultados históricos inventados ni una aprobación para dinero real.

La investigación, análisis financiero y decisión de automatización están en [RESEARCH.md](RESEARCH.md). Cambios y comprobaciones: [VALIDATION.md](VALIDATION.md).

## Decisión operativa

| Etapa | Entradas | Salidas | Requisitos |
|---|---|---|---|
| Ahora: paper | Automáticas por defecto | Automáticas | Datos de Pionex válidos y mínimos/riesgo compatibles |
| Piloto con dinero real | Semiautomáticas por defecto | Automáticas | Evidencia histórica vigente, almacenamiento persistente y configuración explícita |
| Automático real | Automáticas | Automáticas | Lo anterior más 90 días y 50 operaciones paper con evidencia favorable |

Los stops son **locales**, no órdenes protectoras alojadas en Pionex. Si el proceso, red o hosting falla, no se garantiza la salida. Un stop tampoco garantiza precio ni pérdida máxima. No usar un hosting que se duerma para posiciones reales. Una sola réplica/proceso; el bloqueo de archivo impide duplicación local, no coordina hosts distintos.

## Inicio

```bash
cp .env.example .env
python main.py
```

`LIVE_TRADING=false` simula compras y ventas con comisiones, spread observable y slippage configurado. No llama endpoints privados de Pionex. `ENTRY_MODE=auto` permite ensayar la estrategia sin seleccionar retrospectivamente qué señales seguir. Telegram es opcional en paper automático; sus notificaciones sí son mensajes reales cuando se configura un token.

`ORDER_USDT=5` conserva el límite original. **Si el mínimo del par es superior, no habrá operación.** El bot nunca aumenta este importe para satisfacer al exchange. `BOT_CAPITAL_USDT=100` es el presupuesto asignado al bot, no una lectura de todo tu patrimonio. Las tenencias externas no se incorporan automáticamente.

Configuración disponible: `.env.example`. No subir `.env`, claves, bases de datos o journals al repositorio. Configurar permisos de lectura y trading adecuados en Pionex; evitar usar la misma cuenta para otras operaciones sobre los pares vigilados durante la prueba.

## Telegram

- `/status`: modo, posiciones, pendientes y estado de datos.
- `/pause`: pausa compras; **las salidas siguen activas**.
- `/resume`: levanta solamente la pausa manual; no elimina bloqueos de riesgo/conciliación.
- `/help`: ayuda.

En modo semi los botones de compra vencen en 180 segundos y pertenecen a una señal durable. Se revalidan datos, BTC, cotización, liquidez, mínimos y riesgo antes de operar. No hay botones de venta antiguos que puedan cerrar otra posición del mismo par.

## Estado, conciliación y recuperación

`DATA_DIR/paper.sqlite3` o `DATA_DIR/live.sqlite3` contiene posiciones, órdenes, señales, cash del bot, residuo de redondeo, trades y límites de cartera. SQLite usa transacciones y WAL con sincronización FULL. Un error de lectura nunca se interpreta como cuenta vacía.

Antes de un POST se guarda intención e ID único. Un timeout conserva la orden como UNKNOWN/PREPARED y consulta el mismo ID; **nunca reenvía el POST automáticamente**. Los fills acumulados se aplican por diferencias, una sola vez. Una aceptación, ejecución parcial o consulta fallida no elimina la posición. El residuo menor al mínimo se conserva como `dust`, con cantidad y costo; no se convierte ficticiamente en USDT.

Órdenes ambiguas bloquean compras. Si siguen sin confirmarse, revisar el ID en Pionex y preservar la DB para conciliación. No borrar la DB para hacer desaparecer el bloqueo. Ventas manuales externas o saldos significativos sin atribuir pueden congelar nuevas entradas. La nueva versión conserva como referencia `trade_state.json` si existe, pero no adivina precios/costos ni vende posiciones antiguas.

Cambiar estrategia, costos o presupuesto cambia la huella de validación. Al detectar diferencia, una DB existente conserva las posiciones y permite sus salidas, pero bloquea nuevas compras. No cambiar de DATA_DIR mientras existan posiciones u órdenes desconocidas sin reconciliarlas primero.

## Estrategia y límites

- Velas de 1 hora cerradas, timestamps alineados y series continuas.
- Precio > EMA50 > EMA200; EMA50 con pendiente positiva.
- Cierre rompe el máximo de las 20 velas anteriores; volumen al menos 1,2 veces su media.
- BTC también en régimen alcista; freno por caída horaria/intrahoraria del 1,5%.
- Stop: máximo entre 1% y 2 ATR, sin admitir distancia mayor al 6%; target: 3 ATR.
- Rechazo si la relación target/riesgo neta estimada es menor a 1,2 o no cubre costos.
- Compra rechazada ante spread >0,10%, book insuficiente, impacto estimado >slippage o precio que se alejó más de 0,5 ATR.
- Riesgo presupuestado por trade ≤0,5% y conjunto ≤1%; hasta dos posiciones y 30% de exposición.
- Pérdida diaria del 2% pausa entradas ese día UTC; drawdown del 8% congela entradas hasta revisión.
- Salida por stop, target o 72 horas de tenencia. No martingala, promediar pérdidas ni aumentar leverage.

Los límites son políticas de ingeniería conservadoras, **no parámetros óptimos obtenidos de investigación**. Gaps, fees distintos, falta de liquidez y fallas operativas pueden exceder el riesgo previsto. Los indicadores y filtros no estiman una probabilidad de ganar.

## Validación reproducible

```bash
python -m unittest discover -s tests -v
python download_history.py --directory history --days 365
python backtest.py history --output validation.json
python forward_report.py data/paper.sqlite3 --output forward_validation.json
```

La descarga usa solamente datos públicos y guarda hashes de CSV y reglas obtenidas. Si Pionex rechaza el acceso, no genera un manifest de éxito. El backtest usa los mismos filtros/riesgo, entradas en la apertura siguiente, cartera conjunta, fees, slippage, spread, gaps, mínimos, costo del cierre final y criterio stop-first cuando OHLC no revela el orden de las barreras. Compara una cesta buy-and-hold al 100% y al 30% de capital; no afirma igualar su volatilidad.

La política exige: fuente Pionex, misma huella, al menos 180 días de historia, 100 trades fuera de la primera porción del 30%, profit factor ≥1,2, límite inferior de expectancy por block-bootstrap positivo, drawdown ≤10%, escenario de costos dobles positivo y dos de tres segmentos positivos con ≥20 trades cada uno. Evidencia histórica vence a los 30 días. No hay optimizador: una hipótesis registrada; explorar variantes invalida la premisa de un solo ensayo y exige investigación adicional sobre multiplicidad.

Automático real requiere además journal paper de 90 días observados, ≥50 trades, expectancy inferior positiva, profit factor ≥1,2 y drawdown ≤8%. Su informe vence a los siete días. **Pasar esas reglas no constituye prueba de rentabilidad futura.** Los informes son artefactos de investigación local, no firmas independientes ni barreras contra un operador que los altere intencionalmente.

El modelo histórico no reproduce books históricos, latencia humana, outages, delistings ni todas las variaciones de reglas. Solo usa reglas actuales; la watchlist tiene sesgo de selección. No se ha calculado PBO, DSR ni una prueba multi-año/multi-régimen. Estos límites deben revisarse antes de arriesgar capital.

## Despliegue

Comando de inicio: **`python main.py`**, igual al `Procfile`. No usar `gunicorn main:app`: este programa tiene su propio servidor de salud y un worker explícito. `requirements.txt` es deliberadamente vacío de dependencias.

`/health` devuelve 503 durante arranque, falta de datos o worker caído; 200 indica worker y datos recientes, no estrategia aprobada ni rentabilidad. Para LIVE: `DATA_DIR` absoluto montado en disco persistente, `PERSISTENT_STORAGE=true` y `ACK_LOCAL_STOPS=true`; esas variables son una declaración del operador, no una comprobación física del hosting. Si faltan, el proceso rechaza arrancar en real.

Conservar una copia de DB, archivos WAL/SHM según el procedimiento de backup de SQLite, configuración y estado anterior antes de migrar. No se requiere ni se recomienda activar trading real para ejecutar tests, descargar velas o leer la investigación.
