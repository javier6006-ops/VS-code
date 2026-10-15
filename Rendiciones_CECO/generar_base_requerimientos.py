# -*- coding: utf-8 -*-
"""
BASE ÚNICA PARA POWER BI — Rendiciones desde el libro "Centro de Requerimientos" (todas las hojas de reembolso)
==============================================================================================================
    python generar_base_requerimientos.py --libro Centro_de_Requerimientos.xlsx --salida salida_req

- Toma todas las hojas cuyo nombre contiene "reembolso" (o las indicadas con --hojas) y las unifica.
- Lee TODOS los adjuntos (PDF, imágenes con OCR, planillas, comprimidos, correos) y abre el gasto por comprobante y concepto.
- No incluye Cabify. Conserva el estado de cada solicitud (Abierto / En progreso / Finalizado / Rechazado, etapa, pagada…).
- Al final de la tabla quedan las columnas de seguimiento de facturación (se conservan al regenerar).
Reutiliza el motor de lectura y las reglas de clasificación de rpa_rendiciones.py / pipeline_rendiciones.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import pickle
import re
import shutil
import sys
from pathlib import Path

os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import pandas as pd  # noqa: E402

import generar_base_powerbi as G  # noqa: E402
import ingesta_requerimientos as I  # noqa: E402
import lectura_requerimientos as L  # noqa: E402
import pipeline_rendiciones as P  # noqa: E402
import rpa_rendiciones as R  # noqa: E402

log = logging.getLogger("base_req")

# --------------------------------------------------------------------------- columnas
QUITAR = {"Usuario_Cabify", "Cabify_Pasajero_Match", "Cabify_Match_Fuente", "Ticket_Cabify", "Pasajero_Cabify",
          "Solicitante_Cabify", "Vertical_Cabify", "Estado_Viaje_Cabify", "Monto_Cabify_CLP", "Periodo_Origen", "Accion_Solicitada",
          "Tipo_Fondo", "N_Requerimiento_Asociado", "Tipo_Documento_Declarado"}
NUEVAS_SOLICITUD = [
    ("Libro_Origen", "Solicitud", "texto", "Libro del que se tomó el registro. Si el ID estaba en varios libros se usa el más reciente."),
    ("N_Libros_Donde_Aparece", "Solicitud", "entero", "En cuántos libros aparecía el ID (duplicados eliminados = valor − 1)."),
    ("Estado_En_Otros_Libros", "Solicitud", "texto", "Estado que tenía el ID en los libros más antiguos (ayuda a ver la evolución)."),
    ("Comparte_Adjunto_Con", "Calidad y auditoría", "texto", "Otros IDs con exactamente el mismo archivo adjunto (posible rendición duplicada)."),
    ("Hoja_Origen", "Solicitud", "texto", "Formulario/hoja de la que viene la solicitud (sin el número de hoja, que cambia entre libros)."),
    ("Canal", "Solicitud", "texto", "Canal/formulario de la solicitud según la hoja Resumen."),
    ("Etapa_En_Curso", "Solicitud", "texto", "Etapa del flujo en que está la solicitud (Inicial, Derivado a Etapa 2, Finalizado…)."),
    ("Estado_Solicitud", "Solicitud", "texto", "Estado final de la solicitud: Pagada / Aprobado para pago / Rechazada / vacío si sigue en curso."),
    ("Etapas_Canal", "Solicitud", "texto", "N° de etapas del canal."),
    ("Dias_Sin_Movimiento", "Solicitud", "entero", "Días que lleva sin movimiento la solicitud (hoja Resumen)."),
    ("Tiempo_Resolucion_Dias", "Solicitud", "entero", "Días que tardó en resolverse."),
    ("Accion_Solicitada", "Solicitud", "texto", "Acción pedida (solicitar reembolso, cerrar fondo…)."),
    ("Tipo_Fondo", "Solicitud", "texto", "Qué se sustenta o reembolsa (Fondo Fijo, Otros gastos…)."),
    ("N_Requerimiento_Asociado", "Solicitud", "texto", "Requerimiento al que se asocia (p. ej. el fondo que se rinde)."),
    ("Tipo_Documento_Declarado", "Solicitud", "texto", "Tipo de documento que dijo adjuntar el rendidor (boleta, factura, otros…)."),
    ("N_Proyecto", "Cliente / facturación", "texto", "N° de proyecto declarado en la solicitud (clave para refacturar)."),
    ("Linea_Negocio", "Cliente / facturación", "texto", "Línea de negocio declarada."),
    ("Empresa_Cliente", "Cliente / facturación", "texto", "Empresa/cliente declarado en el formulario."),
    ("Razon_Social", "Cliente / facturación", "texto", "Razón social que emite (hoja Resumen)."),
    ("Cod_CC_Cliente", "Cliente / facturación", "texto", "Código del centro de costo del cliente (hoja Resumen)."),
    ("Centro_Costo_Cliente", "Cliente / facturación", "texto", "Centro de costo del cliente (hoja Resumen). Distinto del CECO interno."),
    ("RUT_Asociado", "Cliente / facturación", "texto", "RUT del asociado al que corresponde el gasto (hojas Trade/Vivo)."),
    ("Nombre_Asociado", "Cliente / facturación", "texto", "Nombre del asociado al que corresponde el gasto."),
    ("Jefatura_RUT", "Organización", "texto", "RUT de la jefatura del solicitante."),
    ("Motivo_Etapa1", "Revisión", "texto", "Motivo de aprobación/rechazo de la primera revisión."),
    ("Motivo_Etapa2", "Revisión", "texto", "Motivo de aprobación/rechazo de la segunda revisión (incluye 'Aprobado - Refacturable')."),
    ("Detalle_Rechazo", "Revisión", "texto", "Texto libre del motivo de rechazo, si lo hay."),
    ("Refacturable_Segun_Aprobador", "Revisión", "texto", "SI si el aprobador marcó el gasto como refacturable; NO si lo marcó no refacturable; vacío si no se pronunció."),
    ("Facturable_Sugerido", "Revisión", "texto", "SUGERENCIA para tu validación: FACTURABLE / NO FACTURABLE / POR REVISAR (aprobador > carpeta > proyecto declarado)."),
    ("Fuente_Sugerencia", "Revisión", "texto", "De dónde sale la sugerencia de Facturable_Sugerido."),
    ("Link_Adjunto_2", "Adjunto", "texto", "Segundo link de adjunto de la solicitud (captura de pantalla / archivo adicional), si existe."),
    ("N_Links_Adjuntos", "Adjunto", "entero", "N° de links de adjuntos de la solicitud."),
    ("Nombre_Adjunto", "Adjunto", "texto", "Nombre del archivo adjunto principal (parte final del link)."),
    ("Extension_Adjunto", "Adjunto", "texto", "Tipo del archivo subido: pdf, jpg, zip, rar, xlsx…"),
    ("N_Archivos_En_Adjunto", "Adjunto", "entero", "Archivos que contenía el adjunto (si es comprimido)."),
    ("Resumen_Tipos_Adjunto", "Adjunto", "texto", "Composición del adjunto, ej. '3 jpg, 1 pdf'."),
    ("Clase_Adjunto", "Adjunto", "texto", "Clase del comprobante de la fila: Imagen (OCR) / PDF con texto / PDF escaneado (OCR) / Planilla / Correo / Word / Lectura manual / Sin adjunto…"),
    ("Nombre_Archivo", "Adjunto", "texto", "Nombre del archivo de la fila (sin carpetas)."),
    ("Carpeta_Interna", "Adjunto", "texto", "Carpeta o comprimido que contiene el archivo de la fila."),
]
COLUMNAS = []
_nuevas_ins = False
for c in G.COLUMNAS:
    if c[0] in QUITAR:
        continue
    if c[1] == "Organización" and not _nuevas_ins:
        COLUMNAS += NUEVAS_SOLICITUD
        _nuevas_ins = True
    COLUMNAS.append(c)
COLS = [c[0] for c in COLUMNAS]
TIPOS = {c[0]: c[2] for c in COLUMNAS}
G.COLUMNAS, G.COLS, G.TIPOS = COLUMNAS, COLS, TIPOS
G.TRACK = [c[0] for c in COLUMNAS if c[1] == "Seguimiento de facturación"]
EXT_COMPR = {"zip", "rar", "7z"}


def _ext(nombre: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]{2,5})(?:\?|$)", nombre or "")
    return m.group(1).lower() if m else ""


def clase(tipo_archivo: str, metodo: str, estado_lectura: str) -> str:
    t, m = (tipo_archivo or "").lower(), (metodo or "").lower()
    if "manual" in m:
        return "Lectura manual (imagen/PDF revisado)"
    if m == "cuadratura":
        return "Cuadratura (sin comprobante legible)"
    if t == "pdf":
        return "PDF escaneado (OCR)" if "ocr" in m else "PDF con texto"
    if t in ("jpg", "jpeg", "png", "webp", "heic", "gif", "bmp", "tif", "tiff"):
        return "Imagen / foto (OCR)"
    if t in ("xls", "xlsx", "xlsm", "csv", "ods") or "planilla" in m:
        return "Planilla Excel"
    if t in ("doc", "docx"):
        return "Documento Word"
    if t in ("msg", "eml"):
        return "Correo"
    if t == "txt":
        return "Texto"
    return t or ("Sin adjunto" if estado_lectura == "SIN ADJUNTO" else "Otro")


def huella(libros: list[Path], *extra: Path) -> str:
    """Identifica la entrada (libros + reglas + lecturas manuales) para no reutilizar una caché obsoleta."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted(libros):
        st = Path(f).stat()
        h.update(f"{Path(f).name}|{st.st_size}|{int(st.st_mtime)}".encode())
    for f in (Path(P.__file__), Path(R.__file__), Path(__file__), Path(I.__file__)):
        h.update(f.read_bytes())
    for d in extra:
        if Path(d).is_dir():
            for f in sorted(Path(d).glob("*.json")):
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


