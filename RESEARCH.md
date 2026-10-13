# Investigación y decisión de diseño

Fecha de consulta: 13 de octubre de 2026 (UTC). Proyecto auditado: `thraggelregente/radar-crypto`, base `3ea12c67a1f2c5dbf6cfe49e49730905ae8ffdd3`.

## Resultado de la investigación

No hay una ley económica que vuelva perdedor a todo bot por estar automatizado. Un algoritmo ejecuta reglas: el resultado depende de su ventaja neta, riesgos, costos y aplicación. La literatura documenta efectos de momentum, pero no demuestra que un robot minorista particular, un cruce horario de medias o una combinación de RSI sea rentable. Automatizar un sistema sin expectativa positiva puede hacer que pierda de forma más consistente.

**Decisión para este proyecto:** paper automático ahora; compras reales semiautomáticas durante el eventual piloto, con salidas de riesgo automáticas; compras reales totalmente automáticas solo después de evidencia histórica y forward vigente. Hoy no existe esa evidencia y las entradas reales quedan bloqueadas. No se cambia una incertidumbre estadística por una promesa comercial.

## Qué se encontró en fuentes primarias

| Fuente | Hallazgo pertinente | Alcance y limitación |
|---|---|---|
| Liu y Tsyvinski, *Risks and Returns of Cryptocurrency*, RFS 2021 [1] | Encuentran predictibilidad vinculada a momentum y atención del inversor. | Patrón histórico, no validación de este código ni de EMA50/200 en 1h. |
| Moskowitz, Ooi y Pedersen, *Time Series Momentum*, JFE 2012 [2] | Evidencia de tendencia en varios mercados de futuros. | Horizontes y diversificación muy distintos del radar; no extrapolar rendimientos a altcoins. |
| Liu, Tsyvinski y Wu, *Common Risk Factors in Cryptocurrency*, JF 2022 [3] | Riesgos comunes explican una parte importante de estrategias cripto. | Cinco altcoins no equivalen automáticamente a cinco riesgos independientes. |
| Bailey et al., *The Probability of Backtest Overfitting*, 2015 [4] | Buscar muchas reglas sobre la misma muestra facilita falsos positivos. | Una separación simple train/test puede ser insuficiente, especialmente si se reutiliza. |
| Bailey y López de Prado, *The Deflated Sharpe Ratio*, 2014 [5] | Selección y retornos no normales pueden inflar el Sharpe. | No se calculó DSR aquí; no se presenta un Sharpe sin corregir como certificado. |
| Cornelli et al., BIS Bulletin 69, 2023 [6] | La mayoría de usuarios minoristas del conjunto analizado perdió en bitcoin. | Estudia adopción/tenencias minoristas; no identifica causalmente resultados de bots. |
| Pionex: fees, Trade API, Market/Common y Bot API [7–12] | Permiten comprobar costos por fill, cantidades, precisión y capacidades distintas de bots nativos. | Documentación de producto, no evidencia independiente de beneficio. |

La decisión es una **inferencia de diseño propia**: ensayar una hipótesis sencilla, rechazar señales con costos o datos inadecuados, limitar exposición conjunta y exigir evidencia antes de automatizar capital. Los papers no recomiendan nuestros valores concretos ni garantizan que tengan ventaja.

## Por qué fallan muchos sistemas

### 1. Confundir indicadores con una ventaja

RSI mide una propiedad del precio pasado. Estar sobrevendido no implica recuperación próxima: un activo puede seguir cayendo. El código anterior asignaba 50 puntos por RSI≤32, suficiente para comprar incluso en tendencia bajista y sin volumen. El target/stop fijo no aportaba una probabilidad de éxito. Sumar señales derivadas del mismo precio puede contar varias veces la misma información.

Se eliminó esa entrada autónoma por sobreventa. La hipótesis nueva pide una ruptura cerrada dentro de un régimen alcista del par y BTC. Puede perder con falsas rupturas y mercados laterales; eso se mide, no se evita por definición.

### 2. Ignorar el costo de rotar

