# -*- coding: utf-8 -*-
"""Lectura automática (descarga + texto + OCR) de TODOS los adjuntos de las rendiciones del libro de requerimientos.
Reanudable: cada adjunto queda en la caché y no se vuelve a procesar."""
from __future__ import annotations

import logging
import os
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import ingesta_requerimientos as I  # noqa: E402
import rpa_rendiciones as R  # noqa: E402

log = logging.getLogger("lectura_req")


def leer_adjuntos(rend: list, urls: dict, cache: Path, pkl: Path | None = None, workers: int | None = None) -> list[dict]:
    if pkl and Path(pkl).exists():
        log.info("Resultados desde %s", pkl)
        return pickle.load(open(pkl, "rb"))
    analyzer = R.LocalAnalyzer(R.Classifier(R.CONFIG["conceptos"]))
    trabajos = []                                  # (indice rendicion, Rendicion con un solo link)
    for i, r in enumerate(rend):
        for u in (urls.get(r.id) or [None]):
            trabajos.append((i, replace(r, adjunto=u)))
    out: dict[int, list] = {}
    t0, n = time.time(), len(trabajos)
    log.info("Procesando %d adjuntos de %d rendiciones (caché: %s)", n, len(rend), cache)
    with ThreadPoolExecutor(workers or R.CONFIG["descarga"]["workers"]) as ex:
        fut = {ex.submit(R.process_one, rr, analyzer, cache, False): i for i, rr in trabajos}
        for k, f in enumerate(as_completed(fut), 1):
            try:
                out.setdefault(fut[f], []).append(f.result())
            except Exception as e:  # noqa: BLE001
                out.setdefault(fut[f], []).append({"rendicion": {}, "estado": "ERROR", "archivos": [], "items": [], "errores": [str(e)]})
            if k % 50 == 0 or k == n:
                log.info("  %d/%d adjuntos (%.0f min)", k, n, (time.time() - t0) / 60)
    resultados = []
    for i, r in enumerate(rend):
        partes = out.get(i, [])
        base = partes[0]
        for p in partes[1:]:
            base["items"] += p["items"]; base["archivos"] += p["archivos"]; base["errores"] += p["errores"]
            if base["estado"] != "OK" and p["estado"] == "OK":
                base["estado"] = "OK"
        base["rendicion"] = {**base.get("rendicion", {}), **{k: v for k, v in r.__dict__.items()}}
        resultados.append(base)
    if pkl:
        pickle.dump(resultados, open(pkl, "wb"))
    return resultados


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    libro = Path(sys.argv[1])
    cache = Path(sys.argv[2])
    pkl = Path(sys.argv[3])
    rend, extra, urls = I.leer_libro(libro, I.hojas_reembolso(libro))
    leer_adjuntos(rend, urls, cache, pkl)
    log.info("LISTO")
