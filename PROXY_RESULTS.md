# Evaluación exploratoria con históricos oficiales de Binance

## Decisión

**La primera hipótesis no se aprueba para dinero real.** Los casos con operaciones presentan retorno negativo, profit factor inferior a 1 y expectativa estimada negativa. Cambiar capital no convirtió esta hipótesis en rentable. No se ajustaron indicadores después de observar estos resultados para conseguir una curva positiva.

## Datos y procedimiento

- Fuente: archivos mensuales oficiales de Binance Spot, descargados desde `data.binance.vision`.
- 216 ZIP verificados con sus CHECKSUM SHA256 oficiales; hashes adicionales de cada CSV normalizado.
- SOL/USDT, ETH/USDT, NEAR/USDT, SUI/USDT, AVAX/USDT y BTC/USDT.
- 26.304 velas de 1h por par: desde 2023-10-01 00:00 UTC hasta 2026-10-01 00:00 UTC, fin exclusivo.
- Primer 30% reservado. Evaluación cronológica desde 2024-08-24 19:00 UTC: aproximadamente 767,2 días.
- Hipótesis y controles registrados antes de esta descarga: tendencia, ruptura, BTC, ATR, volumen, riesgo y circuit breakers.
- Costos supuestos por lado: fee 0,05%, slippage 0,05%; spread total presupuesto 0,10%. La fee estándar de Pionex se comprobó públicamente; no se verificó el tier personal ni el book histórico.
- Las reglas de mínimos/precisión son supuestos explícitos de sensibilidad. **No son mínimos obtenidos de Pionex ni una orden de aumentar el presupuesto.**

Se ejecutó `python research_proxy.py history_binance --output reports`. Los informes compactos conservan métricas, límites y hashes; los logs detallados se reproducen con el mismo script y los CSV verificados. Los tres escenarios se declararon como sensitividades de presupuesto/mínimo, sin elegir el ganador para habilitar LIVE.

## Resultado fuera de la primera porción de historia

| Escenario declarado | Capital | Mínimo supuesto | Operaciones | Retorno neto | Profit factor | Drawdown estimado |
|---|---:|---:|---:|---:|---:|---:|
| Saldo informado, mínimo conservador | 14,22 USDT | 10 USDT | 0 | 0,00% | No definido | 0,00% |
| Contrafactual para aislar el mínimo | 14,22 USDT | 1 USDT | 103 | −5,48% | 0,815 | 8,01% |
| Capital hipotético de investigación | 100 USDT | 10 USDT | 66 | −6,38% | 0,677 | 8,27% |

El primer resultado es **inactividad**, no una estrategia ganadora ni un backtest con cero riesgo de mercado. Los campos estadísticos sin muestra de los JSON usan sentinelas del programa y no deben interpretarse como estimaciones de tasa de acierto o confianza. Para este escenario no hay expectativa, profit factor ni win rate estimables.

El tercer escenario utiliza cap por operación de 20 USDT; los otros conservan 5 USDT. Es un presupuesto hipotético para estudiar restricciones, no una recomendación de depósito.

La política de drawdown congela nuevas entradas. En los casos con pérdidas alcanza ese bloqueo temprano y deja de incorporar nuevos trades durante el resto de la evaluación continua. Por eso no se anualizan los resultados ni se interpreta que estuvieron activos operando durante los 767 días completos. Las posiciones existentes conservan sus salidas; los tres segmentos adicionales reinician el presupuesto para evaluar períodos separados.

Una cesta buy-and-hold de los cinco activos vigilados con 30% de capital invertido y 70% en cash perdió aproximadamente **4,50%** bajo el mismo presupuesto de costos. El caso contrafactual de 14,22 USDT perdió más. Esa comparación iguala una asignación de capital, no volatilidad, riesgo, trayectoria de exposición ni calidad de ejecución.

## Diagnóstico de la primera hipótesis