Pionex publica una tarifa spot estándar de 0,05% **por ejecución**; compra y venta tienen fees separados [7]. La tasa efectiva del usuario/par puede cambiar. Además existen spread, impacto, slippage, mínimos, capital inactivo, infraestructura y eventualmente impuestos. El costo exacto de las dos fees depende de importes ejecutados diferentes.

Una frecuencia alta puede convertir una ventaja bruta débil en expectativa neta negativa. El paso de 15m a 1h busca reducir decisiones ruidosas y rotación potencial; no demuestra que el horizonte de 1h sea óptimo. La estrategia puede pasar mucho tiempo en cash. Ese costo de oportunidad se compara con buy-and-hold; estar en USDT tampoco elimina riesgo de stablecoin o custodia.

### 3. Optimizar el pasado y reconocer ganancias de papel

Probar cien variantes y seleccionar la mejor sobre la misma muestra puede identificar ruido. Usar el cierre que originó la señal como precio de entrada anticipa información. Omitir fees, marcar fills sin confirmar y cerrar simulaciones a cero distorsiona la medición. Contar solo ganancias realizadas de un grid puede ocultar pérdidas del inventario.

La nueva implementación distingue simulación, intención enviada y cantidad realmente ejecutada. La evaluación histórica entra en la vela siguiente, conserva costos y residuos, y trata de forma adversa una vela que tocó simultáneamente stop y target. Se registró una hipótesis sin optimizador. Si se prueban variantes sobre mercado real, deben registrarse como ensayos adicionales y revisarse el control de selección; no debe mantenerse artificialmente `trials=1`.

### 4. Cambios de régimen y riesgo conjunto

Una reversión al promedio puede fracasar durante una tendencia prolongada. Una ruptura puede sufrir pequeñas pérdidas repetidas en rango. Los pares pueden caer simultáneamente por su factor cripto común [3]. No hace falta que cada indicador esté mal para que una cartera sobreexpuesta pierda.

Se impusieron límites sobre exposición y riesgo total, no solo un número de posiciones. No se estima una matriz dinámica de correlaciones con pocas muestras ni se vende esa estimación como diversificación garantizada. Hay freno BTC y filtros de tendencia; las entradas bajistas se omiten, sin intentar replicar shorts usando tokens apalancados.

### 5. Martingala, acumulación y colas

Duplicar tras pérdidas puede aumentar la proporción de ciclos ganadores al costo de una quiebra ocasional. Para apuestas `a, 2a, 4a, ...`, después de `n` intentos se necesita `a(2^n−1)` de capital. Con 5 USDT iniciales, diez intentos requieren 5.115 USDT. Una gran tasa de acierto no evita ese crecimiento de exposición.

El radar no duplica, promedia pérdidas, añade leverage ni sube `ORDER_USDT` para superar mínimos. Gaps y suspensión del exchange pueden saltar el stop local: los límites de pérdidas son desencadenantes y presupuestos, no garantías de pérdida máxima.

### 6. Fallos operativos

Un error de estado, un timeout o una venta parcial puede dejar capital real sin seguimiento. Es una pérdida que no aparece en una ecuación idealizada de RSI. El bug anterior de Telegram podía borrar una compra; una venta aceptada se eliminaba sin fill completo; datos antiguos podían emitir señales nuevas.

Las correcciones incluyen transacciones, IDs durables, conciliación por ID, aplicación incremental de fills, separación paper/live, conservación de dust y salud del worker. La incertidumbre bloquea compras; no dispara reintentos ciegos de órdenes. Los stops locales aún dependen de disponibilidad operacional.

## Análisis financiero y matemático

### Expectativa y punto de equilibrio

Con ganancia bruta media `G`, pérdida bruta media `L`, probabilidad de ganar `p` y costo medio de ida/vuelta `c`, una aproximación por operación es:

`E[r] = pG − (1−p)L − c`

`p_equilibrio = (L+c)/(G+L)`

El bot anterior tenía target 2,5% y stop 2%. **Ejemplo hipotético**, suponiendo que todos los winners/losers ejecutan exactamente esos valores:

