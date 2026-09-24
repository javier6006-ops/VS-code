# -*- coding: utf-8 -*-
"""Etapa 2 del postproceso: incorpora las lecturas manuales (lecturas/lote_*.json) a los resultados del RPA,
recalcula auditoría y cuadratura, genera el Desglose y agrega hojas de análisis:
  - Rendiciones CECO-Rendidor : evolutivo mensual CECO → Rendidor → Concepto (total y por concepto)
  - Top 5 Conceptos           : 5 conceptos con más gasto por CECO y por CECO-Rendidor
  - Cabify + Mov Evolutivo    : usuarios Cabify que además rinden APPS_TRANSPORTE o COMBUSTIBLE, mes a mes
  - Base Dinámica             : tabla plana para tablas dinámicas
Formato de cada lectura: {"id", "items": [{"img","emisor","fecha":"DD-MM-YYYY","monto","concepto","descripcion"}],
                          "ilegibles": ["03.jpg", ...], "nota": ""}"""
import datetime as dt
import glob
import json
import pickle
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

sys.argv = ["x"]
import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402
from openpyxl.styles import Font  # noqa: E402
from openpyxl.utils import get_column_letter as L  # noqa: E402

import rpa_rendiciones as R  # noqa: E402

XLSX = Path("Presupuestos_Rendiciones_actualizado.xlsx")
CONCEPTOS_OK = {"APPS_TRANSPORTE", "COMBUSTIBLE", "ESTACIONAMIENTO", "PEAJE_TAG", "TAXI_COLECTIVO",
                "TRANSPORTE_PUBLICO", "BUS_INTERURBANO", "PASAJE_AEREO", "ALOJAMIENTO", "ALIMENTACION",
                "UTILES_OFICINA", "NOTARIA", "COURIER", "FERRETERIA_MANTENCION", "SUPERMERCADO", "FARMACIA", "OTROS"}
MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
            "Noviembre", "Diciembre"]

results = pickle.load(open("resultados_base.pkl", "rb"))
index = {x["id"]: x for x in json.load(open("revisar/indice.json", encoding="utf-8"))}
lecturas = {}
for fn in sorted(glob.glob("lecturas/lote_*.json")):   # (los .jsonl son borradores)
    for x in json.load(open(fn, encoding="utf-8")):
        lecturas[x["id"]] = x

# ---------- 1. Incorporar lecturas manuales ----------
ilegible_ids, n_items = set(), 0
for res in results:
    rid = res["rendicion"]["id"]
    if rid not in lecturas:
        continue
    lec, pags = lecturas[rid], {p["img"]: p for p in index[rid]["paginas"]}
    leidas = {(p["archivo"], p["pagina"]) for p in pags.values()}   # páginas leídas a mano (sin monto o sólo OCR)
    res["items"] = [it for it in res["items"] if (it.get("archivo"), it.get("pagina")) not in leidas]
    for it in lec.get("items", []):
        c = it["concepto"].strip().upper()
        assert c in CONCEPTOS_OK, (rid, c)
        p = pags.get(it.get("img"), {})
        item = asdict(R.Item(tipo_documento=it.get("tipo", ""), emisor=it.get("emisor", ""),
                             fecha_doc=R.parse_date(it.get("fecha")), descripcion=it.get("descripcion", ""),
                             monto=float(it["monto"]), concepto=c, metodo="lectura_manual", confianza="media",
                             observacion=it.get("obs", "")))
        res["items"].append({**item, "archivo": p.get("archivo", ""), "tipo_archivo": p.get("tipo_archivo", ""),
                             "pagina": p.get("pagina"), "alertas": ""})
        n_items += 1
    if lec.get("ilegibles"):
        ilegible_ids.add(rid)
    res["estado"] = (res["estado"] + " + LECTURA MANUAL") if lec.get("items") else (res["estado"] + " + ILEGIBLE")

