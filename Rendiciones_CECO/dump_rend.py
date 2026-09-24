# -*- coding: utf-8 -*-
"""Vuelca una rendición como texto para lectura manual: planillas (todas las filas con monto), PDF con texto
(primeras líneas por página) e imágenes (conteo). Uso: python dump_rend.py CDR12345 [CDR...]"""
import pickle
import sys

ids = sys.argv[1:]
sys.argv = ["x"]
import rpa_rendiciones as R  # noqa: E402

res = {r["rendicion"]["id"]: r for r in pickle.load(open("resultados_base.pkl", "rb"))}
CACHE = R.Path("Presupuestos_Rendiciones_actualizado_rpa_cache/descargas")
for rid in ids:
    r = res[rid]
    rr = r["rendicion"]
    print(f"\n######## {rid} | {rr['rendidor']} | {rr['ceco']} | {rr['concepto_declarado']} | declarado {rr['monto_declarado']:,.0f}")
    try:
        data = R.download(rr["adjunto"], CACHE).read_bytes()
        files = R.expand(R.filename_from_url(rr["adjunto"]), data)
    except Exception as e:  # noqa: BLE001
        print("  ERROR", e)
        continue
    n_img = 0
    for f in files:
        try:
            units = R.extract(f)
        except R.UnsupportedFile as e:
            print(f"  [{f.ruta}] no soportado: {e}")
            continue
        for u in units:
            if u.filas:
                print(f"  == PLANILLA {f.ruta} (hoja {u.pagina})")
                for fila in u.filas:
                    cells = [str(v).strip()[:45] for v in fila if v is not None and str(v).strip() not in ("", "nan")]
                    if any(R.parse_amount(v) and R.parse_amount(v) >= 500 for v in fila if not isinstance(v, str)
                           or v.replace(".", "").replace(",", "").strip().isdigit()):
                        print("    | " + " | ".join(cells)[:230])
            elif u.texto and u.origen.startswith("pdf"):
                t = " ".join(u.texto.split())
                print(f"  == PDF {f.ruta} p{u.pagina}: {t[:420]}")
            elif u.imagen is not None:
                n_img += 1
    print(f"  (imágenes/escaneados: {n_img})")