def cargar_estado(pkl: Path, fp: str):
    """Estado procesado si existe y corresponde a la misma entrada; si no, None."""
    sello = Path(f"{pkl}.fp")
    if not pkl.exists():
        return None
    if not sello.exists() or sello.read_text().strip() != fp:
        log.warning("%s es de otra entrada o versión del código: se recalcula", pkl.name)
        return None
    return pickle.load(open(pkl, "rb"))


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def sugerencia(refact: str, carpeta: str, proyecto: str) -> tuple[str, str]:
    if refact == "SI":
        return "FACTURABLE", "Aprobador marcó refacturable"
    if refact == "NO":
        return "NO FACTURABLE", "Aprobador marcó no refacturable"
    if carpeta:
        return carpeta, "Nombre de la carpeta del adjunto"
    return "POR REVISAR", "Sin pista de facturación" + (" (tiene N° de proyecto)" if proyecto else "")


def enriquecer(rows: list[dict], extra: dict, urls: dict, res_by_id: dict):
    # solicitudes con monto declarado 0: una lectura OCR no puede crear gasto → se deja 0 y se alerta
    decl = {r["ID_Rendicion"]: r["Declarado_Rendicion_CLP"] for r in rows if r.get("Fila_Principal") == 1}
    for r in rows:
        if (decl.get(r["ID_Rendicion"]) or 0) == 0 and (r.get("Monto_CLP") or 0) > 0:
            r["Monto_CLP"] = 0.0
            r["Alertas_Rendicion"] = ((r.get("Alertas_Rendicion") or "") + " Monto declarado = 0: lectura no cuadrada al gasto.").strip()
    por_hash: dict[str, set] = {}
    for i, x in res_by_id.items():
        if x.get("hash_adjunto"):
            por_hash.setdefault(x["hash_adjunto"], set()).add(i)
    comparte = {i: ", ".join(sorted(g - {i})) for g in por_hash.values() if len(g) > 1 for i in g}
    for r in rows:
        d, u = extra.get(r["ID_Rendicion"], {}), urls.get(r["ID_Rendicion"], [])
        res = res_by_id.get(r["ID_Rendicion"], {})
        arch = res.get("archivos") or []
        tipos = pd.Series([a.get("tipo") for a in arch]).value_counts() if arch else pd.Series(dtype=int)
        nombre_adj = (u[0].rsplit("/", 1)[-1] if u else "")
        from urllib.parse import unquote
        nombre_adj = unquote(nombre_adj)
        m2 = (d.get("motivo_e2") or "") + " " + (d.get("motivo_e1") or "")
        refact = "NO" if re.search(r"no\s+refacturable", m2, re.I) else ("SI" if re.search(r"refacturable", m2, re.I) else "")
        fact_sug, fuente = sugerencia(refact, r.get("Facturable_Segun_Carpeta", ""), d.get("proyecto", "") or d.get("proyecto_sel", ""))
        interno = r.get("Archivo_Interno") or ""
        r.update({
            "Libro_Origen": d.get("libro", ""), "N_Libros_Donde_Aparece": 1 + len(d.get("otros", [])),
            "Estado_En_Otros_Libros": "; ".join(d.get("otros", [])), "Comparte_Adjunto_Con": comparte.get(r["ID_Rendicion"], ""),
            "Hoja_Origen": d.get("hoja", ""), "Canal": d.get("canal", ""), "Etapa_En_Curso": d.get("etapa", ""),
            "Estado_Solicitud": d.get("estado_sol", ""), "Etapas_Canal": d.get("etapas", ""),
            "Dias_Sin_Movimiento": _int(d.get("dias_sin_mov")), "Tiempo_Resolucion_Dias": _int(d.get("dias_res")),
            "Accion_Solicitada": d.get("accion", ""), "Tipo_Fondo": d.get("fondo", ""),
            "N_Requerimiento_Asociado": d.get("req", ""), "Tipo_Documento_Declarado": d.get("tipo_doc", ""),
            "N_Proyecto": d.get("proyecto", "") or d.get("proyecto_sel", ""), "Linea_Negocio": d.get("linea", ""),
            "Empresa_Cliente": d.get("empresa", ""), "Razon_Social": d.get("razon_social", ""),
            "Cod_CC_Cliente": d.get("cod_cc_cliente", ""), "Centro_Costo_Cliente": d.get("cc_cliente", ""),
            "RUT_Asociado": d.get("rut_asoc", ""), "Nombre_Asociado": d.get("nom_asoc", ""),
            "Jefatura_RUT": d.get("jefatura", ""), "Motivo_Etapa1": d.get("motivo_e1", ""), "Motivo_Etapa2": d.get("motivo_e2", ""),
            "Detalle_Rechazo": " | ".join(x for x in (d.get("otro_e1", ""), d.get("otro_e2", "")) if x),
            "Refacturable_Segun_Aprobador": refact, "Facturable_Sugerido": fact_sug, "Fuente_Sugerencia": fuente,
            "Link_Adjunto_2": u[1] if len(u) > 1 else "", "N_Links_Adjuntos": len(u), "Nombre_Adjunto": nombre_adj,
            "Extension_Adjunto": _ext(nombre_adj), "N_Archivos_En_Adjunto": len(arch),
            "Resumen_Tipos_Adjunto": ", ".join(f"{n} {t}" for t, n in tipos.items()) if len(tipos) else "",
            "Clase_Adjunto": clase(r.get("Tipo_Archivo"), r.get("Metodo_Lectura"), r.get("Estado_Lectura")),
            "Nombre_Archivo": interno.split(">")[-1].strip().rsplit("/", 1)[-1] if interno else "",
            "Carpeta_Interna": interno.rsplit(">", 1)[0].strip() if ">" in interno else "",
            "Rut_Rendidor": d.get("rut", ""), "Fecha_Resolucion": d.get("fecha_res") or None,
            "Responsables_Aprobacion": d.get("resp", ""),
        })
        r["Estado_Talana"] = d.get("estado", r.get("Estado_Talana", ""))
        r["Tipo_Registro"] = G.TIPO_REG_REND