# ---------- 1b. Robustez sobre lo leído automáticamente ----------
EXTRA = [  # emisor/descripción -> concepto, para ítems automáticos que quedaron en OTROS
    ("APPS_TRANSPORTE", ["uber", "cabify", "didi", "indrive"]),
    ("COMBUSTIBLE", ["copec", "enex", "aramco", "esmax", "shell", "petrobras", "bencina", "gasolina", "diesel"]),
    ("PEAJE_TAG", ["autopista", "peaje", "costanera norte", "vespucio"]),
    ("ESTACIONAMIENTO", ["estacionamiento", "parking", "saba"]),
    ("ALOJAMIENTO", ["hotel", "hostal", "hospedaje", "cabanas", "apart", "airbnb", "booking"]),
    ("PASAJE_AEREO", ["latam", "sky airline", "jetsmart", "aerolinea"]),
    ("BUS_INTERURBANO", ["turbus", "pullman", "buses", "terminal rodoviario"]),
    ("TRANSPORTE_PUBLICO", ["metro de santiago", "bip", "efe", "movired"]),
    ("TAXI_COLECTIVO", ["taxi", "radiotaxi", "transfer"]),
    ("ALIMENTACION", ["restaurant", "restaurante", "resto", "coffee", "coffe", "cafe", "cafeteria", "sushi", "pizza",
                      "burger", "gastronom", "comida", "panaderia", "pasteleria", "dulceria", "chocolate", "almuerzo",
                      "colacion", "cena", "coffee break", "starbucks", "mcdonald", "juan maestro", "doggis"]),
    ("SUPERMERCADO", ["lider", "jumbo", "tottus", "unimarc", "santa isabel", "acuenta", "supermercado", "oxxo"]),
    ("FERRETERIA_MANTENCION", ["sodimac", "easy", "construmart", "ferreteria", "aseo", "limpieza", "epp",
                               "seguridad industrial"]),
    ("UTILES_OFICINA", ["libreria", "lapiz lopez", "imprenta", "impresion", "toner", "resma"]),
    ("COURIER", ["chilexpress", "starken", "correos de chile", "bluexpress", "blue express"]),
    ("NOTARIA", ["notaria", "notario", "conservador"]),
    ("FARMACIA", ["farmacia", "cruz verde", "salcobrand", "ahumada"]),
]
import re as _re  # noqa: E402
EXTRA_RX = [(c, kw, _re.compile(rf"(?<![a-z0-9]){_re.escape(kw)}(?![a-z0-9])")) for c, kws in EXTRA for kw in kws]
n_ocr_dudoso = n_reclas = 0
for res in results:
    declared = res["rendicion"].get("monto_declarado") or 0
    if res["rendicion"]["id"] in lecturas:
        continue
    decl_n = R.normalize(res["rendicion"].get("concepto_declarado"))
    for it in res["items"]:
        raw = f"{it.get('emisor') or ''} {it.get('descripcion') or ''}"
        if it.get("metodo") in ("ocr_tesseract", "texto_pdf_texto") and it.get("monto"):
            m_exp = _re.search(r"monto\s*\$\s*([\d\.]{3,})", raw, _re.I)
            if m_exp:                                   # "Monto $140.000" explícito en el comprobante
                v = R.parse_amount(m_exp.group(1))
                if v and v != it["monto"]:
                    it["observacion"] = f"Monto corregido al 'Monto $' explícito (OCR leía {it['monto']:,.0f})".replace(",", ".")
                    it["monto"] = v
            elif _re.search(rf"\b{int(it['monto'])}\b", raw.replace(".", "")) and _re.search(r"\d{7}\s+\w", raw) \
                    and str(int(it["monto"])).endswith("000") and len(str(int(it["monto"]))) == 7:
                it["observacion"] = "Monto OCR era un código postal: no se considera"
                if _re.search(r",\s*CL\b", raw):
                    it["concepto"], it["palabra_clave"] = "APPS_TRANSPORTE", "reclasif:recibo app (direccion, CL)"
                it["monto"], n_ocr_dudoso = None, n_ocr_dudoso + 1
                continue
        if it.get("metodo") == "planilla_reglas" and it.get("monto") and it.get("concepto") == "OTROS" \
                and "movilizacion" in decl_n:
            it["concepto"], it["palabra_clave"] = "COMBUSTIBLE", "reclasif:planilla movilizacion (km vehiculo propio)"
            n_reclas += 1
        if it.get("metodo") == "ocr_tesseract" and it.get("monto") and declared and it["monto"] > declared * 1.05:
            it["observacion"] = f"Monto OCR dudoso ({it['monto']:,.0f} > declarado): no se considera".replace(",", ".")
            it["monto"], n_ocr_dudoso = None, n_ocr_dudoso + 1
        if it.get("monto") and it.get("concepto") == "OTROS":
            t = R.normalize(f"{it.get('emisor') or ''} {it.get('descripcion') or ''}")
            for c, kw, rx in EXTRA_RX:
                if rx.search(t):
                    it["concepto"], it["palabra_clave"], n_reclas = c, f"reclasif:{kw}", n_reclas + 1
                    break

