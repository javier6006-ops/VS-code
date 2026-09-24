# -*- coding: utf-8 -*-
"""Etapa 1 del postproceso: reconstruye los resultados del RPA (desde la caché, sin volver a descargar),
los guarda en resultados_base.pkl y exporta a revisar/<ID>/ las imágenes de las rendiciones
cuyo SIN_IDENTIFICAR es "Imagen/escaneado sin leer (falta OCR)"."""
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

obj = [r for r in results if r["estado"] == "SIN LEER (FALTA OCR)"]
print("Rendiciones con motivo 'falta OCR':", len(obj))
index = []
for n, res in enumerate(obj, 1):
    r = res["rendicion"]
    d = OUT / r["id"]
    d.mkdir(parents=True, exist_ok=True)
    data = R.download(r["adjunto"], CACHE / "descargas").read_bytes()
    pages = []
    for f in R.expand(R.filename_from_url(r["adjunto"]), data):
        try:
            units = R.extract(f)
        except R.UnsupportedFile:
            continue
        for u in units:
            if u.imagen is None:
                continue
            img = Image.open(io.BytesIO(u.imagen))
            img.thumbnail((1500, 1500))
            name = f"{len(pages) + 1:02d}.jpg"
            img.convert("RGB").save(d / name, quality=85)
            pages.append({"img": name, "archivo": f.ruta, "tipo_archivo": f.tipo, "pagina": u.pagina})
    index.append({"lote": (n - 1) // 50 + 1, "id": r["id"], "rendidor": r["rendidor"], "ceco": r["ceco"],
                  "declarado": r["monto_declarado"], "concepto_declarado": r["concepto_declarado"],
                  "fecha": str(r["fecha"]), "paginas": pages})
json.dump(index, open(OUT / "indice.json", "w"), ensure_ascii=False, indent=1)
print("Imágenes exportadas:", sum(len(x["paginas"]) for x in index), "| lotes de 50:", (len(index) + 49) // 50)
