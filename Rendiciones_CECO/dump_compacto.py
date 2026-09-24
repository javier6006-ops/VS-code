# -*- coding: utf-8 -*-
"""Volcado compacto de las rendiciones con más monto sin detalle (OTROS genérico + SIN_IDENTIFICAR).
Uso: python dump_compacto.py DESDE HASTA   (posiciones en el ranking)"""
import glob
import sys

import pandas as pd

a, b = int(sys.argv[1]), int(sys.argv[2])
f = sorted(glob.glob("Desglose_Rendiciones_*_revisado.xlsx"))[-1]
d = pd.read_pickle("/tmp/claude-0/desglose.pkl") if len(sys.argv) > 3 else pd.read_excel(f, sheet_name="Desglose x Ítem")
d.to_pickle("/tmp/claude-0/desglose.pkl")
GEN = {"Comprobante con OCR poco legible (revisar)", "Otros sin clasificar", "Planilla: fila sin categoría de gasto",
       "Voucher / boleta sin detalle del producto", "Pago de cuentas / Servipag"}
bad = d[((d["Concepto detectado"] == "OTROS") & d["Subtipo OTROS"].isin(GEN)) | (d["Concepto detectado"] == "SIN_IDENTIFICAR")]
rank = bad.groupby("ID")["Monto ítem"].sum().sort_values(ascending=False)
for pos, (rid, m) in enumerate(rank.iloc[a:b].items(), a):
    x = d[d.ID == rid]
    r0 = x.iloc[0]
    ok = x[~x.index.isin(bad.index)].groupby("Concepto detectado")["Monto ítem"].sum()
    print(f"\n#{pos} {rid} {r0['Rendidor'][:24]} | {r0['CECO'][:14]} | {str(r0['Concepto declarado'])[:22]} | "
          f"decl {x['Monto ítem'].sum():,.0f} | sin detalle {m:,.0f}")
    if len(ok):
        print("   ya clasificado: " + "; ".join(f"{k} {v:,.0f}" for k, v in ok.items()))
    files = x.groupby("Archivo (ruta interna)").size()
    print("   archivos: " + "; ".join(f"{str(k)[-45:]}({v})" for k, v in list(files.items())[:6]) +
          (f" ... +{len(files) - 6}" if len(files) > 6 else ""))
    bb = x[x.index.isin(bad.index)].sort_values("Monto ítem", ascending=False).head(6)
    for _, it in bb.iterrows():
        t = " ".join(str(v) for v in (it["Emisor"], it["Descripción"]) if v == v)[:110]
        print(f"   - {it['Monto ítem']:>10,.0f} [{str(it['Método lectura'])[:6]}] {t}")