# ---------- 1c. Re-lectura de planillas donde el parser tomó la columna equivocada (ej. "Valor por Km") ----------
CACHE = XLSX.parent / f"{XLSX.stem}_rpa_cache"
MONTO_COLS = ["monto a rendir", "monto a reembolsar", "total a rendir", "monto total", "valor total", "monto", "total",
              "importe", "valor"]
NO_MONTO = ["por km", "unitario", "unit", "km recorridos", "kms", "litros", "cantidad", "rut", "fecha", "n°", "nro"]
DESC_COLS = ["motivo", "detalle", "descripcion", "concepto", "glosa", "item", "proveedor", "destino"]


def _relee_planilla(f):
    """Devuelve [(descripcion, monto, es_km, fecha)] o None si no encuentra una tabla con columna de monto."""
    out = None
    for u in R.extract(f):
        if not u.filas:
            continue
        for hi, row in enumerate(u.filas[:30]):
            hdr = [R.normalize(v) if v is not None else "" for v in row]
            mcol = next((j for key in MONTO_COLS for j, h in enumerate(hdr)
                         if key in h and not any(b in h for b in NO_MONTO)), None)
            if mcol is None:
                continue
            dcol = next((j for key in DESC_COLS for j, h in enumerate(hdr) if key in h), None)
            fcol = next((j for j, h in enumerate(hdr) if "fecha" in h), None)
            es_km = any("km" in h for h in hdr)
            rows = []
            for r in u.filas[hi + 1:]:
                if mcol >= len(r):
                    continue
                txt = " ".join(R.normalize(v) for v in r if isinstance(v, str))
                if "total" in txt and dcol is not None and not (dcol < len(r) and r[dcol]):
                    continue                                     # fila TOTAL
                v = R.parse_amount(r[mcol])
                if not v or v < 100:
                    continue
                desc = str(r[dcol]) if dcol is not None and dcol < len(r) and r[dcol] is not None else ""
                rows.append((desc, v, es_km, r[fcol] if fcol is not None and fcol < len(r) else None))
            if rows:
                out = (out or []) + rows
            break
    return out


