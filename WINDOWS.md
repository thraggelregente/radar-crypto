# Descargar el proyecto y reunir datos en Windows

El error `can't open file C:\Users\golbe\download_history.py` indica que CMD está en una carpeta que no contiene ese archivo. No es un error de la API ni un motivo para instalar más paquetes. Primero hay que descargar la **rama de trabajo**, que contiene los archivos nuevos; `main` todavía contiene la versión anterior.

Si ya obtuvimos los históricos alternativos, el único archivo que necesitamos de tu computadora es el snapshot público de Pionex: después de los pasos 1–4, hacer doble clic en **verificar_pionex.cmd** y compartir **pionex_public_snapshot.json**. Sirve para confirmar mínimos y cotizaciones. Si falla, copiar el error de la ventana. No contiene claves ni balance privado; usa el capital informado de 14,22 USDT.

## Opción sencilla

1. Abrir https://github.com/thraggelregente/radar-crypto/tree/codex/radar-hardening-20261013 con tu cuenta de GitHub.
2. Botón verde **Code → Download ZIP**.
3. En el Explorador de Windows, seleccionar el ZIP y **Extraer todo**.
4. Abrir la carpeta extraída que contiene `download_history.py`, `download_binance.py` y `collect_data.cmd`.
5. Hacer doble clic en **collect_data.cmd**. El archivo cambia a su propia carpeta, usa simulación y descarga únicamente datos públicos. No necesita claves ni envía mensajes/órdenes.
6. Si Pionex falla, intenta archivos oficiales de Binance, con SHA256 verificado. El resultado alternativo queda etiquetado como Binance y no puede aprobar operaciones reales en Pionex.
7. Al finalizar debe aparecer **datos_radar.zip** en esa carpeta. Compartir ese archivo, no la carpeta completa del proyecto ni `.env`.

La descarga de 36 meses puede tardar varios minutos. El archivo ZIP final contiene CSV y el manifest; deja fuera claves, configuración privada y archives de cache. Si falla, copiar el texto del error. No ejecutar `main.py` ni habilitar LIVE para reunir datos.

## Opción CMD

Abrir la carpeta del proyecto en el Explorador. Hacer clic en la barra de dirección, escribir `cmd` y Enter. La consola se abre en la carpeta correcta. Verificar:

```bat
dir download_history.py
```

Luego ejecutar UNA de estas opciones:

```bat
python download_history.py --directory history_pionex --days 365
```

```bat
python download_binance.py --directory history_binance --months 36
```

No se requiere tener cuenta en Binance ni instalar librerías. Python 3.11+; tu Python 3.14 no explica el error de archivo inexistente. Las pruebas del proyecto se ejecutan también en CI con Python 3.11.

## Capital y costos

El presupuesto predeterminado del proyecto se ajustó al saldo informado de **14,22 USDT**. La tarifa spot estándar publicada por Pionex es 0,05% por lado; en código `FEE_RATE=0.0005`. Una tarifa especial de cuenta debe comprobarse en tu plataforma.

Con la política de exposición máxima del 30%, el máximo conjunto es **4,266 USDT**. Con stop del 2% y costo presupuestado del 0,30%, el tamaño máximo por riesgo es **3,09 USDT**. Si el mínimo real del par fuese 5 o 10 USDT, no se puede operar bajo esas reglas. Es una incompatibilidad de presupuesto/mínimos, no una invitación a aumentar capital o riesgo.

Los históricos de Binance sirven para estudiar una hipótesis de precios y sensibilidad. Books, fees efectivas, mínimos y fills de Pionex deben verificarse aparte. Los mínimos incluidos en el manifest alternativo son supuestos explícitos de investigación, no datos reales de Pionex.

Fuentes: https://www.pionex.com/en/fees — https://github.com/binance/binance-public-data — https://www.pionex.com/docs/api-docs/trade-api/common
