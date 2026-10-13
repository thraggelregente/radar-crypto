# Comprobaciones de la entrega

Base auditada: `3ea12c67a1f2c5dbf6cfe49e49730905ae8ffdd3`. Ejecución local de pruebas: Python 3.12.14; CI configurado para Python 3.11. No se instalaron paquetes externos ni se operó una cuenta real.

## Resultado técnico

**47 pruebas offline aprobadas** mediante:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Incluyen un ciclo completo de entrada automática paper y salida automática con comisiones, sin consultar saldos ni enviar órdenes privadas. Los datos de ese ciclo y de los escenarios del simulador son sintéticos y no indican rentabilidad de mercado.

| Riesgo reproducido en el código anterior | Comprobación de la nueva versión |
|---|---|
| Otro mensaje de Telegram borra una compra | Compra y `/status` en un lote preservan posición y offset. |
| Orden aceptada se da por vendida | La posición permanece hasta aplicar el fill. |
| Venta parcial elimina todo | Se conserva cantidad y costo restantes. |
| Timeout lleva a pérdida de seguimiento | Intención anterior al POST; recuperación por ID sin reenvío. |
| Fill repetido duplica el movimiento | Aplicación incremental e idempotente de cantidades/importes. |
| Botón viejo afecta nueva posición | ID de posición anterior no puede vender la posición actual. |
| Señales anteriores sobreviven a falla de datos | Refresh fallido elimina snapshot utilizable. |
| Precio/cierre inventado | Cotización sin timestamp y velas con huecos/vencidas son rechazadas. |
| Simulación desconoce comisiones | Compra paper utiliza ask, slippage y fee; venta utiliza bid y fee. |
| Redondeo pierde o borra inventario | Dust conserva cantidad y costo en el balance. |
| Mínimo empuja a aumentar exposición | Importe incompatible se omite. |
| Sobreventa basta para entrar | Tendencia bajista y ausencia de BTC no cumplen la hipótesis. |
| Orden antes de persistir | El test inspecciona PREPARED dentro del mock de POST. |
| Archivo corrupto parece cuenta vacía | DB corrupta falla; transacción fallida hace rollback. |
| Cambio de estrategia pierde posiciones | Conserva posiciones y bloquea compras nuevas. |
| Indicadores siempre bloquean o test vacío | Setup alcanza filtros predeterminados; prueba de siguiente apertura exige trades. |
| Ambigüedad de OHLC favorece al bot | Cuando toca ambos niveles se contabiliza stop primero. |
| Capital real sin validación | Falta de informes y fuentes sintéticas no habilitan real. |
| Calendario inactivo parece prueba forward | Informe requiere tiempo observado; no basta dejar pasar días. |

También se verifican liquidez/impacto antes de comprar, precisión de importe del par y uso de `minTradeDumping` para ventas a mercado. El health server responde 503 si el worker no tiene datos válidos.

## Limpieza y arquitectura

Eliminados `test_pionex.py`, `test_telegram.py` y `selftest.py`: eran scripts de conexión con efectos de red/mensajes, no una suite de regresión. `Procfile.txt` se reemplazó por `Procfile`. La nueva suite es un archivo `tests/test_core.py`, ejecutable sin claves y sin operar. Se agregó CI.

Separados configuración, HTTP, datos, estrategia, riesgo, SQLite, ejecución y Telegram. Se agregaron descargador público, backtest y reporte forward. La documentación indica presupuesto, migración, hosting, fuente de costos y límites de investigación. El runtime utiliza la biblioteca estándar de Python.

## Resultado financiero

**Rentabilidad real: no evaluada / no aprobada.** La consulta pública de velas/símbolos devolvió HTTP 403 desde este entorno; el intento posterior con el descargador tampoco pudo confirmar la conexión. La ausencia de datos no se reemplazó por velas inventadas. No se presenta un Sharpe, CAGR, tasa de acierto o retorno histórico de mercado.

La estrategia es una hipótesis fijada para su futura medición. Los gates de validación histórica y forward no convierten un resultado positivo en garantía; sus supuestos y límites están detallados en [RESEARCH.md](RESEARCH.md). Un cambio de parámetros/código invalida su huella.

## Lo que no se probó

- Firma autenticada contra una cuenta Pionex real, permisos o ejecución de órdenes.
- Disponibilidad efectiva de pares/mínimos/fee tier del usuario.
- Recuperación en el hosting del usuario, disco persistente, backups o múltiples hosts.
- Latencia real de Telegram y fills, outages o cotizaciones históricas de book.
- Ventaja estadística, rentabilidad multi-régimen, PBO o DSR.

No se enviaron mensajes de Telegram, órdenes reales ni se modificó configuración de despliegue para estas comprobaciones. Los cambios se entregan en una rama de GitHub para separar implementación y despliegue.