n_planillas = 0
for res in results:
    rid, r = res["rendicion"]["id"], res["rendicion"]
    declared = r.get("monto_declarado") or 0
    if rid in lecturas or not declared:
        continue
    pl = [it for it in res["items"] if it.get("metodo") == "planilla_reglas"]
    detected = sum(it["monto"] for it in res["items"] if it.get("monto") and it.get("concepto") != R.SIN_ID)
    if not pl or detected >= 0.6 * declared:
        continue
    try:
        data = R.download(r["adjunto"], CACHE / "descargas").read_bytes()
        files = {f.ruta: f for f in R.expand(R.filename_from_url(r["adjunto"]), data)}
    except Exception:  # noqa: BLE001
        continue
    nuevos, rutas = [], set()
    for ruta in {it["archivo"] for it in pl}:
        f = files.get(ruta)
        rows = _relee_planilla(f) if f else None
        if not rows:
            continue
        s = sum(v for _, v, _, _ in rows)
        if not (0.5 * declared <= s <= 1.5 * declared):          # sólo si la re-lectura cuadra razonablemente
            continue
        rutas.add(ruta)
        clf = R.Classifier(R.CONFIG["conceptos"])
        for desc, v, es_km, fch in rows:
            c, kw = ("COMBUSTIBLE", "planilla km (vehiculo propio)") if es_km else clf.classify(desc)
            nuevos.append({**asdict(R.Item(tipo_documento="Planilla", descripcion=desc[:120], monto=v, concepto=c,
                                           palabra_clave=kw, metodo="planilla_relectura", confianza="media",
                                           fecha_doc=R.parse_date(fch))),
                           "archivo": ruta, "tipo_archivo": "xlsx", "pagina": 1, "alertas": ""})
    if rutas:
        res["items"] = [it for it in res["items"] if not (it.get("metodo") == "planilla_reglas" and it["archivo"] in rutas)]
        res["items"] += nuevos
        n_planillas += 1

R.audit(results)
R.add_cuadratura(results)
for res in results:                      # observación de la diferencia en las revisadas a mano
    rid = res["rendicion"]["id"]
    if rid in lecturas:
        for it in res["items"]:
            if it.get("concepto") == R.SIN_ID and it.get("metodo") == "cuadratura":
                if lecturas[rid].get("obs_sin"):
                    it["descripcion"] = "Diferencia no respaldada en comprobantes legibles en CLP"
                    it["observacion"] = lecturas[rid]["obs_sin"]
                elif rid in ilegible_ids:
                    it["descripcion"], it["observacion"] = "Comprobante(s) ilegible(s)", "ilegible"
                else:
                    it["descripcion"] = "Diferencia: lo leído en los comprobantes no alcanza al monto declarado"
                    it["observacion"] = "no respaldado en comprobantes visibles"

# Prorrateo: si lo leído supera lo declarado (p. ej. planilla resumen + detalle, o boletas de otra rendición),
# los montos se escalan a lo declarado para que los totales por concepto cuadren con lo pagado en Talana.
n_prorr = 0
for res in results:
    declared = res["rendicion"].get("monto_declarado") or 0
    items = [it for it in res["items"] if it.get("monto") and it.get("concepto") != R.SIN_ID]
    detected = sum(it["monto"] for it in items)
    f = declared / detected if declared and detected > declared + 1 else 1.0
    n_prorr += f < 1
    ids = {id(it) for it in items}
    for it in res["items"]:
        it["monto_bruto"], it["factor"] = it.get("monto"), f
        if f < 1 and id(it) in ids:
            it["monto"] = round(it["monto"] * f)

out = Path(f"Desglose_Rendiciones_{dt.datetime.now():%Y%m%d_%H%M}_revisado.xlsx")
meta = {"Archivo de entrada": XLSX.resolve(), "Hoja": "Reembolsos Consolidado", "Motor": "local + lectura manual de imágenes",
        "Filtros": "solo_finalizadas=True, CECOs operacionales (14)", "Fecha ejecución": dt.datetime.now().strftime("%d-%m-%Y %H:%M"),
        "Rendiciones procesadas": len(results), "Revisadas a mano (falta OCR)": len(lecturas),
        "Ítems leídos a mano": n_items, "Con comprobantes ilegibles": len(ilegible_ids),
        "Rendiciones prorrateadas (leído > declarado)": n_prorr,
        "Ítems OCR descartados (monto > declarado)": n_ocr_dudoso, "Ítems OTROS reclasificados": n_reclas,
        "Planillas re-leídas (columna de monto corregida)": n_planillas,
        "Nota montos": "Monto ítem = monto prorrateado a lo declarado; 'Monto bruto leído' = lo leído en el comprobante"}
