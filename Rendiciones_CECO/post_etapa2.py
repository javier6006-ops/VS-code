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
for fn in sorted(glob.glob("lecturas/lote_*.json")):
    for x in json.load(open(fn, encoding="utf-8")):
        lecturas[x["id"]] = x

# ---------- 1. Incorporar lecturas manuales ----------
ilegible_ids, n_items = set(), 0
for res in results:
    rid = res["rendicion"]["id"]
    if rid not in lecturas:
        continue
    lec, pags = lecturas[rid], {p["img"]: p for p in index[rid]["paginas"]}
    res["items"] = [it for it in res["items"] if it.get("metodo") != "sin_ocr"]
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
    res["estado"] = "LECTURA MANUAL" if lec.get("items") else "ILEGIBLE"
    res["errores"] = [e for e in res["errores"] if "Tesseract" not in e]

R.audit(results)
R.add_cuadratura(results)
for res in results:                      # observación de la diferencia en las revisadas a mano
    rid = res["rendicion"]["id"]
    if rid in lecturas:
        for it in res["items"]:
            if it.get("concepto") == R.SIN_ID and it.get("metodo") == "cuadratura":
                if rid in ilegible_ids:
                    it["descripcion"], it["observacion"] = "Comprobante(s) ilegible(s)", "ilegible"
                else:
                    it["descripcion"] = "Diferencia: lo leído en los comprobantes no alcanza al monto declarado"
                    it["observacion"] = "no respaldado en comprobantes visibles"

out = Path(f"Desglose_Rendiciones_{dt.datetime.now():%Y%m%d_%H%M}_revisado.xlsx")
meta = {"Archivo de entrada": XLSX.resolve(), "Hoja": "Reembolsos Consolidado", "Motor": "local + lectura manual de imágenes",
        "Filtros": "solo_finalizadas=True, CECOs operacionales (14)", "Fecha ejecución": dt.datetime.now().strftime("%d-%m-%Y %H:%M"),
        "Rendiciones procesadas": len(results), "Revisadas a mano (falta OCR)": len(lecturas),
        "Ítems leídos a mano": n_items, "Con comprobantes ilegibles": len(ilegible_ids)}
R.write_report(results, out, meta)

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
                         "Monto": float(it["monto"]), "ID": r["id"]})
df = pd.DataFrame(rows)
meses = sorted(m for m in df["Mes"].unique() if m)
nrend = {(r["rendicion"]["ceco"], r["rendicion"]["rendidor"]): 0 for r in results}
for res in results:
    nrend[(res["rendicion"]["ceco"], res["rendicion"]["rendidor"])] += 1

wb = openpyxl.load_workbook(out)
MONEY, HF, HFont = R.MONEY, R.HDR_FILL, R.HDR_FONT


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
    cg = cab[cab["pn"].isin(names)]
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
    wbd.cell(row=i, column=6).number_format = MONEY
wbd.auto_filter.ref = f"A1:G{len(df) + 1}"
R._widths(wbd, [28, 32, 9, 9, 24, 14, 11])

wb.save(out)
json.dump({"archivo": str(out), "top_ceco": {k: [v[0], list(v[1].items())] for k, v in top_ceco.items()},
           "cabify": resumen_cab}, open("resumen.json", "w"), ensure_ascii=False, indent=1, default=float)
print("OK ->", out)