def conciliacion(df: pd.DataFrame, estado: P.Estado, extra: dict) -> list:
    declarado = df["Declarado_Rendicion_CLP"].sum()
    gasto = df["Monto_CLP"].sum()
    por = df.groupby("ID_Rendicion").agg(g=("Monto_CLP", "sum"), d=("Declarado_Rendicion_CLP", "sum"))
    out = [("Fecha de generación", dt.datetime.now().strftime("%d-%m-%Y %H:%M")), ("Filas totales", len(df)),
           ("Rendiciones únicas", df["ID_Rendicion"].nunique()), ("Gasto rendido (suma Monto_CLP)", round(gasto)),
           ("Declarado en Talana (suma 1ª fila)", round(declarado)), ("Diferencia gasto − declarado", round(gasto - declarado)),
           ("Rendiciones que no cuadran (|dif| > $1)", int(((por.g - por.d).abs() > 1).sum()))]
    for est, g in df.groupby("Estado_Talana")["Monto_CLP"].sum().items():
        out.append((f"Gasto con estado «{est}»", round(g)))
    return out + list(estado.stats.items()) + list(extra.items())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--libros", nargs="+", required=True, help="uno o más Excel 'Centro de Requerimientos' (se eliminan duplicados por ID)")
    ap.add_argument("--hojas", nargs="*", help="hojas a analizar (por defecto: todas las que contienen 'reembolso')")
    ap.add_argument("--salida", default="salida_req")
    ap.add_argument("--nombre", default="Base_Rendiciones_Requerimientos")
    ap.add_argument("--cache", default=None, help="carpeta de caché de adjuntos (se reutiliza entre libros)")
    ap.add_argument("--lecturas", default="lecturas")
    ap.add_argument("--indice", default="revisar/indice.json")
    ap.add_argument("--pkl", default=None, help="resultados de la lectura de adjuntos")
    ap.add_argument("--estado-pkl", default=None)
    ap.add_argument("--sin-parquet", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")

    libros, salida = [Path(x) for x in a.libros], Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    cache = Path(a.cache) if a.cache else libros[0].parent / "cache_adjuntos"
    rend, extra, urls, informe = I.leer_libros(libros, a.hojas)
    for nombre, fmax, n, dup in informe:
        log.info("Libro %-40s (hasta %s): %5d solicitudes de reembolso, %5d duplicadas descartadas", nombre, fmax, n, dup)
    maestro = salida / f"{a.nombre}.xlsx"

    fp = huella(libros, Path(a.lecturas))
    estado = cargar_estado(Path(a.estado_pkl), fp) if a.estado_pkl else None
    if estado is None:
        results = L.leer_adjuntos(rend, urls, cache, Path(a.pkl) if a.pkl else None, fp=L.huella_entrada(rend, urls))
        for res, r in zip(results, rend):                      # datos de la solicitud siempre desde el libro
            res["rendicion"] = {**r.__dict__}
        lecturas, index = P.cargar_lecturas(Path(a.lecturas), Path(a.indice))
        ids = {r.id for r in rend}
        lecturas = {k: v for k, v in lecturas.items() if k in ids}
        log.info("Pipeline: %d rendiciones, %d con lectura manual previa", len(results), len(lecturas))
        estado = P.ejecutar_pipeline(results, lecturas, index, cache)
        if a.estado_pkl:
            pickle.dump(estado, open(a.estado_pkl, "wb"))
            Path(f"{a.estado_pkl}.fp").write_text(fp)

    talana = {i: {"rut": d["rut"], "accion": d["accion"], "fondo": d["fondo"], "req": d["req"], "tipo_doc": d["tipo_doc"],
                  "periodo": "", "fres": d["fecha_res"], "resp": d["resp"]} for i, d in extra.items()}
    rows = G.filas_rendiciones(estado, talana, {})
    enriquecer(rows, extra, urls, {x["rendicion"]["id"]: x for x in estado.results})
    df = G.tipar(pd.DataFrame(rows, columns=COLS))
    df, conservados, huerfanos = G.aplicar_facturacion_previa(df, maestro, salida)
    if maestro.exists():
        (salida / "historial").mkdir(exist_ok=True)
        shutil.copy2(maestro, salida / "historial" / f"{a.nombre}_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
    df = G.tipar(df)
    concil = conciliacion(df, estado, {**{f"Libro «{n}»: solicitudes leídas / duplicados descartados": f"{k} / {d}" for n, _, k, d in informe},
                                       "Registros con seguimiento de facturación conservado": conservados,
                                       "Seguimiento sin equivalente (ver facturacion_huerfanos.csv)": huerfanos})
    G.escribir_xlsx(df, maestro, concil)
    df.to_csv(salida / f"{a.nombre}.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    if not a.sin_parquet:
        try:
            df.to_parquet(salida / f"{a.nombre}.parquet", index=False)
        except Exception as e:  # noqa: BLE001
            log.warning("Sin parquet: %s", e)
    print("\n=== BASE GENERADA ===")
    for k, v in concil:
        print(f"  {k:<52} {v:>16,}".replace(",", ".") if isinstance(v, (int, float)) else f"  {k:<52} {v}")
    print(f"  Columnas: {len(COLS)} | Archivo maestro: {maestro}")


if __name__ == "__main__":
    main()