R.write_report(results, out, meta)

def subtipo_otros(it):
    if it.get("concepto") != "OTROS":
        return ""
    t = R.normalize(f"{it.get('emisor') or ''} {it.get('descripcion') or ''}")
    for lab, kws in [("Transferencia a persona", ["transferencia", "nombre pagador", "team honor"]),
                     ("Pago de servicios / cuentas", ["servipag", "comprobante de pago", "cuenta", "aguas", "enel"]),
                     ("Arriendo", ["arriendo", "rent a car", "econorent"]),
                     ("Eventos / publicidad", ["evento", "produccion", "publicidad", "pendon", "impresos"]),
                     ("Premios / gift cards / regalos", ["gift", "premio", "regalo", "tarjeta regalo"]),
                     ("Vestuario / uniformes", ["vestuario", "corbata", "uniforme", "deportes"]),
                     ("Capacitación / salud", ["capacitacion", "curso", "universidad", "clinic", "mutual"]),
                     ("Bazar / compras sin detalle", ["bazar", "sin detalle", "mall chino", "comercial"])]:
        if any(k in t for k in kws):
            return lab
    return "Otros sin clasificar"


# ---------- 2. Base plana ----------
def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").split())


rows = []
for res in results:
    r = res["rendicion"]
    f = R.parse_date(r.get("fecha"))
    for it in res["items"]:
        if it.get("monto"):
            rows.append({"CECO": r["ceco"], "Rendidor": r["rendidor"], "Usuario Cabify": r["usa_cabify"],
                         "Mes": f.strftime("%Y-%m") if f else "", "Concepto": it.get("concepto") or "OTROS",
                         "Subtipo OTROS": subtipo_otros(it),
                         "Monto": float(it["monto"]), "ID": r["id"]})
df = pd.DataFrame(rows)
meses = sorted(m for m in df["Mes"].unique() if m)
nrend = {(r["rendicion"]["ceco"], r["rendicion"]["rendidor"]): 0 for r in results}
for res in results:
    nrend[(res["rendicion"]["ceco"], res["rendicion"]["rendidor"])] += 1

wb = openpyxl.load_workbook(out)
MONEY, HF, HFont = R.MONEY, R.HDR_FILL, R.HDR_FONT
wd = wb["Desglose x Ítem"]
for c, h in ((23, "Monto bruto leído"), (24, "Factor prorrateo"), (25, "Subtipo OTROS")):
    x = wd.cell(row=1, column=c, value=h)
    x.fill, x.font = HF, HFont
n = 1
for res in results:
    for it in res["items"]:
        n += 1
        wd.cell(row=n, column=23, value=it.get("monto_bruto")).number_format = MONEY
        wd.cell(row=n, column=24, value=it.get("factor")).number_format = "0.000"
        wd.cell(row=n, column=25, value=subtipo_otros(it))
wd.auto_filter.ref = f"A1:Y{max(n, 2)}"


def header(ws, hs, row=1):
    for c, h in enumerate(hs, 1):
        x = ws.cell(row=row, column=c, value=h)
        x.fill, x.font, x.border = HF, HFont, R.BORDER
        x.alignment = R.Alignment(horizontal="center", vertical="center", wrap_text=True)


def paint(ws, row, ncol, fill, bold=True):
    for c in range(1, ncol + 1):
        ws.cell(row=row, column=c).fill = fill
        if bold:
            ws.cell(row=row, column=c).font = Font(bold=True)


# ---------- 3. Rendiciones CECO-Rendidor (evolutivo) ----------
ws = wb.create_sheet("Rendiciones CECO-Rendidor", 1)
hs = ["CECO", "Rendidor", "Nivel", "Concepto"] + meses + ["Total", "Promedio mensual", "% del rendidor",
                                                          "% SIN_IDENTIFICAR", "N° rendiciones"]