En el caso contrafactual de mínimo 1 USDT hubo 37 salidas por target, 65 por stop y una por tiempo. La tasa de operaciones positivas fue 35,92%; la ganancia media positiva fue aproximadamente 5,40% por importe y la pérdida media negativa −3,57%. La media de retornos por operación fue −0,35%, con límite inferior por moving-block-bootstrap de −1,25%. El profit factor menor a 1 confirma que los beneficios realizados no compensaron las pérdidas realizadas.

Esto es consistente con demasiadas rupturas que no sostuvieron el movimiento bajo esta regla. **No demuestra causalmente que cambiar un indicador concreto vaya a resolverlo.** Volumen, EMA y ATR filtran precios pasados; no crean una probabilidad de éxito suficiente por sí solos.

Solo uno de tres segmentos independientes fue positivo, tanto en el contrafactual de 14,22 como en el de 100 USDT. Los resultados por segmento están en los JSON. No superan el requisito de dos segmentos favorables y la evidencia no es estable entre períodos.

En el contrafactual pequeño, duplicar costos dio un retorno cercano a +0,06%, con 50 trades y profit factor 1,004. **No significa que encarecer el trading mejore el sistema:** al duplicar costos también se endurecen los filtros y cambia el conjunto de operaciones. Su límite inferior de expectativa sigue siendo negativo y no aprueba robustez. En los casos con mínimo 10 USDT el stress no pudo operar; tampoco se considera aprobado.

## Viabilidad de 14,22 USDT

Exposición máxima actual: `14,22 × 30% = 4,266 USDT`. Para un stop supuesto del 2% y costo del 0,30%, el presupuesto de riesgo del 0,5% permite aproximadamente `14,22 × 0,005 / 0,023 = 3,09 USDT` por trade.

Si el mínimo real es 5 o 10 USDT, no hay operación compatible con el límite de exposición. Si fuese 1 USDT, puede haber operaciones compatibles, pero la hipótesis igualmente no superó el estudio. No se suben riesgo, leverage o capital para ocultar esa incompatibilidad.

Pionex publica fee spot estándar de 0,05% maker y taker. Sobre una operación hipotética de 10 USDT, una fee de entrada es 0,005 USDT; la salida tiene su propia fee sobre el importe realmente vendido. Las comisiones porcentuales no son por sí solas una razón para afirmar que una cuenta pequeña pierde siempre. Los mínimos, riesgo y beneficio económico absoluto son aquí restricciones relevantes.

## Limitaciones y siguiente dato necesario

Esta es una prueba con precios reales de Binance, no con fills ni books de Pionex. No se infiere alpha transferible entre venues. Hay sesgo de watchlist/supervivencia, reglas históricas aproximadas, estimaciones intrabar conservadoras, capital inmóvil después del circuit breaker y un protocolo fijo que no calcula PBO/DSR ni constituye una validación ciega perfecta.

**Ningún resultado Binance puede aprobar LIVE en Pionex**, aunque fuese positivo. Para medir la compatibilidad real de tu cuenta necesitamos mínimos y cotizaciones públicos actuales de Pionex: `verificar_pionex.cmd` los reúne sin claves, acceso al balance privado ni órdenes. Las instrucciones para Windows están en [WINDOWS.md](WINDOWS.md).

Una hipótesis nueva debe registrarse como ensayo adicional, preservar esta evaluación fallida y reservar datos no usados/seguimiento forward. No se presentará como rentable por haber sido ajustada a este mismo historial.

## Evidencia reproducible

- [Resumen de escenarios](reports/binance_proxy_summary.json)
- [14,22 USDT, mínimo 10](reports/reported_14_22_min10.json)
- [14,22 USDT, mínimo contrafactual 1](reports/counterfactual_14_22_min1.json)
- [Capital hipotético 100](reports/hypothetical_100_min10.json)
- Datos oficiales: https://github.com/binance/binance-public-data
- Fees Pionex: https://www.pionex.com/en/fees
