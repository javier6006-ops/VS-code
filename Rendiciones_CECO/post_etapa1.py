# -*- coding: utf-8 -*-
"""Etapa 1 del postproceso: reconstruye los resultados del RPA (desde la caché, sin volver a descargar),
los guarda en resultados_base.pkl y exporta a revisar/<ID>/ las páginas que NO aportaron monto
(fotos, escaneados y páginas PDF) de toda rendición que aún tenga monto SIN_IDENTIFICAR, para lectura manual."""
import copy
import io
import json
import pickle
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.argv = ["x"]
import rpa_rendiciones as R  # noqa: E402
from PIL import Image  # noqa: E402

XLSX = Path("Presupuestos_Rendiciones_actualizado.xlsx")
CACHE = XLSX.parent / f"{XLSX.stem}_rpa_cache"
OUT = Path("revisar")

rend = R.read_rendiciones(XLSX, "Reembolsos Consolidado", cecos=R.CONFIG["cecos_operacionales"], solo_finalizadas=True)
an = R.LocalAnalyzer(R.Classifier(R.CONFIG["conceptos"]))
with ThreadPoolExecutor(4) as ex:
    results = list(ex.map(lambda r: R.process_one(r, an, CACHE, False), rend))
pickle.dump(results, open("resultados_base.pkl", "wb"))

# brecha declarado - detectado, calculada igual que add_cuadratura
tmp = copy.deepcopy(results)
R.audit(tmp)
R.add_cuadratura(tmp)
gap = {t["rendicion"]["id"]: sum(it["monto"] for it in t["items"] if it.get("concepto") == R.SIN_ID)
       for t in tmp}
obj = [r for r in results if gap[r["rendicion"]["id"]] > 1 and r["rendicion"].get("adjunto")
       and r["estado"] not in ("ERROR DESCARGA",)]
print("Rendiciones con SIN_IDENTIFICAR a revisar:", len(obj))


def export(res):
    r = res["rendicion"]
    con_monto = {(it.get("archivo"), it.get("pagina")) for it in res["items"] if it.get("monto")}
    try:
        data = R.download(r["adjunto"], CACHE / "descargas").read_bytes()
        inner = R.expand(R.filename_from_url(r["adjunto"]), data)
    except Exception:  # noqa: BLE001
        return None
    d = OUT / r["id"]
    pages = []
    for f in inner:
        if f.tipo == "pdf":
            import pymupdf
            try:
                doc = pymupdf.open(stream=f.data, filetype="pdf")
            except Exception:  # noqa: BLE001
                continue
            for i, page in enumerate(doc):
                if i >= R.CONFIG["documentos"]["max_paginas_pdf"]:
                    break
                if (f.ruta, i + 1) in con_monto:
                    continue
                img = Image.open(io.BytesIO(page.get_pixmap(dpi=130).tobytes("png")))
                pages.append((img, f, i + 1))
            continue
        if f.tipo not in R.IMAGE_TYPES and f.tipo != "docx":
            continue
        try:
            units = R.extract(f)
        except R.UnsupportedFile:
            continue
        for u in units:
            if u.imagen is not None and (f.ruta, u.pagina) not in con_monto:
                pages.append((Image.open(io.BytesIO(u.imagen)), f, u.pagina))
    if not pages:
        return None
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for k, (img, f, pg) in enumerate(pages, 1):
        img.thumbnail((1500, 1500))
        name = f"{k:02d}.jpg"
        img.convert("RGB").save(d / name, quality=85)
        out.append({"img": name, "archivo": f.ruta, "tipo_archivo": f.tipo, "pagina": pg})
    detectado = sum(it["monto"] for it in res["items"] if it.get("monto"))
    return {"id": r["id"], "rendidor": r["rendidor"], "ceco": r["ceco"], "declarado": r["monto_declarado"],
            "detectado_auto": detectado, "sin_identificar": gap[r["id"]],
            "concepto_declarado": r["concepto_declarado"], "fecha": str(r["fecha"]), "paginas": out}


with ThreadPoolExecutor(4) as ex:
    index = [x for x in ex.map(export, obj) if x]
index.sort(key=lambda x: (x["ceco"], x["rendidor"], x["id"]))
for n, x in enumerate(index):
    x["lote"] = n // 50 + 1
json.dump(index, open(OUT / "indice.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("Rendiciones con páginas a leer:", len(index), "| páginas:", sum(len(x["paginas"]) for x in index),
      "| lotes de 50:", (len(index) + 49) // 50, "| monto SIN_ID:", round(sum(x["sin_identificar"] for x in index)))