header(ws, hs)
ws.freeze_panes = "E2"
m0, mT = 5, 5 + len(meses)          # columnas de meses y total
row = 2
ceco_rows = []
for ceco in sorted(df["CECO"].unique()):
    dc = df[df["CECO"] == ceco]
    ceco_row = row
    row += 1
    rend_rows = []
    for rend in dc.groupby("Rendidor")["Monto"].sum().sort_values(ascending=False).index:
        dr = dc[dc["Rendidor"] == rend]
        rr = row
        ws.cell(row=rr, column=1, value=ceco)
        ws.cell(row=rr, column=2, value=rend)
        ws.cell(row=rr, column=3, value="Rendidor")
        ws.cell(row=rr, column=4, value="Total mes")
        piv = dr.pivot_table(index="Concepto", columns="Mes", values="Monto", aggfunc="sum").fillna(0)
        concs = piv.sum(axis=1).sort_values(ascending=False).index
        for j, m in enumerate(meses):
            ws.cell(row=rr, column=m0 + j, value=f"=SUM({L(m0 + j)}{rr + 1}:{L(m0 + j)}{rr + len(concs)})")
        ws.cell(row=rr, column=mT + 4, value=nrend.get((ceco, rend), 0))
        paint(ws, rr, len(hs), R.WARN_FILL)
        sin_row = None
        for k, c in enumerate(concs, 1):
            x = rr + k
            ws.cell(row=x, column=1, value=ceco)
            ws.cell(row=x, column=2, value=rend)
            ws.cell(row=x, column=3, value="Concepto")
            ws.cell(row=x, column=4, value=c)
            for j, m in enumerate(meses):
                v = float(piv.loc[c, m]) if m in piv.columns else 0
                ws.cell(row=x, column=m0 + j, value=v or None)
            ws.cell(row=x, column=mT + 2, value=f"=IF({L(mT)}{rr}=0,\"\",{L(mT)}{x}/{L(mT)}{rr})")
            if c == R.SIN_ID:
                sin_row = x
                paint(ws, x, len(hs), R.PatternFill("solid", start_color="E7E6E6"), bold=False)
            ws.row_dimensions[x].outlineLevel = 2
        ws.cell(row=rr, column=mT + 3, value=f"=IF({L(mT)}{rr}=0,\"\",{L(mT)}{sin_row}/{L(mT)}{rr})" if sin_row else 0)
        ws.row_dimensions[rr].outlineLevel = 1
        rend_rows.append(rr)
        row = rr + len(concs) + 1
    ws.cell(row=ceco_row, column=1, value=ceco)
    ws.cell(row=ceco_row, column=2, value=f"TOTAL {ceco}")
    ws.cell(row=ceco_row, column=3, value="CECO")
    ws.cell(row=ceco_row, column=4, value="Total mes")
    for j in range(len(meses)):
        col = L(m0 + j)
        ws.cell(row=ceco_row, column=m0 + j, value="=" + "+".join(f"{col}{x}" for x in rend_rows))
    ws.cell(row=ceco_row, column=mT + 4, value="=" + "+".join(f"{L(mT + 4)}{x}" for x in rend_rows))
    paint(ws, ceco_row, len(hs), R.TOTAL_FILL)
    ceco_rows.append(ceco_row)
ws.cell(row=row, column=1, value="TOTAL GENERAL")
ws.cell(row=row, column=3, value="Total")
for j in range(len(meses)):
    col = L(m0 + j)
    ws.cell(row=row, column=m0 + j, value="=" + "+".join(f"{col}{x}" for x in ceco_rows))