| Costo total supuesto | Acierto mínimo para equilibrio |
|---|---:|
| 0% | 44,44% |
| 0,10% | 46,67% |
| 0,30% | 51,11% |
| 0,50% | 55,56% |

Con `p=50%` y `c=0,30%`, la expectativa sería −0,05% por importe operado. Con `p=40%`, sería −0,50%. Ninguno de esos p es una estimación observada: muestran por qué elegir TP/SL no alcanza. El costo de 0,30% es un escenario conservador del diseño: dos fees de 0,05%, slippage de 0,05% por lado y 0,10% de spread total. No es una medición de nuestro book histórico.

Para un ciclo spot ideal, fees cobradas en base al comprar y en quote al vender:

`R_neto = (P_salida/P_entrada) × (1−f_compra) × (1−f_venta) − 1`

Si las fees se cobran en otras monedas, hay que reconstruir cashflows reales. El cliente rechaza una comisión no conciliable en una tercera moneda antes de alterar el journal. En vivo registra fills, no usa la fórmula aproximada como sustituto de la contabilidad.

### Volatilidad, tamaño y exposición

Para capital asignado `C`, presupuesto de riesgo relativo `r`, stop relativo `d` y costo estimado `c`, el importe máximo orientativo es:

`N ≤ Cr/(d+c)`

Con `C=100`, `r=0,005`, `d=0,02`, `c=0,003`: `N≤21,74 USDT`, antes del límite `ORDER_USDT`, cash, exposición y riesgo de otras posiciones. Con `ORDER_USDT=5`, sigue limitado a 5; no se amplía a 21,74. Si el mínimo real del par supera 5, la decisión correcta es no operar con esa configuración.

El stop utiliza `max(1%, 2ATR/precio)`, rechazando distancias superiores al 6%; el target es `3ATR/precio`. La relación filtrada es `(target−c)/(stop+c)`, al menos 1,2. Esa relación no estima `p`: un R/R favorable puede acompañar una probabilidad de ganar tan baja que la expectativa sea negativa. Los parámetros son políticas experimentales, no valores óptimos inferidos de literatura.

La suma de riesgos presupuestados no supera el 1% del capital del bot; exposición máxima 30% y dos posiciones. La regla usa la hipótesis adversa de pérdidas simultáneas, sin atribuir diversificación por nombres distintos. No utiliza Kelly: su ventaja aparente desaparece si `p` y los retornos están mal estimados.

### Crecimiento y drawdown

Maximizar retorno aritmético no es maximizar supervivencia. Aproximadamente, para retornos pequeños, `E[log(1+r)] ≈ E[r]−Var(r)/2`. La variabilidad y colas reducen crecimiento compuesto. Una caída del 50% requiere un 100% de subida para recuperar capital; una del 20% requiere 25%.

Se observa equity conjunto, incluidas posiciones y dust valorados a bid, con costo estimado de liquidación. Pérdida diaria del 2% bloquea compras del día UTC. Drawdown desde máximo del 8% activa un bloqueo persistente de compras. Las salidas de riesgo siguen funcionando. No se oculta el drawdown borrando datos, cambiando de DB o reiniciando la estrategia.

Estos controles pueden limitar pérdidas ordinarias, pero no rescatan una estrategia sin ventaja y pueden quedarse cortos frente a gaps/outages. También pueden bloquear un sistema que después se recuperaría. Su utilidad y costo deben medirse.

### Viabilidad económica con capital pequeño

Un 2% mensual **hipotético** sobre 100 USDT son 2 USDT brutos, sin afirmar que sea alcanzable. Un hosting pago puede consumir ese importe. Por eso se separa rendimiento de trading del rendimiento económico total:

`beneficio_económico = PnL_neto_de_trading − infraestructura − retiros/conversiones − impuestos aplicables`

No se modelaron impuestos argentinos ni costos de cuenta del usuario porque faltan datos y exceden una inferencia de trading. No se promete una renta relevante con operaciones de 5 USDT. Usar infraestructura gratuita que se suspenda tampoco sirve como garantía de protección de posiciones reales.

