# -*- coding: utf-8 -*-
"""
GENERADOR DE BASE ÚNICA PARA POWER BI — Rendiciones por CECO / Rendidor / Concepto (+ Cabify corporativo)
=========================================================================================================
Un solo comando genera una tabla plana (una fila por comprobante/ítem) con todo el detalle, lista para Power BI:

    python generar_base_powerbi.py --excel Presupuestos_Rendiciones_actualizado.xlsx

Entradas
  --excel     Excel de Talana (hojas "Reembolsos Consolidado", "Cabify Extracto", "Auditoria Cabify vs Rend")
  --lecturas  carpeta con las lecturas manuales de comprobantes (lecturas/lote_*.json)           [opcional]
  --indice    índice de páginas exportadas para esas lecturas (revisar/indice.json)              [opcional]
  --pkl       resultados del RPA (lectura automática). Si no existe se lee desde la caché de adjuntos

Salidas (carpeta --salida, por defecto salida_powerbi/)
  Base_Rendiciones_PowerBI.xlsx    ← ARCHIVO MAESTRO. Tabla "tblBase" + Diccionario + Conciliación.
                                     Al final tiene las columnas de SEGUIMIENTO DE FACTURACIÓN (se editan aquí).
  Base_Rendiciones_PowerBI.csv     copia de sólo lectura (UTF-8)
  Base_Rendiciones_PowerBI.parquet copia de sólo lectura, tipada y más rápida para Power BI
  historial/                       respaldo del maestro anterior en cada ejecución
  facturacion_huerfanos.csv        seguimiento de registros que ya no existen en la nueva base (si los hubiera)

Seguimiento de facturación: al regenerar la base, lo que se haya anotado en las columnas finales
(Estado_Facturacion … Obs_Facturacion) se conserva, asociado a la columna Clave_Item (estable entre ejecuciones).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
import pickle
import re
import shutil
import sys
from pathlib import Path

os.environ.setdefault("OMP_THREAD_LIMIT", "1")      # Tesseract con 1 hilo por proceso: ~500x más rápido en equipos de pocos núcleos

import pandas as pd  # noqa: E402

import pipeline_rendiciones as P  # noqa: E402
import rpa_rendiciones as R  # noqa: E402

log = logging.getLogger("base_powerbi")

ESTADOS_FACT = ["Sin clasificar", "Facturado", "Pendiente de facturar", "No facturable", "Facturación parcial"]
TIPO_REG_REND, TIPO_REG_CAB = "Rendición", "Cabify corporativo"

# (nombre, grupo, tipo, descripción). El ORDEN de esta lista es el orden de las columnas.
COLUMNAS = [
    ("Clave_Item", "Identificación", "texto", "Identificador estable del registro; se mantiene entre ejecuciones y ata el seguimiento de facturación."),
    ("Tipo_Registro", "Identificación", "texto", "'Rendición' (gasto rendido en Talana) o 'Cabify corporativo' (viaje del extracto Cabify, informativo)."),
    ("ID_Rendicion", "Identificación", "texto", "ID de la solicitud en Talana (CDR…). Vacío en filas Cabify."),
    ("Estado_Talana", "Identificación", "texto", "Estado de la solicitud en Talana."),
    ("Estado_Lectura", "Identificación", "texto", "Resultado de la lectura de adjuntos: OK, PARCIAL, ERROR…; '+ LECTURA MANUAL' si se leyó a mano."),
    ("Cuenta_Declarada", "Solicitud Talana", "texto", "Cuenta contable declarada por el rendidor (ej. 407 - GASTOS VARIOS, 002 - MOVILIZACIÓN)."),
    ("Accion_Solicitada", "Solicitud Talana", "texto", "Acción de la solicitud (reembolso, cierre de fondo, etc.)."),
    ("Tipo_Fondo", "Solicitud Talana", "texto", "Qué se sustenta o reembolsa (Fondo Fijo, Fondo Temporal, …)."),
    ("N_Requerimiento_Asociado", "Solicitud Talana", "texto", "Requerimiento al que se asocia (p. ej. el fondo que se cierra)."),
    ("Tipo_Documento_Declarado", "Solicitud Talana", "texto", "Tipo de documento que declaró adjuntar el rendidor."),
    ("Periodo_Origen", "Solicitud Talana", "texto", "Periodo de origen de la solicitud en Talana."),
    ("Fecha_Resolucion", "Solicitud Talana", "fecha", "Fecha de resolución de la solicitud."),
    ("Responsables_Aprobacion", "Solicitud Talana", "texto", "Aprobadores de la solicitud."),
    ("CECO", "Organización", "texto", "Centro de costo completo (ej. 230 - SANTIAGO)."),
    ("CECO_Codigo", "Organización", "texto", "Código del CECO (ej. 230)."),
    ("CECO_Nombre", "Organización", "texto", "Nombre del CECO (ej. SANTIAGO)."),
    ("Rendidor", "Organización", "texto", "Persona que rinde. En filas Cabify: el rendidor de Talana si el pasajero calzó con uno; si no, el pasajero."),
    ("Rut_Rendidor", "Organización", "texto", "RUT del creador de la solicitud en Talana."),
    ("Usuario_Cabify", "Organización", "texto", "SI/NO: figura como usuario Cabify en la hoja 'Auditoria Cabify vs Rend' (en filas Cabify siempre SI)."),
    ("Cabify_Pasajero_Match", "Organización", "texto", "Nombre(s) del pasajero Cabify que calzan con este rendidor (cruce por nombre)."),
    ("Cabify_Match_Fuente", "Organización", "texto", "Cómo se hizo el cruce: 'Auditoría Cabify vs Rend' o 'Nombre + apellido' (validar homónimos)."),
    ("Fecha_Reporte", "Fechas", "fecha", "Fecha para el eje de tiempo: fecha de la rendición; en Cabify, el 1° del mes del viaje."),
    ("Anio", "Fechas", "entero", "Año de Fecha_Reporte."),
    ("Mes_Num", "Fechas", "entero", "Mes (1-12) de Fecha_Reporte."),
    ("Mes_Nombre", "Fechas", "texto", "Nombre del mes en español."),
    ("Periodo", "Fechas", "texto", "AAAA-MM de Fecha_Reporte (ordena bien como texto)."),
    ("Fecha_Documento", "Fechas", "fecha", "Fecha del comprobante (boleta, factura, voucher) cuando se pudo leer."),
    ("Dias_Doc_a_Rendicion", "Fechas", "entero", "Días entre el comprobante y la rendición (negativo: comprobante posterior)."),
    ("Concepto", "Clasificación", "texto", "Concepto oficial de los 17 definidos (+ SIN_IDENTIFICAR)."),
    ("Familia_Concepto", "Clasificación", "texto", "Agrupación de conceptos: Movilización, Viaje, Alimentación, Oficina y operación, OTROS, Sin detalle."),
    ("Concepto_Detallado", "Clasificación", "texto", "Concepto con su apertura: 'OTROS · <subtipo>' o 'SIN_IDENTIFICAR · <motivo>'."),
    ("Subtipo_OTROS", "Clasificación", "texto", "Qué hay dentro de OTROS (transferencias, arriendo cowork, telefonía, eventos…)."),
    ("Nivel_Detalle", "Clasificación", "texto", "Calidad del detalle: Concepto identificado / OTROS con subtipo / Sin detalle · con concepto probable / Sin detalle · sin pista."),
    ("Concepto_Probable", "Clasificación", "texto", "Mejor estimación del concepto cuando no hay comprobante legible (no es un dato leído)."),
    ("Base_Probable", "Clasificación", "texto", "En qué se basa el concepto probable."),
    ("Concepto_Estimado", "Clasificación", "texto", "Concepto + probable para lo que no tiene detalle. Filtrar por Nivel_Detalle para separar lo estimado."),
    ("Detalle_Rendicion", "Clasificación", "texto", "Nombres de carpetas/archivos/correos del adjunto (pistas de qué se rindió)."),
    ("Emisor", "Comprobante", "texto", "Comercio o persona que emite el comprobante."),
    ("RUT_Emisor", "Comprobante", "texto", "RUT del emisor, si se pudo leer."),
    ("Folio", "Comprobante", "texto", "N° de boleta/factura, si se pudo leer."),
    ("Tipo_Documento", "Comprobante", "texto", "Tipo de documento leído (boleta, factura, planilla, formulario…)."),
    ("Descripcion", "Comprobante", "texto", "Descripción del ítem tal como se leyó."),
    ("Palabra_Clave", "Comprobante", "texto", "Regla o palabra clave que determinó el concepto."),
    ("Metodo_Lectura", "Comprobante", "texto", "Cómo se leyó: texto_pdf_texto, ocr_tesseract, planilla_reglas, planilla_relectura, formulario_rendicion, lectura_manual, cuadratura, extracto_cabify."),
    ("Confianza", "Comprobante", "texto", "Confianza de la lectura (alta/media/baja), si aplica."),
    ("Archivo_Interno", "Comprobante", "texto", "Ruta del archivo dentro del adjunto."),
    ("Tipo_Archivo", "Comprobante", "texto", "pdf, jpg, xlsx…"),
    ("Pagina", "Comprobante", "entero", "Página o foto dentro del archivo."),
    ("Facturable_Segun_Carpeta", "Comprobante", "texto", "FACTURABLE / NO FACTURABLE según el nombre de la carpeta del adjunto (hoy sólo viene en Trade). Es una SUGERENCIA, no el seguimiento."),
    ("Detalle_Carpeta", "Comprobante", "texto", "Descripción del gasto según el nombre de la carpeta (Trade)."),
    ("Persona_Carpeta", "Comprobante", "texto", "Persona indicada en el nombre de la carpeta (Trade)."),
    ("Ticket_Cabify", "Cabify", "texto", "Código de ticket del viaje."),
    ("Pasajero_Cabify", "Cabify", "texto", "Pasajero del viaje."),
    ("Solicitante_Cabify", "Cabify", "texto", "Quien solicitó el viaje."),
    ("Vertical_Cabify", "Cabify", "texto", "Vertical del solicitante."),
    ("Estado_Viaje_Cabify", "Cabify", "texto", "Estado final del viaje (completado, cancelado…)."),
    ("Link_Adjunto", "Comprobante", "texto", "Link al adjunto en Talana (en Power BI: categoría de datos 'URL web')."),
    ("Monto_CLP", "Montos", "moneda", "GASTO en CLP, cuadrado a lo declarado en Talana (suma por rendición = declarado). 0 en filas Cabify."),
    ("Monto_Bruto_Leido_CLP", "Montos", "moneda", "Monto leído en el comprobante antes del prorrateo."),
    ("Factor_Prorrateo", "Montos", "decimal", "Monto_CLP / Monto_Bruto_Leido. <1 cuando lo leído superaba lo declarado (planilla resumen + detalle, boletas repetidas)."),
    ("Monto_Cabify_CLP", "Montos", "moneda", "Precio del viaje Cabify en CLP (sólo filas Cabify; informativo, no es parte de Monto_CLP)."),
    ("Declarado_Rendicion_CLP", "Montos", "moneda", "Monto declarado en Talana. SOLO en la primera fila de cada rendición (evita sumarlo varias veces)."),
    ("Fila_Principal", "Montos", "entero", "1 en la primera fila de cada rendición, 0 en el resto."),
    ("Alertas", "Calidad y auditoría", "texto", "Alertas del ítem (alerta de concepto, documento antiguo, comprobante repetido…)."),
    ("Alertas_Rendicion", "Calidad y auditoría", "texto", "Alertas de la rendición completa (mismo adjunto en otra rendición, diferencia de monto…)."),
    ("Observacion", "Calidad y auditoría", "texto", "Observaciones de la lectura (OCR corregido, comprobante duplicado, ilegible, etc.)."),
    ("Alerta_Duplicado", "Calidad y auditoría", "texto", "SI si alguna alerta u observación indica posible duplicidad o comprobante usado en otra rendición."),
    ("Estado_Facturacion", "Seguimiento de facturación", "texto", "EDITAR. Sin clasificar / Facturado / Pendiente de facturar / No facturable / Facturación parcial."),
    ("Cliente_Facturado", "Seguimiento de facturación", "texto", "EDITAR. Cliente al que se facturó (o se facturará)."),
    ("N_Factura", "Seguimiento de facturación", "texto", "EDITAR. N° de la factura emitida."),
    ("Fecha_Factura", "Seguimiento de facturación", "fecha", "EDITAR. Fecha de la factura."),
    ("Monto_Facturado_CLP", "Seguimiento de facturación", "moneda", "EDITAR. Monto facturado (CLP)."),
    ("Obs_Facturacion", "Seguimiento de facturación", "texto", "EDITAR. Comentario libre del seguimiento."),
]
COLS = [c[0] for c in COLUMNAS]
TIPOS = {c[0]: c[2] for c in COLUMNAS}
TRACK = [c[0] for c in COLUMNAS if c[1] == "Seguimiento de facturación"]

CARPETA_RX = re.compile(r"(?:^|/|> )([^/>]*?) - (\d{4})_(\d{2})_(\d{2}) - ([A-Za-zÁÉÍÓÚáéíóú]+)[^/>]*? - "
                        r"(FACTURABLE|NO FACTURABLE|NO) - ([^/>]+)", re.I)
DUP_RX = re.compile(r"duplic|repetid|mismo (adjunto|voucher|detalle|ticket|comprobante|pago)|también (respalda|usado|en )|"
                    r"ya rendid|misma boleta|usado en id", re.I)


# ======================================================================================
# DATOS DE TALANA
# ======================================================================================
def _col(df, *prefijos):
    for p in prefijos:
        for c in df.columns:
            if P.norm(c).startswith(P.norm(p)):
                return c
    return None


def leer_talana(xlsx: Path, hoja: str = "Reembolsos Consolidado") -> dict:
    """Campos adicionales de la solicitud en Talana, por ID."""
    df = pd.read_excel(xlsx, sheet_name=hoja)
    if "ID" not in df.columns:
        df = pd.read_excel(xlsx, sheet_name=hoja, header=1)
    campos = {"rut": _col(df, "Rut Creador"), "accion": _col(df, "Seleccione que acción requiere"),
              "fondo": _col(df, "Seleccione que desea sustentar"), "req": _col(df, "Indicar el número del requerimiento"),
              "tipo_doc": _col(df, "Indique el tipo de documento"), "periodo": _col(df, "Periodo Origen"),
              "fres": _col(df, "Fecha resolución"), "resp": _col(df, "Nombre(s) Responsable")}
    out = {}
    for _, row in df.iterrows():
        rid = row["ID"]
        if pd.isna(rid):
            continue
        d = {}
        for k, c in campos.items():
            v = row[c] if c else None
            d[k] = None if v is None or (not isinstance(v, str) and pd.isna(v)) else v
        out[str(rid)] = d
    return out


def _txt(v):
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v).strip()


def _fecha(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    try:
        return R.parse_date(v)
    except Exception:  # noqa: BLE001
        return None


def info_carpeta(ruta: str) -> tuple[str, str, str]:
    """(facturable, detalle, persona) según el nombre de carpeta 'PERSONA - AAAA_MM_DD - Tipo - FACTURABLE - Detalle'."""
    m = CARPETA_RX.search(ruta or "")
    if not m:
        return "", "", ""
    fact = "FACTURABLE" if m.group(6).upper() == "FACTURABLE" else "NO FACTURABLE"
    return fact, m.group(7).strip()[:140], m.group(1).strip()


def estimado(prob: str) -> str:
    if not prob:
        return "SIN_IDENTIFICAR"
    if prob.startswith("Mixto"):
        return "MIXTO"
    if prob.startswith("Movilización"):
        return "MOVILIZACION_SIN_DETALLE"
    if prob.startswith("OTROS"):
        return "OTROS"
    return prob


# ======================================================================================
# FILAS
# ======================================================================================
class Claves:
    def __init__(self):
        self.occ = {}

    def __call__(self, base: str) -> str:
        n = self.occ[base] = self.occ.get(base, 0) + 1
        return hashlib.sha1(f"{base}|{n}".encode()).hexdigest()[:12]


def _periodo_cols(f: dt.date | None) -> dict:
    if not f:
        return {"Fecha_Reporte": None, "Anio": None, "Mes_Num": None, "Mes_Nombre": "", "Periodo": ""}
    return {"Fecha_Reporte": f, "Anio": f.year, "Mes_Num": f.month, "Mes_Nombre": P.MESES_ES[f.month - 1],
            "Periodo": f"{f.year}-{f.month:02d}"}


def cruce_cabify(xlsx: Path, rendidores: list[str]):
    """Cruce Talana→Cabify por nombre. Devuelve (cabify_df, {rendidor:(pasajeros, fuente)}, {pasajero_norm: rendidor})."""
    cab = pd.read_excel(xlsx, sheet_name="Cabify Extracto")
    cab = cab[cab["Estado final"] != "No encontrado"].copy()
    cab["pn"] = cab["Pasajero"].map(P.norm)
    aud = pd.read_excel(xlsx, sheet_name="Auditoria Cabify vs Rend", header=1)
    manual = {}
    for _, a in aud[aud["¿Coincide en Cabify?"] == "SI"].iterrows():
        manual[P.norm(a["Nombre (origen: Talana)"])] = {P.norm(n) for n in str(a["Nombre (origen: Cabify)"]).split(";")}
    pas = set(cab["pn"])
    disp = cab.drop_duplicates("pn").set_index("pn")["Pasajero"].map(_txt).to_dict()
    match, pas2rend = {}, {}
    for rend in sorted(set(rendidores)):
        t = P.norm(rend)
        if t in manual:
            names, how = manual[t], "Auditoría Cabify vs Rend"
        else:
            tk = t.split()
            names = {p for p in pas if len(p.split()) >= 2 and tk and p.split()[0] == tk[0] and p.split()[-1] in tk[1:]}
            how = "Nombre + apellido" if names else ""
        names = names & pas
        if names:
            match[rend] = (sorted(disp[p] for p in names), how)
            for p in names:
                pas2rend.setdefault(p, rend)
    return cab, match, pas2rend


def filas_rendiciones(estado: P.Estado, talana: dict, cab_match: dict) -> list[dict]:
    claves, rows = Claves(), []
    for res in estado.results:
        r = res["rendicion"]
        rid = r["id"]
        fecha = R.parse_date(r.get("fecha"))
        t = talana.get(rid, {})
        c_ceco = str(r["ceco"] or "")
        cod, _, nom = c_ceco.partition(" - ")
        prob, base_prob, detalle = estado.prob.get(rid, ("", "", ""))
        cm = cab_match.get(r["rendidor"])
        items = [it for it in res["items"] if it.get("monto")]
        first = True
        for it in items:
            concepto = it.get("concepto") or "OTROS"
            sub = P.subtipo_otros(it)
            generico = concepto == R.SIN_ID or sub in P.GENERICOS
            if concepto == R.SIN_ID:
                cd = f"SIN_IDENTIFICAR · {P.motivo_sin(it)}"
            elif concepto == "OTROS":
                cd = f"OTROS · {sub}"
            else:
                cd = concepto
            if generico:
                nivel = "Sin detalle · con concepto probable" if prob else "Sin detalle · sin pista"
            elif concepto == "OTROS":
                nivel = "OTROS con subtipo"
            else:
                nivel = "Concepto identificado"
            est = estimado(prob) if generico else concepto
            archivo = it.get("archivo") or ""
            fact, det_carp, pers = info_carpeta(archivo)
            fdoc = _fecha(it.get("fecha_doc"))
            alert_it, alert_r, obs = _txt(it.get("alertas")), _txt(res.get("alertas"))[:600], _txt(it.get("observacion"))
            key = (f"{rid}|cuadratura" if it.get("metodo") == "cuadratura" else
                   "|".join([rid, archivo, str(it.get("pagina") or ""), str(it.get("metodo") or ""),
                             P.norm(it.get("descripcion"))[:60], P.norm(it.get("emisor"))[:40],
                             str(round(it.get("monto_bruto") or 0))]))
            row = {
                "Clave_Item": claves(key), "Tipo_Registro": TIPO_REG_REND, "ID_Rendicion": rid,
                "Estado_Talana": _txt(r.get("estado")), "Estado_Lectura": res.get("estado", ""),
                "Cuenta_Declarada": _txt(r.get("concepto_declarado")), "Accion_Solicitada": _txt(t.get("accion")),
                "Tipo_Fondo": _txt(t.get("fondo")), "N_Requerimiento_Asociado": _txt(t.get("req")),
                "Tipo_Documento_Declarado": _txt(t.get("tipo_doc")), "Periodo_Origen": _txt(t.get("periodo")),
                "Fecha_Resolucion": _fecha(t.get("fres")), "Responsables_Aprobacion": _txt(t.get("resp")),
                "CECO": c_ceco, "CECO_Codigo": cod.strip(), "CECO_Nombre": nom.strip(), "Rendidor": r["rendidor"],
                "Rut_Rendidor": _txt(t.get("rut")), "Usuario_Cabify": r.get("usa_cabify") or "NO",
                "Cabify_Pasajero_Match": "; ".join(cm[0]) if cm else "",
                "Cabify_Match_Fuente": cm[1] if cm else "",
                **_periodo_cols(fecha), "Fecha_Documento": fdoc,
                "Dias_Doc_a_Rendicion": (fecha - fdoc).days if fecha and fdoc else None,
                "Concepto": concepto, "Familia_Concepto": P.familia(concepto), "Concepto_Detallado": cd,
                "Subtipo_OTROS": sub if concepto == "OTROS" else "", "Nivel_Detalle": nivel,
                "Concepto_Probable": prob if generico else "", "Base_Probable": base_prob if generico and prob else "",
                "Concepto_Estimado": est, "Detalle_Rendicion": detalle,
                "Emisor": _txt(it.get("emisor")), "RUT_Emisor": _txt(it.get("rut_emisor")), "Folio": _txt(it.get("folio")),
                "Tipo_Documento": _txt(it.get("tipo_documento")), "Descripcion": _txt(it.get("descripcion")),
                "Palabra_Clave": _txt(it.get("palabra_clave")), "Metodo_Lectura": _txt(it.get("metodo")),
                "Confianza": _txt(it.get("confianza")), "Archivo_Interno": archivo, "Tipo_Archivo": _txt(it.get("tipo_archivo")),
                "Pagina": it.get("pagina"), "Facturable_Segun_Carpeta": fact, "Detalle_Carpeta": det_carp,
                "Persona_Carpeta": pers, "Link_Adjunto": _txt(r.get("adjunto")),
                "Monto_CLP": float(it["monto"]),
                "Monto_Bruto_Leido_CLP": float(it["monto_bruto"]) if it.get("monto_bruto") is not None else None,
                "Factor_Prorrateo": round(float(it.get("factor") or 1.0), 6),
                "Declarado_Rendicion_CLP": float(r.get("monto_declarado") or 0) if first else None,
                "Fila_Principal": 1 if first else 0,
                "Alertas": alert_it, "Alertas_Rendicion": alert_r, "Observacion": obs,
                "Alerta_Duplicado": "SI" if DUP_RX.search(f"{alert_it} {alert_r} {obs}") else "NO",
            }
            first = False
            rows.append(row)
        if first:   # rendición sin ningún ítem con monto: se deja una fila para que no desaparezca del conteo
            rows.append({
                "Clave_Item": claves(f"{rid}|sin_items"), "Tipo_Registro": TIPO_REG_REND, "ID_Rendicion": rid,
                "Estado_Talana": _txt(r.get("estado")), "Estado_Lectura": res.get("estado", ""),
                "Cuenta_Declarada": _txt(r.get("concepto_declarado")), "Accion_Solicitada": _txt(t.get("accion")),
                "Tipo_Fondo": _txt(t.get("fondo")), "N_Requerimiento_Asociado": _txt(t.get("req")),
                "Tipo_Documento_Declarado": _txt(t.get("tipo_doc")), "Periodo_Origen": _txt(t.get("periodo")),
                "Fecha_Resolucion": _fecha(t.get("fres")), "Responsables_Aprobacion": _txt(t.get("resp")),
                "CECO": c_ceco, "CECO_Codigo": cod.strip(), "CECO_Nombre": nom.strip(), "Rendidor": r["rendidor"],
                "Rut_Rendidor": _txt(t.get("rut")), "Usuario_Cabify": r.get("usa_cabify") or "NO",
                **_periodo_cols(fecha), "Concepto": R.SIN_ID, "Familia_Concepto": P.familia(R.SIN_ID),
                "Concepto_Detallado": "SIN_IDENTIFICAR · Rendición sin monto declarado", "Nivel_Detalle": "Sin detalle · sin pista",
                "Concepto_Estimado": "SIN_IDENTIFICAR", "Detalle_Rendicion": detalle, "Link_Adjunto": _txt(r.get("adjunto")),
                "Descripcion": "Rendición sin monto declarado ni ítems", "Metodo_Lectura": "cuadratura",
                "Monto_CLP": 0.0, "Declarado_Rendicion_CLP": float(r.get("monto_declarado") or 0), "Fila_Principal": 1,
                "Alerta_Duplicado": "NO"})
    return rows


def filas_cabify(cab: pd.DataFrame, pas2rend: dict, cecos_full: dict, claves: Claves) -> list[dict]:
    cab = cab.copy()
    cab["cod"] = cab["CECO"].astype(str).str.extract(r"(\d{3})")[0]
    cab = cab[cab["cod"].isin(cecos_full)]
    rows = []
    for _, x in cab.iterrows():
        try:
            mes = P.MESES_ES.index(str(x["Mes"]).strip().capitalize()) + 1
            fecha = dt.date(int(x["Año"]), mes, 1)
        except (ValueError, TypeError):
            continue
        cod = x["cod"]
        ceco = cecos_full[cod]
        pas = _txt(x["Pasajero"])
        rend = pas2rend.get(x["pn"])
        ticket = _txt(x.get("Código ticket"))
        rows.append({
            "Clave_Item": claves(f"CAB|{ticket}" if ticket else f"CAB|{cod}|{fecha}|{pas}|{x['Precio Fnal']}"),
            "Tipo_Registro": TIPO_REG_CAB, "CECO": ceco, "CECO_Codigo": cod, "CECO_Nombre": ceco.partition(" - ")[2],
            "Rendidor": rend or pas, "Usuario_Cabify": "SI", "Cabify_Pasajero_Match": pas if rend else "",
            "Cabify_Match_Fuente": "Cruce por nombre con Talana" if rend else "Sin match con Talana",
            **_periodo_cols(fecha), "Concepto": "APPS_TRANSPORTE", "Familia_Concepto": P.familia("APPS_TRANSPORTE"),
            "Concepto_Detallado": "APPS_TRANSPORTE · Cabify corporativo", "Nivel_Detalle": "Concepto identificado",
            "Concepto_Estimado": "APPS_TRANSPORTE", "Emisor": "Cabify (Maxi Mobility Chile)",
            "Descripcion": f"Viaje Cabify — {_txt(x['Estado final'])}", "Metodo_Lectura": "extracto_cabify",
            "Ticket_Cabify": ticket, "Pasajero_Cabify": pas, "Solicitante_Cabify": _txt(x["Solicitado por"]),
            "Vertical_Cabify": _txt(x["Vertical Solicitante"]), "Estado_Viaje_Cabify": _txt(x["Estado final"]),
            "Monto_CLP": 0.0, "Monto_Cabify_CLP": round(float(x["Precio Fnal"] or 0), 2), "Fila_Principal": 0,
            "Alerta_Duplicado": "NO"})
    return rows


# ======================================================================================
# SEGUIMIENTO DE FACTURACIÓN (se conserva entre ejecuciones)
# ======================================================================================
def aplicar_facturacion_previa(df: pd.DataFrame, previo: Path, salida: Path) -> tuple[pd.DataFrame, int, int]:
    df["Estado_Facturacion"] = "Sin clasificar"
    for c in TRACK[1:]:
        df[c] = None
    if not previo.exists():
        return df, 0, 0
    try:
        old = pd.read_excel(previo, sheet_name="Base", usecols=["Clave_Item"] + TRACK, dtype=object)
    except Exception as e:  # noqa: BLE001
        log.warning("No pude leer el seguimiento previo (%s): %s", previo.name, e)
        return df, 0, 0
    old = old[old["Clave_Item"].notna()].drop_duplicates("Clave_Item").set_index("Clave_Item")
    tiene = (old["Estado_Facturacion"].fillna("Sin clasificar") != "Sin clasificar") | \
        old[TRACK[1:]].notna().any(axis=1)
    old = old[tiene]
    if old.empty:
        return df, 0, 0
    idx = df.set_index("Clave_Item").index
    comunes = old.index.intersection(idx)
    d = df.set_index("Clave_Item")
    for c in TRACK:
        d.loc[comunes, c] = old.loc[comunes, c].values
    d["Estado_Facturacion"] = d["Estado_Facturacion"].fillna("Sin clasificar")
    huerf = old.drop(comunes)
    if len(huerf):
        huerf.reset_index().to_csv(salida / "facturacion_huerfanos.csv", index=False, encoding="utf-8-sig")
    return d.reset_index(), len(comunes), len(huerf)


# ======================================================================================
# SALIDAS
# ======================================================================================
def _como_date(v):
    """Cualquier valor de fecha (date, datetime, Timestamp, texto) -> date, o None."""
    if v is None:
        return None
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        return None if pd.isna(v) else v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return pd.to_datetime(v).date()
        except Exception:  # noqa: BLE001
            return None
    return None


def tipar(df: pd.DataFrame) -> pd.DataFrame:
    for c, t in TIPOS.items():
        if t == "fecha":
            df[c] = pd.to_datetime(df[c].map(_como_date), errors="coerce")
        elif t in ("moneda", "decimal"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        elif t == "entero":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        else:
            df[c] = df[c].where(df[c].notna(), "").astype(str)
    return df[COLS]


def conciliacion(df: pd.DataFrame, estado: P.Estado, extra: dict) -> list[tuple[str, object]]:
    rend = df[df["Tipo_Registro"] == TIPO_REG_REND]
    declarado = rend["Declarado_Rendicion_CLP"].sum()
    gasto = rend["Monto_CLP"].sum()
    por_rend = rend.groupby("ID_Rendicion").agg(g=("Monto_CLP", "sum"), d=("Declarado_Rendicion_CLP", "sum"))
    dif = por_rend[(por_rend["g"] - por_rend["d"]).abs() > 1]
    cab = df[df["Tipo_Registro"] == TIPO_REG_CAB]
    out = [("Fecha de generación", dt.datetime.now().strftime("%d-%m-%Y %H:%M")),
           ("Filas totales", len(df)), ("Filas de rendiciones", len(rend)), ("Rendiciones únicas", rend["ID_Rendicion"].nunique()),
           ("Gasto rendido (suma Monto_CLP)", round(gasto)), ("Declarado en Talana (suma 1ª fila)", round(declarado)),
           ("Diferencia gasto − declarado", round(gasto - declarado)),
           ("Rendiciones que no cuadran (|dif| > $1)", len(dif)),
           ("Viajes Cabify (filas)", len(cab)), ("Monto Cabify corporativo", round(cab["Monto_Cabify_CLP"].sum()))]
    out += [(k, v) for k, v in estado.stats.items()] + list(extra.items())
    return out


def escribir_xlsx(df: pd.DataFrame, path: Path, concil: list):
    import xlsxwriter
    wb = xlsxwriter.Workbook(str(path), {"nan_inf_to_errors": True, "strings_to_urls": False,
                                         "default_date_format": "yyyy-mm-dd"})
    f_hdr = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#1F4E78", "text_wrap": True, "valign": "vcenter"})
    f_trk = wb.add_format({"bold": True, "font_color": "black", "bg_color": "#FFC000", "text_wrap": True, "valign": "vcenter"})
    f_money = wb.add_format({"num_format": "#,##0"})
    f_dec = wb.add_format({"num_format": "0.000"})
    f_date = wb.add_format({"num_format": "yyyy-mm-dd"})
    f_bold = wb.add_format({"bold": True})
    f_wrap = wb.add_format({"text_wrap": True, "valign": "top"})

    ws = wb.add_worksheet("Base")
    cols = []
    for c in COLS:
        t = TIPOS[c]
        cols.append({"header": c, "header_format": f_trk if c in TRACK else f_hdr,
                     "format": {"moneda": f_money, "decimal": f_dec, "fecha": f_date}.get(t)})
    datos = df.astype(object)
    for c in COLS:
        if TIPOS[c] == "fecha":
            datos[c] = df[c].dt.date
        elif TIPOS[c] == "entero":
            datos[c] = df[c].map(lambda v: None if pd.isna(v) else int(v))

    def _limpio(v):
        return None if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and v != v) else v
    data = [[_limpio(v) for v in fila] for fila in datos.values.tolist()]
    ws.add_table(0, 0, len(df), len(COLS) - 1, {"name": "tblBase", "style": "Table Style Light 9", "columns": cols,
                                                "data": data})
    anchos = {"Detalle_Rendicion": 60, "Descripcion": 50, "Emisor": 34, "Archivo_Interno": 50, "Link_Adjunto": 30,
              "Concepto_Detallado": 55, "Alertas": 40, "Alertas_Rendicion": 40, "Observacion": 45,
              "Responsables_Aprobacion": 30, "Obs_Facturacion": 40, "Cliente_Facturado": 24, "Detalle_Carpeta": 40,
              "Rendidor": 30, "CECO": 28, "Estado_Facturacion": 22, "Subtipo_OTROS": 40, "Cuenta_Declarada": 28}
    for i, c in enumerate(COLS):
        ws.set_column(i, i, anchos.get(c, 14))
    ws.set_row(0, 34)
    ws.freeze_panes(1, 3)
    i_est = COLS.index("Estado_Facturacion")
    ws.data_validation(1, i_est, len(df), i_est, {"validate": "list", "source": ESTADOS_FACT, "error_title": "Valor no válido",
                                                  "error_message": "Elige un estado de la lista."})
    i_ff = COLS.index("Fecha_Factura")
    ws.data_validation(1, i_ff, len(df), i_ff, {"validate": "date", "criteria": ">", "value": dt.date(2020, 1, 1),
                                                "ignore_blank": True, "error_title": "Fecha no válida",
                                                "error_message": "Ingresa una fecha (AAAA-MM-DD)."})

    wd = wb.add_worksheet("Diccionario")
    for j, h in enumerate(["Columna", "Grupo", "Tipo", "Descripción"]):
        wd.write(0, j, h, f_hdr)
    for i, (n, g, t, d) in enumerate(COLUMNAS, 1):
        wd.write(i, 0, n, f_bold)
        wd.write(i, 1, g)
        wd.write(i, 2, t)
        wd.write(i, 3, d, f_wrap)
    wd.set_column(0, 0, 28)
    wd.set_column(1, 1, 26)
    wd.set_column(2, 2, 10)
    wd.set_column(3, 3, 120)
    wd.freeze_panes(1, 0)

    wc = wb.add_worksheet("Conciliacion")
    wc.write(0, 0, "Control de la base", f_hdr)
    wc.write(0, 1, "Valor", f_hdr)
    for i, (k, v) in enumerate(concil, 1):
        wc.write(i, 0, k)
        wc.write(i, 1, v, f_money if isinstance(v, (int, float)) else None)
    r0 = len(concil) + 3
    wc.write(r0, 0, "Seguimiento de facturación (se recalcula al abrir)", f_bold)
    for j, h in enumerate(["Estado", "N° filas", "Gasto CLP"]):
        wc.write(r0 + 1, j, h, f_hdr)
    for i, e in enumerate(ESTADOS_FACT, r0 + 2):
        wc.write(i, 0, e)
        wc.write_formula(i, 1, f'=COUNTIFS(tblBase[Estado_Facturacion],A{i + 1},tblBase[Tipo_Registro],"{TIPO_REG_REND}")')
        wc.write_formula(i, 2, f'=SUMIFS(tblBase[Monto_CLP],tblBase[Estado_Facturacion],A{i + 1})', f_money)
    wc.set_column(0, 0, 58)
    wc.set_column(1, 2, 18)
    wb.close()


def main():
    ap = argparse.ArgumentParser(description="Genera la base única (una fila por comprobante) para Power BI.")
    ap.add_argument("--excel", default="Presupuestos_Rendiciones_actualizado.xlsx")
    ap.add_argument("--salida", default="salida_powerbi")
    ap.add_argument("--lecturas", default="lecturas")
    ap.add_argument("--indice", default="revisar/indice.json")
    ap.add_argument("--pkl", default="resultados_base.pkl")
    ap.add_argument("--nombre", default="Base_Rendiciones_PowerBI")
    ap.add_argument("--reprocesar", action="store_true", help="vuelve a leer los adjuntos (usa la caché)")
    ap.add_argument("--sin-cabify", action="store_true", help="no incluye los viajes Cabify")
    ap.add_argument("--sin-parquet", action="store_true")
    ap.add_argument("--estado-pkl", default=None,
                    help="guarda/reutiliza el estado ya procesado (acelera ejecuciones repetidas al ajustar el formato)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")

    xlsx, salida = Path(a.excel), Path(a.salida)
    if not xlsx.exists():
        sys.exit(f"No existe el Excel de entrada: {xlsx}")
    salida.mkdir(parents=True, exist_ok=True)
    maestro = salida / f"{a.nombre}.xlsx"
    cache = xlsx.parent / f"{xlsx.stem}_rpa_cache"

    if a.estado_pkl and Path(a.estado_pkl).exists():
        log.info("Reutilizando estado procesado: %s (borra el archivo para recalcular)", a.estado_pkl)
        estado = pickle.load(open(a.estado_pkl, "rb"))
    else:
        results = P.cargar_resultados(xlsx, Path(a.pkl), a.reprocesar)
        lecturas, index = P.cargar_lecturas(Path(a.lecturas), Path(a.indice))
        log.info("Aplicando pipeline (%d rendiciones, %d con lectura manual)...", len(results), len(lecturas))
        estado = P.ejecutar_pipeline(results, lecturas, index, cache)
        if a.estado_pkl:
            pickle.dump(estado, open(a.estado_pkl, "wb"))
    results = estado.results

    log.info("Leyendo datos de Talana...")
    talana = leer_talana(xlsx)
    rendidores = [r["rendicion"]["rendidor"] for r in results]
    cecos_full = {c.split(" - ")[0]: c for c in R.CONFIG["cecos_operacionales"]}
    cab, cab_match, pas2rend = (None, {}, {}) if a.sin_cabify else cruce_cabify(xlsx, rendidores)

    log.info("Construyendo filas...")
    rows = filas_rendiciones(estado, talana, cab_match)
    if cab is not None:
        rows += filas_cabify(cab, pas2rend, cecos_full, Claves())
    df = tipar(pd.DataFrame(rows, columns=COLS))

    # seguimiento de facturación previo (conserva lo anotado) + respaldo del maestro anterior
    df, conservados, huerfanos = aplicar_facturacion_previa(df, maestro, salida)
    if maestro.exists():
        (salida / "historial").mkdir(exist_ok=True)
        shutil.copy2(maestro, salida / "historial" / f"{a.nombre}_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
    df = tipar(df)

    extra = {"Registros con seguimiento de facturación conservado": conservados,
             "Registros de seguimiento sin equivalente (ver facturacion_huerfanos.csv)": huerfanos}
    concil = conciliacion(df, estado, extra)
    log.info("Escribiendo %s ...", maestro)
    escribir_xlsx(df, maestro, concil)
    df.to_csv(salida / f"{a.nombre}.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    if not a.sin_parquet:
        df.to_parquet(salida / f"{a.nombre}.parquet", index=False)

    print("\n=== BASE GENERADA ===")
    for k, v in concil[1:12]:
        print(f"  {k:<48} {v:>16,}".replace(",", ".") if isinstance(v, (int, float)) else f"  {k:<48} {v}")
    print(f"  Columnas: {len(COLS)} | Archivo maestro: {maestro}")


if __name__ == "__main__":
    main()
