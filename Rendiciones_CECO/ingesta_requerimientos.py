# -*- coding: utf-8 -*-
"""Ingesta del libro "Centro de Requerimientos" (Talana): unifica las hojas de reembolso en una sola lista.

- Toma todas las hojas cuyo nombre contenga "reembolso" (o las que se indiquen) y las unifica por nombre de campo.
- Agrega los datos de la hoja "Resumen" (razón social, centro de costo del cliente, jefatura, etapa, días sin movimiento).
- Entrega `Rendicion` (formato del RPA) + un diccionario de campos extra por ID para la base de Power BI.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pandas as pd

import pipeline_rendiciones as P
import rpa_rendiciones as R

# campo destino → prefijos (normalizados) de columnas origen; el primero con valor gana
CAMPOS = {
    "rut": ["rut creador"], "rendidor": ["nombre creador"], "etapas": ["etapas canal"], "responsables_n": ["responsables"],
    "estado": ["estado"], "fecha_res": ["fecha resolucion"], "dias_res": ["tiempo de resolucion"],
    "rut_resp": ["rut responsable"], "resp": ["nombre(s) responsable"],
    "empresa": ["nombre de empresa"], "linea": ["seleccione la linea de negocio"],
    "proyecto_sel": ["indique el numero de proyecto"], "proyecto": ["escriba el numero de proyecto", "indicar n° de proyecto", "indicar n de proyecto"],
    "accion": ["seleccione que accion requiere"], "fondo": ["seleccione que desea sustentar"],
    "req": ["indicar el numero del requerimiento"], "monto": ["indique monto a sustentar"],
    "tipo_doc": ["indique el tipo de documento"], "ceco": ["indique el ceco"], "cuenta": ["indique cuenta contable"],
    "rut_asoc": ["rut del asociado"], "nom_asoc": ["nombre y apellido del asociado"],
    "estado_sol": ["indique estado de la solicitud"],
}
URL_PREFIJOS = ["adjuntar el archivo", "adjuntar captura", "adjuntar archivo"]


def _s(v) -> str:
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) or str(v).strip() in ("Sin información", "nan") else str(v).strip()


def _monto(v) -> float | None:
    t = re.sub(r"[^\d,.\-]", "", _s(v))
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"-?\d{1,3}\.\d{3}", t):
        t = t.replace(".", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def hojas_reembolso(libro: Path, patron: str = "reembolso") -> list[str]:
    return [s for s in pd.ExcelFile(libro).sheet_names if patron in P.norm(s)]


def leer_libro(libro: Path, hojas: list[str]):
    """Devuelve (rendiciones: list[Rendicion], extra: dict[id -> dict], urls: dict[id -> list[str]])."""
    resumen = pd.read_excel(libro, sheet_name="Resumen", dtype=str)
    resumen = resumen.drop_duplicates("ID").set_index("ID")
    rend, extra, urls = [], {}, {}
    for hoja in hojas:
        df = pd.read_excel(libro, sheet_name=hoja, dtype=str)
        norm_cols = {c: P.norm(re.sub(r"\.\d+$", "", c)) for c in df.columns}
        # motivos: la plantilla repite "Indicar motivo..." y "Otro motivo..." (etapa 1 y etapa 2)
        mot_cols = [c for c, n in norm_cols.items() if n.startswith("indicar motivo de aprobacion")]
        otro_cols = [c for c, n in norm_cols.items() if n.startswith("otro motivo de rechazo")]
        url_cols = [c for c, n in norm_cols.items() if any(n.startswith(p) for p in URL_PREFIJOS)]
        for _, row in df.iterrows():
            rid = _s(row.get("ID"))
            if not rid or rid in extra:
                continue
            d = {}
            for k, prefs in CAMPOS.items():
                col = next((c for c, n in norm_cols.items() if any(n.startswith(p) for p in prefs)), None)
                d[k] = _s(row[col]) if col else ""
            d["motivo_e1"] = _s(row[mot_cols[0]]) if len(mot_cols) > 0 else ""
            d["motivo_e2"] = _s(row[mot_cols[1]]) if len(mot_cols) > 1 else ""
            d["otro_e1"] = _s(row[otro_cols[0]]) if len(otro_cols) > 0 else ""
            d["otro_e2"] = _s(row[otro_cols[1]]) if len(otro_cols) > 1 else ""
            us = []
            for c in url_cols:
                v = _s(row[c])
                if v.lower().startswith("http") and v not in us:
                    us.append(v)
            rs = resumen.loc[rid] if rid in resumen.index else None
            d.update({
                "hoja": hoja, "n_adjuntos_links": len(us),
                "razon_social": _s(rs["Razón Social"]) if rs is not None else "",
                "cod_cc_cliente": _s(rs["Código Centro Costo"]) if rs is not None else "",
                "cc_cliente": _s(rs["Centro Costo"]) if rs is not None else "",
                "jefatura": _s(rs["Jefatura"]) if rs is not None else "",
                "fecha_sol": _s(rs["Fecha"]) if rs is not None else "",
                "canal": _s(rs["Canal"]) if rs is not None else hoja,
                "etapa": _s(rs["Etapa en curso"]) if rs is not None else "",
                "dias_sin_mov": _s(rs["Días que lleva sin movimiento"]) if rs is not None else "",
                "estado_resumen": _s(rs["Estado"]) if rs is not None else "",
            })
            extra[rid], urls[rid] = d, us
            try:
                f = R.parse_date(d["fecha_sol"] or d["fecha_res"])
            except Exception:  # noqa: BLE001
                f = None
            rend.append(R.Rendicion(id=rid, rendidor=d["rendidor"], ceco=d["ceco"] or "Sin CECO",
                                    concepto_declarado=d["cuenta"], fecha=f, monto_declarado=_monto(d["monto"]),
                                    adjunto=us[0] if us else None, usa_cabify="NO", estado=d["estado"]))
    return rend, extra, urls