## Qué permite Pionex y qué se eligió

| Alternativa | Mecanismo y ventaja práctica | Riesgo/limitación | Decisión |
|---|---|---|---|
| Spot por Trade API [8–10] | Compra MARKET por `amount`, venta por `size`; consulta de fills y clientOrderId. | Protección local en esta integración; límites y fees por operación. | Implementado, LONG y sin leverage. |
| Spot Grid nativo por Bot API [11] | Lifecycle de grid con stop/take profit configurables del producto. | Un límite inferior del grid no es un stop [12]; inventario puede caer y total PnL ser negativo. | Alternativa a investigar separadamente, no supuesto atajo a rentabilidad. |
| Futures/Futures Grid [13] | Permite direcciones y gestión de contratos. | Funding, margen, liquidación y posible restricción de acceso a API; no asumir permiso por tener documentación. | No incorporado: agrega riesgos y no demuestra ventaja. |
| Arbitraje de funding/basis | Busca diferencias entre spot y contratos, con hedge y cashflows. | Funding cambia, basis se mueve, dos legs, margen, ejecución y custodia. | No incorporado con capital/riesgo no especificados. |
| DCA/acumulación | Distribuye compras en el tiempo. | Puede acumular un activo que sigue cayendo; no prueba alpha. | No se usa como recuperación de pérdidas. |

El endpoint Spot de creación documenta órdenes LIMIT/MARKET, no un bracket genérico con stop de nuestro radar. La Bot API tiene controles específicos de grid que no se transfieren a órdenes spot normales. No se anuncia un stop nativo inexistente. Book, mínimos, `minTradeDumping` y `amountPrecision` se verifican; las reglas se refrescan. La tarifa de tu cuenta y el acceso efectivo deben comprobarse en Pionex.

Los grids muestran por qué importa el balance completo: un ejemplo propio con +30 USDT de ciclos cerrados y −90 de inventario arroja −60 antes de otros ajustes. Automatizar más ciclos no elimina la pérdida de exposición. Mantener cash es una decisión válida cuando no hay un setup compatible.

## Semiautomático o automático

| Criterio | Confirmación manual | Automático |
|---|---|---|
| Intervención ante señal extraña | Permite observar contexto no modelado. | Requiere filtros, límites y monitorización explícitos. |
| Latencia y disciplina | Depende de horarios, cansancio y selección subjetiva. | Sigue reglas y evita omisiones humanas, sin crear ventaja. |
| Reproducibilidad | Los trades seleccionados pueden diferir del backtest. | Facilita medir una hipótesis completa. |
| Fallos | Un clic no repara el journal ni una API caída. | Un fallo puede operar repetidamente si faltan controles. |

La confirmación humana no sirve como prueba de alpha y no debe ser el único stop. Por eso las salidas se automatizaron en ambas modalidades. Paper automático recoge todos los setups que cumplan reglas y permite observar costos/rechazos sin capital real. Un piloto real semiautomático, si supera validación, permite revisar calidad de ejecución y conciliación antes de ampliar autonomía.

Automático real no se habilita por voluntad del programa ni por un buen día. Se requiere mantener resultados forward, evidencia vigente, una sola instancia, estado persistente y disponibilidad adecuada. Aun si pasa los umbrales, la entrada a real es una decisión de presupuesto operativa separada; el programa no aumenta importe ni habilita `LIVE_TRADING` por sí mismo.

## Protocolo de prueba y resultado actual