ws.cell(row=row, column=mT + 4, value="=" + "+".join(f"{L(mT + 4)}{x}" for x in ceco_rows))
paint(ws, row, len(hs), R.TOTAL_FILL)
for x in range(2, row + 1):
    ws.cell(row=x, column=mT, value=f"=SUM({L(m0)}{x}:{L(mT - 1)}{x})")
    ws.cell(row=x, column=mT + 1, value=f"=IFERROR(AVERAGEIF({L(m0)}{x}:{L(mT - 1)}{x},\">0\"),\"\")")
    for c in range(m0, mT + 2):
        ws.cell(row=x, column=c).number_format = MONEY
    for c in (mT + 2, mT + 3):
        ws.cell(row=x, column=c).number_format = "0.0%"
ws.auto_filter.ref = f"A1:{L(len(hs))}{row}"
ws.sheet_properties.outlinePr.summaryBelow = False
R._widths(ws, [26, 30, 10, 22] + [12] * len(meses) + [14, 13, 10, 11, 10])

# ---------- 4. Top 5 conceptos ----------
wt = wb.create_sheet("Top 5 Conceptos", 2)
header(wt, ["CECO", "Rendidor", "Ranking", "Concepto", "Monto", "% del total"])
wt.freeze_panes = "C2"
row, top_ceco = 2, {}
for ceco in sorted(df["CECO"].unique()):
    dc = df[df["CECO"] == ceco]
    for nivel, sub in [("(Todo el CECO)", dc)] + [(r, dc[dc["Rendidor"] == r]) for r in
                                                  dc.groupby("Rendidor")["Monto"].sum().sort_values(ascending=False).index]:
        s = sub.groupby("Concepto")["Monto"].sum().sort_values(ascending=False)
        tot = s.sum()
        if nivel == "(Todo el CECO)":
            top_ceco[ceco] = (tot, s.head(5))
        for k, (c, v) in enumerate(s.head(5).items(), 1):
            for col, val in enumerate([ceco, nivel, k, c, v, v / tot if tot else None], 1):
                wt.cell(row=row, column=col, value=val)
            wt.cell(row=row, column=5).number_format = MONEY
            wt.cell(row=row, column=6).number_format = "0.0%"
            if nivel == "(Todo el CECO)":
                paint(wt, row, 6, R.TOTAL_FILL)
            row += 1
wt.auto_filter.ref = f"A1:F{row - 1}"
R._widths(wt, [28, 32, 8, 24, 14, 10])

# ---------- 5. Cabify + Movilización evolutivo ----------
cab = pd.read_excel(XLSX, sheet_name="Cabify Extracto")
cab = cab[cab["Estado final"] != "No encontrado"].copy()
cab["Mes"] = cab.apply(lambda x: f"{int(x['Año'])}-{MESES_ES.index(str(x['Mes']).strip().capitalize()) + 1:02d}", axis=1)
cab["pn"] = cab["Pasajero"].map(norm)
aud = pd.read_excel(XLSX, sheet_name="Auditoria Cabify vs Rend", header=1)
manual_map = {}
for _, a in aud[aud["¿Coincide en Cabify?"] == "SI"].iterrows():
    manual_map[norm(a["Nombre (origen: Talana)"])] = {norm(n) for n in str(a["Nombre (origen: Cabify)"]).split(";")}
pas = set(cab["pn"])


def cabify_names(talana):
    t = norm(talana)
    if t in manual_map:
        return manual_map[t], "Auditoría Cabify vs Rend"
    tk = t.split()
    hits = {p for p in pas if len(p.split()) >= 2 and p.split()[0] == tk[0] and p.split()[-1] in tk[1:]}
    return hits, "Nombre + apellido" if hits else ""


