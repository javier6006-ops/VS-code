# Base única para Power BI — Rendiciones por CECO / Rendidor / Concepto

Un solo comando genera una tabla plana (una fila por comprobante) con todo el detalle y, al final, las columnas
para trazar lo facturado y lo no facturado.

```bash
python generar_base_powerbi.py --excel Presupuestos_Rendiciones_actualizado.xlsx
```

Genera en `salida_powerbi/`:

| Archivo | Para qué |
|---|---|
| `Base_Rendiciones_PowerBI.xlsx` | **Archivo maestro.** Hoja `Base` (tabla `tblBase`), `Diccionario` y `Conciliacion`. Aquí se anota la facturación. |
| `Base_Rendiciones_PowerBI.parquet` | Copia de solo lectura, tipada y más rápida. |
| `Base_Rendiciones_PowerBI.csv` | Copia de solo lectura (UTF-8). Al importarla en Power BI usa configuración regional *Inglés (EE. UU.)*, porque `Factor_Prorrateo` usa punto decimal. |
| `historial/` | Respaldo del maestro anterior en cada ejecución. |
| `facturacion_huerfanos.csv` | Solo si algún registro con seguimiento ya no existe en la base nueva. |

Opciones útiles: `--reprocesar` (vuelve a leer los adjuntos desde la caché), `--sin-cabify`, `--lecturas`, `--indice`, `--pkl`, `--salida`.

## Cómo está armada la base

- **Una fila = un comprobante o ítem leído.** Cada rendición suma exactamente lo declarado en Talana (`Monto_CLP`).
- `Tipo_Registro` distingue **"Rendición"** de **"Cabify corporativo"**. Los viajes Cabify (extracto, CECOs operacionales) traen su
  monto en `Monto_Cabify_CLP` y `Monto_CLP = 0`, así que **no se mezclan con el gasto rendido** al sumar.
- Para no duplicar al sumar: el monto declarado en Talana está en `Declarado_Rendicion_CLP` **solo en la primera fila** de cada rendición.
- Calidad del detalle: `Nivel_Detalle` (Concepto identificado · OTROS con subtipo · Sin detalle con concepto probable · Sin detalle sin pista),
  `Subtipo_OTROS`, `Concepto_Probable` y `Concepto_Estimado`. **Lo probable es una estimación, no un dato leído**: filtra por `Nivel_Detalle`
  para separarlo.
- Todas las columnas están descritas en la hoja `Diccionario`.

## Seguimiento de facturación (columnas finales, en naranjo)

`Estado_Facturacion` · `Cliente_Facturado` · `N_Factura` · `Fecha_Factura` · `Monto_Facturado_CLP` · `Obs_Facturacion`

1. Abre `Base_Rendiciones_PowerBI.xlsx` y edita esas columnas en la hoja `Base` (el estado tiene lista desplegable: *Sin clasificar,
   Facturado, Pendiente de facturar, No facturable, Facturación parcial*). Todo parte en *Sin clasificar*.
2. `Facturable_Segun_Carpeta` es solo una **sugerencia**: hoy viene del nombre de las carpetas de Trade (FACTURABLE / NO FACTURABLE).
3. **Al regenerar la base se conserva lo anotado**, enlazado por `Clave_Item` (identificador estable). Si cambia la lectura de un comprobante
   y su clave desaparece, el seguimiento queda en `facturacion_huerfanos.csv`; no se pierde en silencio.
4. En la hoja `Conciliacion` hay un resumen de facturado / pendiente / no facturable que se recalcula al abrir el archivo.

## Conectar en Power BI

1. *Obtener datos → Libro de Excel* → `Base_Rendiciones_PowerBI.xlsx` → tabla **tblBase** (o *Parquet* para refrescos más rápidos).
2. En `Link_Adjunto` define *Categoría de datos → URL web*.
3. Crea una tabla de calendario y relaciónala con `Fecha_Reporte` (o usa `Periodo`, que ordena bien como texto).
4. Para refrescar: ejecuta el generador de nuevo y actualiza el informe. La base es una sola tabla, no requiere relaciones.

## Medidas DAX sugeridas

```DAX
Gasto Rendido      = CALCULATE ( SUM ( tblBase[Monto_CLP] ), tblBase[Tipo_Registro] = "Rendición" )
Declarado Talana   = SUM ( tblBase[Declarado_Rendicion_CLP] )
N Rendiciones      = CALCULATE ( DISTINCTCOUNT ( tblBase[ID_Rendicion] ), tblBase[Tipo_Registro] = "Rendición" )
Gasto Cabify       = SUM ( tblBase[Monto_Cabify_CLP] )

Gasto Sin Detalle  = CALCULATE ( [Gasto Rendido], LEFT ( tblBase[Nivel_Detalle], 11 ) = "Sin detalle " )
% Sin Detalle      = DIVIDE ( [Gasto Sin Detalle], [Gasto Rendido] )
Gasto Estimado     = CALCULATE ( [Gasto Rendido], tblBase[Nivel_Detalle] <> "Concepto identificado" )

Facturado          = CALCULATE ( [Gasto Rendido], tblBase[Estado_Facturacion] = "Facturado" )
Pendiente Facturar = CALCULATE ( [Gasto Rendido], tblBase[Estado_Facturacion] = "Pendiente de facturar" )
No Facturable      = CALCULATE ( [Gasto Rendido], tblBase[Estado_Facturacion] = "No facturable" )
Sin Clasificar     = CALCULATE ( [Gasto Rendido], tblBase[Estado_Facturacion] = "Sin clasificar" )
% Facturado        = DIVIDE ( [Facturado], [Facturado] + [Pendiente Facturar] )
Monto Facturado    = SUM ( tblBase[Monto_Facturado_CLP] )
```

## Páginas sugeridas del informe

1. **Resumen**: tarjetas (Gasto Rendido, % Sin Detalle, Facturado, Pendiente Facturar), barras por `Familia_Concepto`, línea por `Periodo`.
2. **CECO → Rendidor**: matriz con `CECO` y `Rendidor` en filas, `Periodo` en columnas y `Concepto_Detallado` como detalle (drill-down).
3. **Calidad del detalle**: `Nivel_Detalle` y `Subtipo_OTROS` por CECO; tabla de las rendiciones con más monto sin detalle.
4. **Cabify vs rendido**: `Gasto Cabify` junto a `Gasto Rendido` filtrado a `APPS_TRANSPORTE` y `COMBUSTIBLE`, por `Rendidor` y `Periodo`.
5. **Facturación**: `Estado_Facturacion` por CECO y cliente, y tabla de pendientes con `Link_Adjunto`.
6. **Auditoría**: filas con `Alerta_Duplicado = "SI"`, `Dias_Doc_a_Rendicion` atípicos y `Factor_Prorrateo < 1`.

## Datos personales

La base contiene nombres, RUT y links a comprobantes. Está excluida de git (`.gitignore`); compártela solo por canales internos.