1. Hipótesis y código quedan identificados por hash de configuración/implementación.
2. Descargar OHLC y reglas públicas de Pionex con manifest y hashes; validar continuidad y tiempo.
3. Reservar inicialmente 30% de historia; evaluar cronológicamente el 70% restante con hipótesis fija. No hay entrenamiento de parámetros ni se declara una validación ciega perfecta.
4. Simular cartera, siguiente apertura, comisiones por lado, slippage, spread, mínimos, riesgo agregado, gaps y cierre final. Conservar residuos y evitar anticipación de información.
5. Evaluar tres segmentos temporales adicionales y escenario de costos dobles. El requisito no elimina el riesgo de selección ni equivale a PBO/DSR.
6. Exigir 180 días de historia y 100 trades OOS como política mínima, PF≥1,2, límite inferior de expectativa por moving-block-bootstrap positivo, DD≤10%, stress positivo y al menos dos segmentos positivos con ≥20 trades.
7. Para compras reales automáticas: además paper 90 días observados/50 trades, PF≥1,2, bootstrap favorable y DD≤8%. Informes con vencimiento; un cambio de estrategia invalida evidencia.

**Resultado actual:** no aprobado para real. Se consultó la API pública de velas y símbolos, y se obtuvo HTTP 403 en este entorno. No se obtuvo un historial verificable ni las reglas de cuenta para una evaluación real. Las pruebas sintéticas prueban orden cronológico y controles, no alpha. No se usaron retornos inventados para rellenar esa ausencia.

Límites adicionales: reglas históricas aproximadas por reglas actuales; watchlist seleccionada y sesgo de supervivencia; OHLC no reproduce book/latencia/outages; el simulador permite una entrada por hora y modela stops intrabar con datos incompletos. La literatura sobre tendencia no identifica nuestros parámetros. Deben añadirse muestras/regímenes y validación independiente si se exploran múltiples hipótesis. Ni 100 trades ni bootstrap convierten un resultado favorable en certeza.

## Fuentes consultadas

1. Liu, Y.; Tsyvinski, A. (2021). *Risks and Returns of Cryptocurrency*. Review of Financial Studies 34(6), 2689–2727. https://doi.org/10.1093/rfs/hhaa113
2. Moskowitz, T.; Ooi, Y.; Pedersen, L. (2012). *Time Series Momentum*. Journal of Financial Economics 104(2), 228–250. https://doi.org/10.1016/j.jfineco.2011.11.003 — resumen del autor: https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum
3. Liu, Y.; Tsyvinski, A.; Wu, X. (2022). *Common Risk Factors in Cryptocurrency*. Journal of Finance 77(2), 1133–1177. https://doi.org/10.1111/jofi.13119
4. Bailey, D.; Borwein, J.; López de Prado, M.; Zhu, Q. (2015). *The Probability of Backtest Overfitting*. https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
5. Bailey, D.; López de Prado, M. (2014). *The Deflated Sharpe Ratio*. https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
6. Cornelli, G.; Doerr, S.; Frost, J.; Gambacorta, L. (2023). *Crypto shocks and retail losses*. BIS Bulletin 69. https://www.bis.org/publications/bulletin-69-crypto-shocks-and-retail-losses
7. Pionex, tarifa spot por ejecución, actualización indicada 25/08/2026. https://www.pionex.com/blog/pionex-fee-trx-grid-is-0-05-buy-0-05-sell-or-0-05-fee-buy-sell/ y https://www.pionex.com/en/fees
8. Pionex Trade API, órdenes y consulta por clientOrderId. https://www.pionex.com/docs/api-docs/trade-api/trade
9. Pionex Common, reglas de pares. https://www.pionex.com/docs/api-docs/trade-api/common
10. Pionex Market y autenticación/límites. https://www.pionex.com/docs/api-docs/trade-api/market — https://www.pionex.com/docs/api-docs/trade-api/general-info/authentication — https://www.pionex.com/docs/api-docs/trade-api/general-info/rate-limits
11. Pionex Bot API, Spot Grid. https://www.pionex.com/docs/api-docs/bot-api/spot-grid
12. Pionex, diferencia entre lower limit y stop. https://www.pionex.com/blog/what-happens-if-the-price-cross-the-lower-range-in-the-grid-trading-bot/
13. Pionex, spot versus futures grid y riesgos de funding/liquidación. https://www.pionex.com/blog/futures-grid/ — https://www.pionex.com/docs/api-docs/futures-api/market