mov = df[df["Concepto"].isin(["APPS_TRANSPORTE", "COMBUSTIBLE"])]
wcab = wb.create_sheet("Cabify + Mov Evolutivo", 3)
hs = ["CECO", "Rendidor (Talana)", "Pasajero(s) Cabify", "Match", "Fuente"] + meses + ["Total", "Promedio mensual"]
header(wcab, hs)
wcab.freeze_panes = "F2"
row = 2
f0 = 6
resumen_cab = []
for (ceco, rend), g in mov.groupby(["CECO", "Rendidor"]):
    names, how = cabify_names(rend)
    if not names:
        continue
    cg = cab[cab["pn"].isin(names) & cab["Mes"].isin(meses)]
    if cg["Precio Fnal"].sum() <= 0:                     # sin viajes Cabify en el período: no es usuario activo
        continue
    lines = [("Cabify corporativo (viajes)", cg.groupby("Mes")["Precio Fnal"].sum()),
             ("Rendido APPS_TRANSPORTE", g[g["Concepto"] == "APPS_TRANSPORTE"].groupby("Mes")["Monto"].sum()),
             ("Rendido COMBUSTIBLE", g[g["Concepto"] == "COMBUSTIBLE"].groupby("Mes")["Monto"].sum())]
    start = row
    for fuente, s in lines:
        for c, v in enumerate([ceco, rend, "; ".join(sorted(cg["Pasajero"].unique())), how, fuente], 1):
            wcab.cell(row=row, column=c, value=v)
        for j, m in enumerate(meses):
            wcab.cell(row=row, column=f0 + j, value=float(s.get(m, 0)) or None)
        row += 1
    for c, v in enumerate([ceco, rend, "", "", "Total movilización (Cabify + rendido)"], 1):
        wcab.cell(row=row, column=c, value=v)
    for j in range(len(meses)):
        col = L(f0 + j)
        wcab.cell(row=row, column=f0 + j, value=f"=SUM({col}{start}:{col}{row - 1})")
    paint(wcab, row, len(hs), R.TOTAL_FILL)
    resumen_cab.append((ceco, rend, float(cg[cg["Mes"].isin(meses)]["Precio Fnal"].sum()),
                        float(g[g["Concepto"] == "APPS_TRANSPORTE"]["Monto"].sum()),
                        float(g[g["Concepto"] == "COMBUSTIBLE"]["Monto"].sum())))
    row += 2
tT = f0 + len(meses)
for x in range(2, row):
    if wcab.cell(row=x, column=1).value:
        wcab.cell(row=x, column=tT, value=f"=SUM({L(f0)}{x}:{L(tT - 1)}{x})")
        wcab.cell(row=x, column=tT + 1, value=f"=IFERROR(AVERAGEIF({L(f0)}{x}:{L(tT - 1)}{x},\">0\"),\"\")")
        for c in range(f0, tT + 2):
            wcab.cell(row=x, column=c).number_format = MONEY
wcab.cell(row=row + 1, column=1, value=(
    "Usuarios de Cabify corporativo (Cabify Extracto, viajes no 'No encontrado', mismos meses que las rendiciones) que además "
    "rinden APPS_TRANSPORTE o COMBUSTIBLE según lo leído en los adjuntos. Match: hoja 'Auditoria Cabify vs Rend' o "
    "primer nombre + un apellido coincidentes (validar homónimos).")).font = Font(italic=True, size=9)
R._widths(wcab, [26, 30, 30, 10, 34] + [12] * len(meses) + [14, 13])

# ---------- 6. Base dinámica ----------
wbd = wb.create_sheet("Base Dinámica")
header(wbd, list(df.columns))
for i, rr in enumerate(df.itertuples(index=False), 2):
    for c, v in enumerate(rr, 1):
        wbd.cell(row=i, column=c, value=v)
    wbd.cell(row=i, column=7).number_format = MONEY
wbd.auto_filter.ref = f"A1:H{len(df) + 1}"
R._widths(wbd, [28, 32, 9, 9, 24, 26, 14, 11])

wb.save(out)
json.dump({"archivo": str(out), "top_ceco": {k: [v[0], list(v[1].items())] for k, v in top_ceco.items()},
           "cabify": resumen_cab}, open("resumen.json", "w"), ensure_ascii=False, indent=1, default=float)
print("OK ->", out)
