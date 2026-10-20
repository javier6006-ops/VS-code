#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONSOLIDAR RENDICIONES — un solo script que genera la base única para Power BI
================================================================================
Lee uno o varios libros "Centro de Requerimientos" (Talana), unifica todas las hojas de REEMBOLSO, elimina
duplicados por ID (gana el libro más reciente), descarga y lee TODOS los adjuntos (PDF, imágenes con OCR, planillas,
comprimidos), abre el gasto por comprobante y concepto, y entrega UNA tabla plana lista para Power BI.
No incluye Cabify. Conserva el estado de cada solicitud.

SEGUIMIENTO DE FACTURACIÓN (columnas finales, en naranjo): Estado_Facturacion (lista desplegable), Cliente_Facturado,
N_Factura, Fecha_Factura, Monto_Facturado_CLP, Obs_Facturacion. Además trae una SUGERENCIA (Facturable_Sugerido) según el
aprobador ("Refacturable") o el nombre de la carpeta del adjunto. Lo que anotes en el Excel maestro se CONSERVA cuando
vuelves a ejecutar el script (se asocia a Clave_Item, que es estable).

USO
    python consolidar_rendiciones.py                      → modo interactivo (te pregunta libros, hojas y destino)
    python consolidar_rendiciones.py --libros A.xlsx B.xlsx C.xlsx --salida C:\Base   → sin preguntas

REQUISITOS
    pip install pandas openpyxl xlsxwriter pyarrow requests pillow pytesseract pymupdf python-docx py7zr rarfile
    + Tesseract OCR con idioma español (spa) y 7-Zip/unrar para .rar/.7z   (ver rpa_rendiciones: sección de instalación)
    Opcional:  python consolidar_rendiciones.py --revisar-requisitos

SALIDAS (en la carpeta de destino)
    Base_Consolidada_Rendiciones.xlsx   MAESTRO: hoja Base (tabla tblBase) + Diccionario + Conciliacion. Aquí anotas la facturación.
    Base_Consolidada_Rendiciones.parquet / .csv   copias de solo lectura para Power BI
    historial/   respaldo del maestro anterior en cada ejecución · _trabajo/  archivos intermedios (cachés reanudables)
La primera ejecución es lenta (OCR de miles de adjuntos; es reanudable). Las siguientes reutilizan la caché de adjuntos.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import types
from pathlib import Path

_ESTE = Path(__file__).resolve()
_ORDEN = ["rpa_rendiciones", "pipeline_rendiciones", "generar_base_powerbi", "ingesta_requerimientos",
          "lectura_requerimientos", "generar_base_requerimientos"]


def _cargar_modulos():
    """Los módulos del sistema viajan DENTRO de este archivo (secciones '#|'); se registran como módulos normales.
    Se ejecuta al importar el archivo, así también funciona en los procesos hijo de multiprocessing (Windows)."""
    if "rpa_rendiciones" in sys.modules and getattr(sys.modules["rpa_rendiciones"], "__bundle__", None) == str(_ESTE):
        return
    partes, actual = {}, None
    for linea in _ESTE.read_text(encoding="utf-8").splitlines():
        if linea.startswith("# >>> MODULO "):
            actual = linea.split()[-1]
            partes[actual] = []
        elif linea.startswith("# <<< FIN "):
            actual = None
        elif actual is not None:
            partes[actual].append(linea[3:] if linea.startswith("#| ") else "")
    for nombre in _ORDEN:
        m = types.ModuleType(nombre)
        m.__file__, m.__bundle__ = str(_ESTE), str(_ESTE)
        sys.modules[nombre] = m
        exec(compile("\n".join(partes[nombre]), f"{_ESTE.name}::{nombre}", "exec"), m.__dict__)


_cargar_modulos()


# ---------------------------------------------------------------------------------------------
# ASISTENTE INTERACTIVO
# ---------------------------------------------------------------------------------------------
def _tk():
    try:
        import tkinter as tk
        from tkinter import filedialog
        r = tk.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        return tk, filedialog, r
    except Exception:  # noqa: BLE001
        return None


def _menu(titulo, opciones, multiple=False, defecto=None):
    print(f"\n{titulo}")
    for i, o in enumerate(opciones, 1):
        print(f"  {i:>2}) {o}{' *' if defecto and (i - 1) in defecto else ''}")
    pref = ",".join(str(i + 1) for i in (defecto or []))
    while True:
        r = input(f"  Elige {'(números separados por coma)' if multiple else '(un número)'} [{pref}]: ").strip() or pref
        try:
            idx = [int(x) - 1 for x in r.replace(" ", "").split(",") if x]
            if idx and all(0 <= i < len(opciones) for i in idx) and (multiple or len(idx) == 1):
                return idx
        except ValueError:
            pass
        print("  Opción no válida.")


def _si_no(preg, defecto=True):
    r = input(f"{preg} [{'S/n' if defecto else 's/N'}]: ").strip().lower()
    return defecto if not r else r in ("s", "si", "sí", "y", "yes")


def interactivo(consola=False) -> list[str]:
    import json
    prefs_f = _ESTE.with_name(".consolidar_seleccion.json")
    try:
        prefs = json.loads(prefs_f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prefs = {}
    tk = None if consola else _tk()
    print("=== Consolidar rendiciones para Power BI ===")

    # 1) libros
    modo = _menu("¿Dónde están los libros 'Centro de Requerimientos'?",
                 ["Elegir archivos Excel (uno o varios)", "Elegir una carpeta (uso todos los .xlsx que contenga)"], defecto=[0])[0]
    if modo == 0:
        if tk:
            sel = tk[1].askopenfilenames(title="Selecciona los libros (Ctrl para varios)",
                                         initialdir=prefs.get("origen") or str(Path.home()),
                                         filetypes=[("Excel", "*.xlsx *.xlsm")])
            libros = [Path(x) for x in sel]
        else:
            libros = [Path(x.strip().strip('"')) for x in input("Rutas de los libros separadas por ';': ").split(";") if x.strip()]
    else:
        c = tk[1].askdirectory(title="Carpeta con los libros", initialdir=prefs.get("origen") or str(Path.home())) if tk \
            else input("Ruta de la carpeta: ").strip().strip('"')
        libros = sorted(p for p in Path(c).glob("*.xls[xm]") if not p.name.startswith("~$")) if c else []
    libros = [p for p in libros if p.exists()]
    if not libros:
        sys.exit("No se seleccionó ningún libro.")
    print("\nLibros:\n" + "\n".join(f"  - {p}" for p in libros))

    # 2) hojas
    import pandas as pd
    P = sys.modules["pipeline_rendiciones"]
    todas = []
    for p in libros:
        for h in pd.ExcelFile(p).sheet_names:
            corta = sys.modules["ingesta_requerimientos"]._hoja_corta(h)
            if corta not in todas and corta != "Resumen":
                todas.append(corta)
    defecto = [i for i, h in enumerate(todas) if "reembolso" in P.norm(h)]
    idx = _menu("HOJAS A ANALIZAR (las marcadas * son las de reembolso; elige las que quieras transformar):",
                todas, multiple=True, defecto=defecto)
    hojas_sel = [todas[i] for i in idx]

    # 3) destino
    inicial = prefs.get("destino") or str(libros[0].parent / "salida_consolidado")
    destino = tk[1].askdirectory(title="Carpeta donde guardar la base", initialdir=inicial) if tk else \
        input(f"Carpeta de destino [{inicial}]: ").strip().strip('"') or inicial
    if not destino:
        sys.exit("No se seleccionó carpeta de destino.")
    cache_ini = prefs.get("cache") or str(Path(destino) / "_trabajo" / "cache_adjuntos")
    cache = input(f"\nCarpeta de caché de adjuntos (se reutiliza entre ejecuciones; ~GB) [{cache_ini}]: ").strip().strip('"') or cache_ini

    print("\n--- Resumen ---")
    print(f"  Libros  : {len(libros)}\n  Hojas   : {', '.join(hojas_sel)}\n  Destino : {destino}\n  Caché   : {cache}")
    if not _si_no("¿Ejecutar?", True):
        sys.exit("Cancelado.")
    prefs_f.write_text(json.dumps({"origen": str(libros[0].parent), "destino": str(destino), "cache": cache},
                                  ensure_ascii=False), encoding="utf-8")
    return ["--libros", *map(str, libros), "--salida", str(destino), "--cache", cache, "--hojas-coinciden", *hojas_sel]


def revisar_requisitos():
    import importlib
    import shutil
    ok = True
    for mod in ["pandas", "openpyxl", "xlsxwriter", "pyarrow", "requests", "PIL", "pytesseract", "pymupdf", "docx", "py7zr", "rarfile"]:
        try:
            importlib.import_module(mod)
            print(f"  [ok]  {mod}")
        except Exception:  # noqa: BLE001
            ok = False
            print(f"  [FALTA] {mod}   → pip install {'pillow' if mod == 'PIL' else 'python-docx' if mod == 'docx' else mod}")
    for exe in ("tesseract", "7z"):
        print(f"  [{'ok' if shutil.which(exe) else 'FALTA'}]  {exe} (en PATH o en las rutas de rpa_rendiciones.CONFIG)")
    print("\nTodo listo." if ok else "\nInstala lo que falta y vuelve a ejecutar.")


def main():
    ap = argparse.ArgumentParser(description="Consolida los libros de requerimientos en una base única para Power BI.",
                                 add_help=False)
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("--revisar-requisitos", action="store_true")
    ap.add_argument("--consola", action="store_true", help="no abrir ventanas de selección")
    ap.add_argument("--libros", nargs="+")
    ap.add_argument("--salida")
    ap.add_argument("--hojas-coinciden", nargs="*", help=argparse.SUPPRESS)
    a, resto = ap.parse_known_args()
    if a.help:
        print(__doc__)
        return
    if a.revisar_requisitos:
        return revisar_requisitos()
    G = sys.modules["generar_base_requerimientos"]
    if not a.libros:
        argv = interactivo(a.consola) + resto
    else:
        argv = ["--libros", *a.libros, "--salida", a.salida or "salida_consolidado", *resto]
    # carpeta de trabajo (cachés reanudables) dentro del destino, salvo que se indique otra
    salida = Path(argv[argv.index("--salida") + 1])
    trabajo = salida / "_trabajo"
    trabajo.mkdir(parents=True, exist_ok=True)
    if "--hojas-coinciden" in argv:                          # el asistente entrega nombres cortos → nombres reales por libro
        i = argv.index("--hojas-coinciden")
        cortas, argv = argv[i + 1:], argv[:i]
        I = sys.modules["ingesta_requerimientos"]
        reales = []
        for p in argv[argv.index("--libros") + 1: argv.index("--salida")]:
            import pandas as pd
            reales += [h for h in pd.ExcelFile(p).sheet_names if I._hoja_corta(h) in cortas and h not in reales]
        argv += ["--hojas", *reales]
    defaults = {"--nombre": "Base_Consolidada_Rendiciones", "--pkl": str(trabajo / "resultados_lectura.pkl"),
                "--estado-pkl": str(trabajo / "estado_procesado.pkl"),
                "--lecturas": str(_ESTE.parent / "lecturas"), "--indice": str(_ESTE.parent / "revisar" / "indice.json")}
    for k, v in defaults.items():
        if k not in argv:
            argv += [k, v]
    G.main(argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()


# =============================================================================================
# MÓDULOS INCORPORADOS (código del sistema). Cada línea va precedida por "#| ".  No es necesario editarlos
# para usar el script; si lo haces, mantén el prefijo. Orden: lectura de adjuntos → reglas → base → ingesta → ejecución.
# =============================================================================================
# >>> MODULO rpa_rendiciones
#| # -*- coding: utf-8 -*-
#| """
#| RPA RENDICIONES — Desglose por concepto de los respaldos adjuntos
#| =================================================================
#| Archivo único. Doble clic (o `python rpa_rendiciones.py`) abre una ventana para:
#|   1. Seleccionar el Excel "Presupuestos_Rendiciones_actualizado.xlsx".
#|   2. Elegir la hoja: "Detalle Adjuntos a Revisar" (32 auditados) o "Reembolsos Consolidado" (todas).
#|   3. Presionar "Iniciar": el RPA abre cada link de la columna Adjunto, descarga el archivo
#|      (PDF con texto o escaneado, fotos, Excel, Word, ZIP/RAR/7z anidados), lee los comprobantes
#|      y genera un Excel con el desglose por concepto y alertas de auditoría, junto al Excel de entrada.
#|
#| Requisitos: Python 3.10+ (en Windows, el de python.org ya incluye la ventana Tkinter).
#| Las librerías faltantes se instalan solas la primera vez (requiere internet).
#| Opcionales:
#|   - Tesseract OCR (motor Local, para fotos y escaneados): https://github.com/UB-Mannheim/tesseract/wiki
#|     (en la instalación marcar idioma "Spanish").
#|   - 7-Zip (para abrir .rar): https://www.7-zip.org
#|   - API key de Anthropic (motor Claude, lectura muy superior de fotos/manuscritos).
#|
#| Modo sin ventana:  python rpa_rendiciones.py --cli "ruta.xlsx" [--hoja ...] [--motor local|claude]
#|                    [--solo-movilizacion] [--limite N] [--reprocesar]
#| """
#| from __future__ import annotations
#|
#| # =====================================================================
#| # 0. DEPENDENCIAS (autoinstalación)
#| # =====================================================================
#| import importlib
#| import subprocess
#| import sys
#|
#| _DEPS = {  # módulo -> paquete pip
#|     "openpyxl": "openpyxl", "pandas": "pandas", "xlrd": "xlrd", "requests": "requests",
#|     "pymupdf": "pymupdf", "PIL": "pillow", "pillow_heif": "pillow-heif", "pytesseract": "pytesseract",
#|     "docx": "python-docx", "rarfile": "rarfile", "py7zr": "py7zr", "anthropic": "anthropic",
#| }
#|
#|
#| def ensure_dependencies() -> None:
#|     missing = []
#|     for mod, pkg in _DEPS.items():
#|         try:
#|             importlib.import_module(mod)
#|         except ImportError:
#|             missing.append(pkg)
#|     if missing:
#|         print(f"Instalando librerías faltantes: {', '.join(missing)} ...")
#|         subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *missing])
#|         importlib.invalidate_caches()
#|
#|
#| ensure_dependencies()
#|
#| # =====================================================================
#| # IMPORTS
#| # =====================================================================
#| import argparse  # noqa: E402
#| import base64  # noqa: E402
#| import datetime as dt  # noqa: E402
#| import gzip  # noqa: E402
#| import hashlib  # noqa: E402
#| import io  # noqa: E402
#| import json  # noqa: E402
#| import logging  # noqa: E402
#| import os  # noqa: E402
#| import queue  # noqa: E402
#| import re  # noqa: E402
#| import shutil  # noqa: E402
#| import tempfile  # noqa: E402
#| import threading  # noqa: E402
#| import time  # noqa: E402
#| import unicodedata  # noqa: E402
#| import urllib.parse  # noqa: E402
#| import zipfile  # noqa: E402
#| from collections import defaultdict  # noqa: E402
#| from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402
#| from dataclasses import asdict, dataclass, field  # noqa: E402
#| from pathlib import Path  # noqa: E402
#| from urllib.parse import unquote, urlparse  # noqa: E402
#|
#| import openpyxl  # noqa: E402
#| import requests  # noqa: E402
#| from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
#| from openpyxl.utils import get_column_letter  # noqa: E402
#| from PIL import Image, ImageOps  # noqa: E402
#|
#| try:
#|     import pillow_heif
#|     pillow_heif.register_heif_opener()
#| except ImportError:
#|     pass
#|
#| log = logging.getLogger("rpa")
#|
#| # =====================================================================
#| # 1. CONFIGURACIÓN (ajustable aquí mismo)
#| # =====================================================================
#| CONFIG = {
#|     # Estructura del Excel entregado: la hoja tiene una nota en la fila 1 y encabezados en la fila 2.
#|     # Se busca el encabezado automáticamente, así que funciona con ambas hojas.
#|     "hojas_soportadas": ["Detalle Adjuntos a Revisar", "Reembolsos Consolidado"],
#|     "hoja_auditoria_cabify": "Auditoria Cabify vs Rend",
#|     "estados_pagados": ["finalizado", "pagada"],      # filtro "Sólo pagadas/finalizadas"
#|     # Botón "CECOs operacionales" en la ventana / --cecos-operacionales en modo CLI
#|     "cecos_operacionales": ["220 - GERENCIA OPERACIONES", "221 - RECLUTAMIENTO Y SELECCIÓN", "230 - SANTIAGO",
#|                             "231 - SUPPLY CHAIN", "240 - ANTOFAGASTA", "250 - LA SERENA", "260 - VIÑA DEL MAR",
#|                             "265 - TALCA", "270 - CONCEPCIÓN", "280 - PUERTO MONTT", "290 - TRADE", "295 - RETAIL",
#|                             "298 - FACILITY", "320 - BUSINESS PROFESSIONAL"],
#|     "columnas": {  # nombres posibles de cada campo (se usa el primero que exista)
#|         "id":       ["ID"],
#|         "rendidor": ["Rendidor", "Nombre Creador"],
#|         "ceco":     ["CECO", "Indique el CECO"],
#|         "concepto": ["Concepto", "Indique cuenta contable"],
#|         "fecha":    ["Fecha"],
#|         "estado":   ["Estado", "Indique estado de la solicitud"],
#|         "monto":    ["Monto", "Indique monto a sustentar o reembolsar"],
#|         # En "Detalle Adjuntos a Revisar" el link es un hipervínculo con texto "Abrir archivo";
#|         # en "Reembolsos Consolidado" la URL viene como texto en esta columna larga.
#|         "adjunto":  ["Adjunto", "Adjuntar el archivo (en caso de necesitar subir más de un archivo, "
#|                                 "se debe comprimir la información en un archivo .RAR)"],
#|     },
#|     "claude": {"modelo": "claude-sonnet-5",           # más barato: "claude-haiku-4-5-20251001"
#|                "max_tokens": 4000, "max_chars_texto": 30000, "lado_max_imagen_px": 1568},
#|     "local": {"idioma_ocr": "spa", "dpi_pdf_escaneado": 250,
#|               "tesseract_rutas": [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
#|                                   r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
#|                                   os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")]},
#|     "descarga": {"timeout_seg": 60, "reintentos": 3, "workers": 4,
#|                  # sólo se descargan links https de estos hosts (el Excel es una entrada no confiable)
#|                  "hosts_permitidos": ["storage.googleapis.com"]},
#|     "descompresion": {"profundidad_max": 3, "max_archivos_por_adjunto": 200,
#|                       "rutas_7zip": [r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"]},
#|     "documentos": {"max_paginas_pdf": 40, "min_chars_pdf_con_texto": 40},
#|     "auditoria": {"tolerancia_diferencia_pct": 5, "dias_max_antiguedad_doc": 90,
#|                   "conceptos_alerta": ["COMBUSTIBLE", "APPS_TRANSPORTE", "TAXI_COLECTIVO"]},
#|     # Taxonomía: el ORDEN define la prioridad (primer concepto que calza gana). Palabras sin tildes.
#|     "conceptos": {
#|         "APPS_TRANSPORTE": ["cabify", "uber", "didi", "indriver", "indrive", "beat"],
#|         "COMBUSTIBLE": ["copec", "shell", "petrobras", "aramco", "enex", "terpel", "esso", "bencina", "gasolina",
#|                         "combustible", "diesel", "petroleo", "kerosene", "gas 93", "gas 95", "gas 97",
#|                         "sin plomo", "lts", "litros"],
#|         "ESTACIONAMIENTO": ["estacionamiento", "parking", "parquimetro", "saba", "central parking"],
#|         "PEAJE_TAG": ["peaje", "autopista", "costanera", "vespucio", "autopase", "tag", "ruta 68", "ruta 5",
#|                       "vias chile", "globalvia", "pase diario"],
#|         "TAXI_COLECTIVO": ["taxi", "colectivo", "radiotaxi", "transfer"],
#|         "TRANSPORTE_PUBLICO": ["metro", "bip", "red movilidad", "transantiago", "tren", "efe", "merval", "biotren"],
#|         "BUS_INTERURBANO": ["turbus", "tur bus", "pullman", "buses", "jac", "condor", "eme bus", "pasaje bus"],
#|         "PASAJE_AEREO": ["latam", "sky airline", "jetsmart", "pasaje aereo", "vuelo"],
#|         "ALOJAMIENTO": ["hotel", "hostal", "alojamiento", "airbnb", "cabana", "residencial", "booking"],
#|         "ALIMENTACION": ["restaurant", "restaurante", "almuerzo", "cena", "desayuno", "colacion", "comida", "cafe",
#|                          "cafeteria", "sandwich", "menu", "pizza", "sushi", "mcdonald", "burger", "kfc",
#|                          "starbucks", "juan maestro", "doggis", "bebida", "agua mineral"],
#|         "UTILES_OFICINA": ["libreria", "resma", "lapiz", "cuaderno", "toner", "tinta", "carpeta", "papel",
#|                            "corchetera"],
#|         "NOTARIA": ["notaria", "notario", "legalizacion", "conservador"],
#|         "COURIER": ["chilexpress", "starken", "correos de chile", "bluexpress", "courier", "envio"],
#|         "FERRETERIA_MANTENCION": ["sodimac", "easy", "ferreteria", "construmart", "herramienta"],
#|         "SUPERMERCADO": ["lider", "jumbo", "unimarc", "santa isabel", "tottus", "supermercado", "acuenta"],
#|         "FARMACIA": ["farmacia", "cruz verde", "salcobrand", "ahumada"],
#|         "OTROS": [],
#|     },
#| }
#|
#|
#| # =====================================================================
#| # 2. UTILIDADES (texto, montos CLP, fechas, RUT, folio)
#| # =====================================================================
#| def normalize(s: object) -> str:
#|     if s is None:
#|         return ""
#|     s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
#|     return re.sub(r"\s+", " ", s).strip().lower()
#|
#|
#| _AMOUNT_RE = re.compile(r"\$?\s?(\d{1,3}(?:[.,]\d{3})+(?:,\d{1,2})?|\d{3,9}(?:,\d{1,2})?)")
#|
#|
#| def parse_amount(raw: object) -> float | None:
#|     if raw is None or isinstance(raw, bool):
#|         return None
#|     if isinstance(raw, (int, float)):
#|         return float(raw)
#|     s = str(raw).strip().replace("$", "").replace(" ", "").replace("CLP", "")
#|     if not s:
#|         return None
#|     if "." in s and "," in s:
#|         s = s.replace(".", "").replace(",", ".")
#|     elif "," in s:
#|         parts = s.split(",")
#|         s = s.replace(",", "") if all(len(p) == 3 for p in parts[1:]) else s.replace(",", ".")
#|     elif "." in s and all(len(p) == 3 for p in s.split(".")[1:]):
#|         s = s.replace(".", "")
#|     try:
#|         return float(s)
#|     except ValueError:
#|         return None
#|
#|
#| def find_amounts(text: str, min_v: float = 100, max_v: float = 50_000_000) -> list[float]:
#|     out = []
#|     for m in _AMOUNT_RE.finditer(text):
#|         v = parse_amount(m.group(1))
#|         if v is not None and min_v <= v <= max_v:
#|             out.append(v)
#|     return out
#|
#|
#| _DATE_PATTERNS = [(re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"), "dmy"),
#|                   (re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"), "ymd"),
#|                   (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b"), "dmy2")]
#|
#|
#| def find_date(text: str) -> dt.date | None:
#|     for rx, kind in _DATE_PATTERNS:
#|         for m in rx.finditer(text):
#|             a, b, c = (int(x) for x in m.groups())
#|             try:
#|                 return dt.date(c, b, a) if kind == "dmy" else dt.date(a, b, c) if kind == "ymd" else dt.date(2000 + c, b, a)
#|             except ValueError:
#|                 continue
#|     return None
#|
#|
#| def parse_date(raw: object) -> dt.date | None:
#|     if raw is None or raw == "":
#|         return None
#|     if isinstance(raw, dt.datetime):
#|         return raw.date()
#|     if isinstance(raw, dt.date):
#|         return raw
#|     s = str(raw).strip()
#|     try:
#|         return dt.date.fromisoformat(s[:10])
#|     except ValueError:
#|         return find_date(s)
#|
#|
#| _RUT_RE = re.compile(r"\b(\d{1,2}\.?\d{3}\.?\d{3}\s?-\s?[\dkK])\b")
#| _FOLIO_RE = re.compile(r"(?:folio|n[°ºo]\.?|nro\.?|(?:boleta|factura)\s+electronica\s+n)\s*[:#]?\s*(\d{3,12})", re.I)
#|
#|
#| def find_rut(text: str) -> str | None:
#|     m = _RUT_RE.search(text)
#|     return m.group(1).replace(".", "").replace(" ", "").upper() if m else None
#|
#|
#| def find_folio(text: str) -> str | None:
#|     m = _FOLIO_RE.search(text) or _FOLIO_RE.search(normalize(text))   # "N°" se pierde al normalizar
#|     return m.group(1) if m else None
#|
#|
#| def first_existing(paths: list[str]) -> str | None:
#|     return next((p for p in paths if p and Path(p).exists()), None)
#|
#|
#| SETTINGS_FILE = Path(__file__).resolve().parent / "rpa_rendiciones_settings.json"
#|
#|
#| def load_settings() -> dict:
#|     if SETTINGS_FILE.exists():
#|         try:
#|             return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
#|         except (json.JSONDecodeError, OSError):
#|             pass
#|     return {}
#|
#|
#| def save_setting(key: str, value: str) -> None:
#|     data = load_settings()
#|     data[key] = value
#|     try:
#|         SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
#|     except OSError:
#|         pass
#|
#|
#| def _glob_search(exe_name: str, roots: list[str], max_dirs: int = 6000) -> str | None:
#|     """Busca exe_name bajo cada raíz, hasta 3 niveles de profundidad (evita escanear todo el disco)."""
#|     seen = 0
#|     for root in roots:
#|         base = Path(root)
#|         if not base.exists():
#|             continue
#|         for pattern in (exe_name, f"*/{exe_name}", f"*/*/{exe_name}"):
#|             for hit in base.glob(pattern):
#|                 seen += 1
#|                 if hit.is_file():
#|                     return str(hit)
#|                 if seen > max_dirs:
#|                     return None
#|     return None
#|
#|
#| def find_tesseract() -> str | None:
#|     override = load_settings().get("tesseract_cmd")
#|     if override and Path(override).exists():
#|         return override
#|     found = first_existing(CONFIG["local"]["tesseract_rutas"]) or shutil.which("tesseract")
#|     if found:
#|         return found
#|     return _glob_search("tesseract.exe", [r"C:\Program Files", r"C:\Program Files (x86)",
#|                                           os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
#|                                           os.path.expandvars(r"%USERPROFILE%")])
#|
#|
#| def find_7zip() -> str | None:
#|     override = load_settings().get("sevenzip_cmd")
#|     if override and Path(override).exists():
#|         return override
#|     found = first_existing(CONFIG["descompresion"]["rutas_7zip"]) or shutil.which("7z") or shutil.which("unrar")
#|     if found:
#|         return found
#|     return _glob_search("7z.exe", [r"C:\Program Files", r"C:\Program Files (x86)"])         or _glob_search("UnRAR.exe", [r"C:\Program Files", r"C:\Program Files (x86)",
#|                                       os.path.expandvars(r"%USERPROFILE%")])
#|
#|
#| def sha256(data: bytes) -> str:
#|     return hashlib.sha256(data).hexdigest()
#|
#|
#| # =====================================================================
#| # 3. LECTURA DEL EXCEL ENTREGADO
#| # =====================================================================
#| @dataclass
#| class Rendicion:
#|     id: str
#|     rendidor: str
#|     ceco: str
#|     concepto_declarado: str
#|     fecha: object
#|     monto_declarado: float | None
#|     adjunto: str | None
#|     usa_cabify: str = "NO"
#|     estado: str = ""
#|
#|
#| def _find_header(ws, required: list[str], max_scan: int = 10) -> tuple[int, dict[str, int]]:
#|     for r in range(1, max_scan + 1):
#|         values = [normalize(c.value) for c in ws[r]]
#|         if all(normalize(req) in values for req in required):
#|             return r, {v: i for i, v in enumerate(values) if v}
#|     raise ValueError(f"No encontré encabezado con {required} en la hoja '{ws.title}'")
#|
#|
#| def _cell_link(cell) -> str | None:
#|     if cell.hyperlink is not None and cell.hyperlink.target:
#|         return cell.hyperlink.target
#|     v = cell.value
#|     if isinstance(v, str):
#|         v = v.strip()
#|         if v.lower().startswith(("http://", "https://")) or (len(v) < 260 and Path(v).exists()):
#|             return v
#|     return None
#|
#|
#| def list_sheets(xlsx: Path) -> list[str]:
#|     wb = openpyxl.load_workbook(xlsx, read_only=True)
#|     names = wb.sheetnames
#|     wb.close()
#|     return [s for s in CONFIG["hojas_soportadas"] if s in names] or names
#|
#|
#| def list_cecos(xlsx: Path, hoja: str) -> list[str]:
#|     """CECOs distintos de la hoja (para el selector de la ventana)."""
#|     wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
#|     try:
#|         ws = wb[hoja]
#|         hr, hmap = _find_header(ws, ["ID"])
#|         col = next((hmap[normalize(c)] for c in CONFIG["columnas"]["ceco"] if normalize(c) in hmap), None)
#|         if col is None:
#|             return []
#|         return sorted({str(r[col]).strip() for r in ws.iter_rows(min_row=hr + 1, values_only=True)
#|                        if col < len(r) and r[col]})
#|     finally:
#|         wb.close()
#|
#|
#| def read_rendiciones(xlsx: Path, hoja: str, solo_movilizacion=False, rendidor=None, limite=None,
#|                      cecos: list[str] | None = None, solo_finalizadas: bool = False) -> list[Rendicion]:
#|     log.info("Leyendo '%s' (hoja '%s')...", xlsx.name, hoja)
#|     wb = openpyxl.load_workbook(xlsx, data_only=True)       # modo normal: necesario para leer hipervínculos
#|     if hoja not in wb.sheetnames:
#|         raise ValueError(f"La hoja '{hoja}' no existe. Hojas disponibles: {wb.sheetnames}")
#|     ws = wb[hoja]
#|     hr, hmap = _find_header(ws, ["ID"])
#|     idx = {}
#|     for k, cands in CONFIG["columnas"].items():
#|         idx[k] = next((hmap[normalize(c)] for c in cands if normalize(c) in hmap), None)
#|     missing = [k for k in ("id", "rendidor", "adjunto") if idx[k] is None]
#|     if missing:
#|         raise ValueError(f"En la hoja '{hoja}' faltan las columnas {missing}.")
#|
#|     # usuarios Cabify desde la hoja de auditoría (si existe)
#|     cabify = set()
#|     sh = CONFIG["hoja_auditoria_cabify"]
#|     if sh in wb.sheetnames:
#|         try:
#|             ahr, amap = _find_header(wb[sh], ["¿Coincide en Cabify?"])
#|             ncol = next((i for k, i in amap.items() if k.startswith("nombre (origen: talana)")
#|                          or k.startswith("nombre creador")), 0)
#|             fcol = amap[normalize("¿Coincide en Cabify?")]
#|             for row in wb[sh].iter_rows(min_row=ahr + 1):
#|                 if normalize(row[fcol].value) == "si" and row[ncol].value:
#|                     cabify.add(normalize(row[ncol].value))
#|         except ValueError:
#|             pass
#|
#|     filtro = normalize(rendidor) if rendidor else None
#|     cecos_n = {normalize(c) for c in cecos} if cecos else None
#|     pagados = {normalize(e) for e in CONFIG["estados_pagados"]}
#|     if solo_finalizadas and idx.get("estado") is None:
#|         log.warning("La hoja no tiene columna de estado: no se puede filtrar por pagadas/finalizadas.")
#|     out: list[Rendicion] = []
#|     for row in ws.iter_rows(min_row=hr + 1):
#|         def get(k):
#|             return row[idx[k]].value if idx[k] is not None and idx[k] < len(row) else None
#|         rid = get("id")
#|         if rid in (None, ""):
#|             continue
#|         nombre = str(get("rendidor") or "").strip()
#|         concepto = str(get("concepto") or "").strip()
#|         if solo_movilizacion and "movilizacion" not in normalize(concepto):
#|             continue
#|         if filtro and filtro not in normalize(nombre):
#|             continue
#|         ceco = str(get("ceco") or "").strip()
#|         if cecos_n is not None and normalize(ceco) not in cecos_n:
#|             continue
#|         estado = str(get("estado") or "").strip()
#|         if solo_finalizadas and idx.get("estado") is not None and normalize(estado) not in pagados:
#|             continue
#|         out.append(Rendicion(str(rid), nombre, ceco, concepto, parse_date(get("fecha")),
#|                              parse_amount(get("monto")), _cell_link(row[idx["adjunto"]]),
#|                              "SI" if normalize(nombre) in cabify else "NO", estado))
#|         if limite and len(out) >= limite:
#|             break
#|     wb.close()
#|     log.info("Rendiciones a procesar: %d (con link: %d)", len(out), sum(1 for r in out if r.adjunto))
#|     return out
#|
#|
#| # =====================================================================
#| # 4. DESCARGA + DETECCIÓN DE TIPO
#| # =====================================================================
#| _session = requests.Session()
#| _session.headers["User-Agent"] = "Mozilla/5.0 (RPA-Rendiciones)"
#|
#|
#| def filename_from_url(url: str) -> str:
#|     name = unquote(Path(urlparse(url).path).name) if url.lower().startswith("http") else Path(url).name
#|     return name or "adjunto"
#|
#|
#| def url_key(url: str) -> str:
#|     return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
#|
#|
#| def download(url: str, cache_dir: Path) -> Path:
#|     cache_dir.mkdir(parents=True, exist_ok=True)
#|     dest = cache_dir / f"{url_key(url)}__{filename_from_url(url)[:80]}"
#|     if dest.exists() and dest.stat().st_size > 0:
#|         return dest
#|     if not url.lower().startswith("http"):
#|         shutil.copy(url, dest)
#|         return dest
#|     cfg = CONFIG["descarga"]
#|     pu = urllib.parse.urlparse(url)
#|     if pu.scheme != "https" or (pu.hostname or "").lower() not in cfg["hosts_permitidos"]:
#|         raise ConnectionError(f"Link no permitido (sólo https a {cfg['hosts_permitidos']}): {url[:80]}")
#|     last = None
#|     for attempt in range(1, cfg["reintentos"] + 1):
#|         try:
#|             r = _session.get(url, timeout=cfg["timeout_seg"])
#|             if r.status_code in (403, 404):
#|                 raise FileNotFoundError(f"HTTP {r.status_code}: el archivo no existe o expiró el link")
#|             r.raise_for_status()
#|             dest.write_bytes(r.content)
#|             return dest
#|         except FileNotFoundError:
#|             raise
#|         except Exception as e:  # noqa: BLE001
#|             last = e
#|             time.sleep(2 * attempt)
#|     raise ConnectionError(f"No se pudo descargar ({cfg['reintentos']} intentos): {last}")
#|
#|
#| def sniff(data: bytes, name: str = "") -> str:
#|     """Tipo real por firma del archivo (la extensión puede mentir)."""
#|     h = data[:16]
#|     ext = Path(name).suffix.lower().lstrip(".")
#|     if h.startswith(b"%PDF"):
#|         return "pdf"
#|     if h.startswith(b"Rar!\x1a\x07"):
#|         return "rar"
#|     if h.startswith(b"7z\xbc\xaf\x27\x1c"):
#|         return "7z"
#|     if h.startswith(b"\x1f\x8b"):
#|         return "gz"
#|     if h.startswith((b"PK\x03\x04", b"PK\x05\x06")):
#|         try:
#|             names = zipfile.ZipFile(io.BytesIO(data)).namelist()
#|         except zipfile.BadZipFile:
#|             return "zip"
#|         if any(n.startswith("xl/") for n in names):
#|             return "xlsx"
#|         if any(n.startswith("word/") for n in names):
#|             return "docx"
#|         return "zip"
#|     if h.startswith(b"\xd0\xcf\x11\xe0"):
#|         return "xls" if ext in ("xls", "") else ext
#|     if h.startswith(b"\xff\xd8\xff"):
#|         return "jpg"
#|     if h.startswith(b"\x89PNG"):
#|         return "png"
#|     if h.startswith((b"GIF87a", b"GIF89a")):
#|         return "gif"
#|     if h[:4] == b"RIFF" and data[8:12] == b"WEBP":
#|         return "webp"
#|     if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1", b"ftypheif"):
#|         return "heic"
#|     if h.startswith((b"II*\x00", b"MM\x00*")):
#|         return "tiff"
#|     if h.startswith(b"BM"):
#|         return "bmp"
#|     if ext in ("csv", "txt", "tsv"):
#|         return ext
#|     if b"<html" in data[:300].lower():
#|         return "html"
#|     return ext or "desconocido"
#|
#|
#| # =====================================================================
#| # 5. DESCOMPRESIÓN RECURSIVA (zip, rar, 7z, gz)
#| # =====================================================================
#| @dataclass
#| class InnerFile:
#|     ruta: str
#|     nombre: str
#|     data: bytes
#|     tipo: str
#|
#|
#| class ArchiveError(Exception):
#|     pass
#|
#|
#| def _zip_members(data: bytes):
#|     with zipfile.ZipFile(io.BytesIO(data)) as z:
#|         for info in z.infolist():
#|             if info.is_dir():
#|                 continue
#|             if info.flag_bits & 0x1:
#|                 raise ArchiveError("ZIP protegido con contraseña")
#|             name = info.filename
#|             if not info.flag_bits & 0x800:                      # tildes en zips de Windows
#|                 try:
#|                     name = name.encode("cp437").decode("utf-8")
#|                 except (UnicodeEncodeError, UnicodeDecodeError):
#|                     name = name.encode("cp437", "replace").decode("cp850", "replace")
#|             yield name, z.read(info)
#|
#|
#| def _rar_members(data: bytes):
#|     tool = find_7zip()
#|     if tool:                                    # 7-Zip (o UnRAR) abre RAR4 y RAR5
#|         yield from _extract_with_7zip(tool, data, ".rar")
#|         return
#|     import rarfile
#|     with tempfile.NamedTemporaryFile(suffix=".rar", delete=False) as tmp:
#|         tmp.write(data)
#|         path = tmp.name
#|     try:
#|         with rarfile.RarFile(path) as rf:
#|             if rf.needs_password():
#|                 raise ArchiveError("RAR protegido con contraseña")
#|             for info in rf.infolist():
#|                 if not info.is_dir():
#|                     yield info.filename, rf.read(info)
#|     except rarfile.RarCannotExec as e:
#|         raise ArchiveError("No se puede abrir .rar: instala 7-Zip (https://www.7-zip.org)") from e
#|     except rarfile.Error as e:
#|         raise ArchiveError(f"RAR dañado: {e}") from e
#|     finally:
#|         Path(path).unlink(missing_ok=True)
#|
#|
#| def _extract_with_7zip(tool: str, data: bytes, suffix: str):
#|     with tempfile.TemporaryDirectory() as tmpdir:
#|         src = Path(tmpdir) / f"adjunto{suffix}"
#|         src.write_bytes(data)
#|         out_dir = Path(tmpdir) / "x"
#|         # -pNOPASS evita que 7-Zip se quede esperando contraseña
#|         cp = subprocess.run([tool, "x", "-y", "-pNOPASS", f"-o{out_dir}", str(src)], capture_output=True,
#|                             text=True, errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
#|         if cp.returncode != 0:
#|             msg = (cp.stderr or cp.stdout).lower()
#|             if "password" in msg or "contrase" in msg:
#|                 raise ArchiveError("Comprimido protegido con contraseña")
#|             raise ArchiveError(f"7-Zip no pudo abrir el comprimido: {(cp.stderr or cp.stdout).strip()[-160:]}")
#|         for p in out_dir.rglob("*"):
#|             if p.is_file():
#|                 yield str(p.relative_to(out_dir)), p.read_bytes()
#|
#|
#| def _7z_members(data: bytes):
#|     import py7zr
#|     with tempfile.TemporaryDirectory() as tmpdir:
#|         with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as z:
#|             if z.needs_password():
#|                 raise ArchiveError("7z protegido con contraseña")
#|             z.extractall(path=tmpdir)
#|         for p in Path(tmpdir).rglob("*"):
#|             if p.is_file():
#|                 yield str(p.relative_to(tmpdir)), p.read_bytes()
#|
#|
#| _SKIP = ("__macosx/", "thumbs.db", ".ds_store", "desktop.ini")
#|
#|
#| def expand(name: str, data: bytes, depth: int = 0, prefix: str = "") -> list[InnerFile]:
#|     lim = CONFIG["descompresion"]
#|     tipo = sniff(data, name)
#|     ruta = f"{prefix}{name}"
#|     if tipo not in ("zip", "rar", "7z", "gz"):
#|         return [InnerFile(ruta, Path(name).name, data, tipo)]
#|     if depth >= lim["profundidad_max"]:
#|         raise ArchiveError(f"Demasiados niveles de compresión en {ruta}")
#|     try:
#|         members = {"zip": _zip_members, "rar": _rar_members, "7z": _7z_members}.get(tipo)
#|         members = members(data) if members else [(Path(name).stem or "contenido", gzip.decompress(data))]
#|         out: list[InnerFile] = []
#|         for mname, mdata in members:
#|             low = mname.lower().replace("\\", "/")
#|             if any(s in low for s in _SKIP) or Path(mname).name.startswith("._") or not mdata:
#|                 continue
#|             out.extend(expand(mname, mdata, depth + 1, f"{ruta} > "))
#|             if len(out) > lim["max_archivos_por_adjunto"]:
#|                 log.warning("%s: demasiados archivos, se truncó", ruta)
#|                 break
#|         return out
#|     except zipfile.BadZipFile as e:
#|         raise ArchiveError(f"Comprimido dañado: {ruta}") from e
#|
#|
#| # =====================================================================
#| # 6. EXTRACCIÓN: archivo -> unidades (texto y/o imagen de página)
#| # =====================================================================
#| IMAGE_TYPES = {"jpg", "jpeg", "png", "gif", "webp", "heic", "tiff", "bmp"}
#| SHEET_TYPES = {"xlsx", "xlsm", "xls", "csv", "tsv"}
#|
#|
#| @dataclass
#| class Unidad:
#|     archivo: InnerFile
#|     pagina: int
#|     origen: str
#|     texto: str = ""
#|     imagen: bytes | None = None
#|     filas: list = field(default_factory=list)
#|
#|
#| class UnsupportedFile(Exception):
#|     pass
#|
#|
#| def _to_jpeg(img: Image.Image, max_side: int = 2200) -> bytes:
#|     img = ImageOps.exif_transpose(img)
#|     if img.mode not in ("RGB", "L"):
#|         img = img.convert("RGB")
#|     if max(img.size) > max_side:
#|         img.thumbnail((max_side, max_side))
#|     buf = io.BytesIO()
#|     img.save(buf, format="JPEG", quality=88)
#|     return buf.getvalue()
#|
#|
#| def extract(f: InnerFile) -> list[Unidad]:
#|     dcfg = CONFIG["documentos"]
#|     if f.tipo == "pdf":
#|         import pymupdf
#|         try:
#|             doc = pymupdf.open(stream=f.data, filetype="pdf")
#|         except Exception as e:  # noqa: BLE001
#|             raise UnsupportedFile(f"PDF dañado o ilegible: {e}") from e
#|         if doc.needs_pass:
#|             raise UnsupportedFile("PDF protegido con contraseña")
#|         out = []
#|         for i, page in enumerate(doc):
#|             if i >= dcfg["max_paginas_pdf"]:
#|                 break
#|             text = page.get_text("text") or ""
#|             if len(text.strip()) >= dcfg["min_chars_pdf_con_texto"]:
#|                 out.append(Unidad(f, i + 1, "pdf_texto", texto=text))
#|             else:                                        # escaneado -> imagen
#|                 pix = page.get_pixmap(dpi=CONFIG["local"]["dpi_pdf_escaneado"])
#|                 out.append(Unidad(f, i + 1, "pdf_escaneado", imagen=_to_jpeg(Image.open(io.BytesIO(pix.tobytes("png"))))))
#|         doc.close()
#|         return out
#|
#|     if f.tipo in IMAGE_TYPES:
#|         try:
#|             img = Image.open(io.BytesIO(f.data))
#|             frames = []
#|             for i in range(getattr(img, "n_frames", 1)):
#|                 img.seek(i)
#|                 frames.append(img.copy())
#|         except Exception as e:  # noqa: BLE001
#|             raise UnsupportedFile(f"Imagen ilegible ({f.tipo}): {e}") from e
#|         return [Unidad(f, i + 1, "imagen", imagen=_to_jpeg(fr)) for i, fr in enumerate(frames)]
#|
#|     if f.tipo in SHEET_TYPES:
#|         import pandas as pd
#|         bio = io.BytesIO(f.data)
#|         try:
#|             if f.tipo in ("csv", "tsv"):
#|                 sheets = {"csv": pd.read_csv(bio, sep="\t" if f.tipo == "tsv" else None, engine="python",
#|                                              header=None, dtype=object, encoding_errors="replace")}
#|             else:
#|                 sheets = pd.read_excel(bio, sheet_name=None, header=None, dtype=object,
#|                                        engine="xlrd" if f.tipo == "xls" else "openpyxl")
#|         except Exception as e:  # noqa: BLE001
#|             raise UnsupportedFile(f"Planilla ilegible: {e}") from e
#|         out = []
#|         if len(sheets) > 1:                             # evita doble conteo detalle + resumen
#|             detalle = {k: v for k, v in sheets.items()
#|                        if not re.search(r"resumen|total|consolidado|dashboard|grafico", normalize(k))}
#|             sheets = detalle or sheets
#|         for i, (sname, df) in enumerate(sheets.items()):
#|             df = df.dropna(how="all").dropna(axis=1, how="all")
#|             if df.empty:
#|                 continue
#|             filas = [[None if pd.isna(v) else v for v in row] for row in df.itertuples(index=False)]
#|             texto = "\n".join("\t".join("" if v is None else str(v) for v in r) for r in filas)
#|             out.append(Unidad(f, i + 1, "planilla", texto=f"[Hoja: {sname}]\n{texto}", filas=filas))
#|         return out
#|
#|     if f.tipo == "docx":
#|         import docx
#|         try:
#|             d = docx.Document(io.BytesIO(f.data))
#|         except Exception as e:  # noqa: BLE001
#|             raise UnsupportedFile(f"Word ilegible: {e}") from e
#|         parts = [p.text for p in d.paragraphs if p.text.strip()]
#|         parts += ["\t".join(c.text.strip() for c in row.cells) for t in d.tables for row in t.rows]
#|         out = [Unidad(f, 1, "word", texto="\n".join(parts))] if parts else []
#|         for j, rel in enumerate(r for r in d.part.rels.values() if "image" in r.reltype):
#|             try:
#|                 out.append(Unidad(f, j + 2, "imagen", imagen=_to_jpeg(Image.open(io.BytesIO(rel.target_part.blob)))))
#|             except Exception:  # noqa: BLE001
#|                 continue
#|         return out
#|
#|     if f.tipo == "txt":
#|         return [Unidad(f, 1, "texto", texto=f.data.decode("utf-8", "replace"))]
#|     if f.tipo == "html":
#|         raise UnsupportedFile("El link devolvió una página web (archivo borrado o requiere login)")
#|     raise UnsupportedFile(f"Tipo de archivo no soportado: {f.tipo}")
#|
#|
#| # =====================================================================
#| # 7. CLASIFICACIÓN Y ANÁLISIS
#| # =====================================================================
#| class Classifier:
#|     def __init__(self, conceptos: dict):
#|         self.conceptos = list(conceptos)
#|         self._rules = [(c, normalize(kw), re.compile(rf"(?<![a-z0-9]){re.escape(normalize(kw))}(?![a-z0-9])"))
#|                        for c, kws in conceptos.items() for kw in kws]
#|
#|     def classify(self, *texts: str) -> tuple[str, str]:
#|         t = normalize(" ".join(x for x in texts if x))
#|         for c, kw, rx in self._rules:
#|             if rx.search(t):
#|                 return c, kw
#|         return "OTROS", ""
#|
#|     def valid(self, c) -> str:
#|         c = (c or "").strip().upper()
#|         return c if c in self.conceptos else "OTROS"
#|
#|
#| @dataclass
#| class Item:
#|     tipo_documento: str = ""
#|     emisor: str = ""
#|     rut_emisor: str | None = None
#|     folio: str | None = None
#|     fecha_doc: object = None
#|     descripcion: str = ""
#|     monto: float | None = None
#|     concepto: str = "OTROS"
#|     palabra_clave: str = ""
#|     metodo: str = ""
#|     confianza: str = ""
#|     observacion: str = ""
#|
#|
#| _TOTAL_RX = re.compile(r"(total\s+a\s+pagar|monto\s+total|total\s+pagado|valor\s+total|\btotal\b)")
#|
#|
#| class LocalAnalyzer:
#|     """Texto nativo + OCR Tesseract + reglas. Gratis; nada sale del equipo."""
#|     name = "local"
#|
#|     def __init__(self, clf: Classifier):
#|         self.clf = clf
#|         self.lang = CONFIG["local"]["idioma_ocr"]
#|         self._tess = None
#|         try:
#|             import pytesseract
#|             cmd = find_tesseract()
#|             if cmd:
#|                 pytesseract.pytesseract.tesseract_cmd = cmd
#|             langs = pytesseract.get_languages(config="")
#|             if self.lang not in langs:
#|                 log.warning("Tesseract no tiene idioma español instalado; se usará inglés (menos preciso).")
#|                 self.lang = "eng"
#|             self._tess = pytesseract
#|         except Exception:  # noqa: BLE001
#|             log.warning("Tesseract NO está instalado: fotos y PDF escaneados no se podrán leer en motor Local. "
#|                         "Instálalo o usa el motor Claude.")
#|
#|     def _from_text(self, text: str, metodo: str) -> list[Item]:
#|         lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
#|         total, conf = None, "baja"
#|         for i, ln in enumerate(lines):
#|             nl = normalize(ln)
#|             if _TOTAL_RX.search(nl) and "subtotal" not in nl and "sub total" not in nl:
#|                 vals = find_amounts(ln) or (find_amounts(lines[i + 1]) if i + 1 < len(lines) else [])
#|                 if vals:
#|                     total, conf = max(vals) if total is None else max(total, max(vals)), "media"
#|         if total is None:
#|             vals = find_amounts(_RUT_RE.sub(" ", text), max_v=5_000_000)
#|             total = max(vals) if vals else None
#|         emisor = next((ln for ln in lines[:6] if re.search(r"[A-Za-z]{3,}", ln)), "")[:80]
#|         c, kw = self.clf.classify(text)
#|         return [Item("boleta/factura" if re.search(r"boleta|factura", normalize(text)) else "documento",
#|                      emisor, find_rut(text), find_folio(text), find_date(text),
#|                      (f"[{kw}] " if kw else "") + " ".join(lines[:3])[:150], total, c, kw, metodo, conf,
#|                      "" if total else "No se detectó monto")]
#|
#|     def _from_sheet(self, filas: list) -> list[Item]:
#|         hdr, cm, cd, cf = None, None, None, None
#|         for i, row in enumerate(filas[:15]):
#|             cells = [normalize(v) if isinstance(v, str) else "" for v in row]
#|             m = next((j for j, c in enumerate(cells) if c and any(h in c for h in
#|                       ("monto", "total", "valor", "importe", "precio", "costo", "$"))), None)
#|             if m is not None:
#|                 hdr, cm = i, m
#|                 cd = next((j for j, c in enumerate(cells) if c and any(h in c for h in
#|                            ("descripcion", "detalle", "concepto", "glosa", "motivo", "item", "proveedor", "gasto"))), None)
#|                 cf = next((j for j, c in enumerate(cells) if "fecha" in c), None)
#|                 break
#|         items = []
#|         for row in filas[(hdr + 1) if hdr is not None else 0:]:
#|             joined = " ".join(str(v) for v in row if isinstance(v, str) and v.strip())
#|             if re.search(r"\b(sub ?)?total(es)?\b|resumen", normalize(joined)):
#|                 if hdr is not None:
#|                     break                                  # fin de la tabla: lo de abajo suele ser resumen/firmas
#|                 continue
#|             if cm is not None and cm < len(row):
#|                 monto = parse_amount(row[cm])
#|             else:
#|                 nums = [parse_amount(v) for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)]
#|                 nums = [n for n in nums if n and 100 <= n <= 50_000_000]
#|                 monto = nums[-1] if nums else None
#|             if not monto or monto <= 0:
#|                 continue
#|             desc = str(row[cd]) if cd is not None and cd < len(row) and row[cd] else joined
#|             c, kw = self.clf.classify(joined)
#|             items.append(Item("planilla", "", None, None, parse_date(row[cf]) if cf is not None and cf < len(row) else None,
#|                               desc[:150], monto, c, kw, "planilla_reglas", "alta" if cm is not None else "media"))
#|         return items
#|
#|     def analyze(self, u: Unidad) -> list[Item]:
#|         if u.origen == "planilla":
#|             return self._from_sheet(u.filas)
#|         if u.imagen is not None:
#|             if not self._tess:
#|                 return [Item(metodo="sin_ocr", confianza="baja", observacion="Imagen no leída: falta Tesseract")]
#|             text = self._tess.image_to_string(Image.open(io.BytesIO(u.imagen)).convert("L"),
#|                                               lang=self.lang, config="--psm 6")
#|             if len(text.strip()) < 15:
#|                 return [Item(metodo="ocr_tesseract", confianza="baja", observacion="Imagen ilegible por OCR")]
#|             return self._from_text(text, "ocr_tesseract")
#|         return self._from_text(u.texto, f"texto_{u.origen}")
#|
#|
#| _SYSTEM = ("Eres un auditor de rendiciones de gastos de una empresa chilena. Recibes una página (foto, escaneo, "
#|            "PDF o planilla) que respalda una rendición. Extrae TODOS los comprobantes visibles (boletas, facturas, "
#|            "vouchers, tickets, capturas de apps, planillas de gastos). Montos en pesos chilenos: el punto separa "
#|            "miles ('$12.500' = 12500). Responde SOLO con JSON válido, sin texto adicional ni bloques ```.")
#|
#| _PROMPT = """Clasifica cada ítem en UNO de estos conceptos: {conceptos}.
#| Pistas: Copec/Shell/Aramco/Enex/litros/bencina/diésel = COMBUSTIBLE; Cabify/Uber/DiDi/inDrive = APPS_TRANSPORTE;
#| autopistas/TAG/peajes = PEAJE_TAG. Si una planilla lista gastos, cada fila es un ítem.
#| Si un comprobante sólo muestra el total, crea un único ítem con ese total. No inventes montos.
#| Formato:
#| {{"documentos": [{{"tipo_documento": "boleta|factura|voucher|ticket|captura_app|planilla|manuscrito|otro",
#|   "emisor": "comercio", "rut_emisor": "12345678-9 o null", "folio": "número o null", "fecha": "YYYY-MM-DD o null",
#|   "monto_total": 12345, "items": [{{"descripcion": "texto breve", "monto": 12345, "concepto": "CONCEPTO"}}],
#|   "legible": true, "observacion": "enmendaduras, manuscrito, fecha ilegible, etc. o vacío"}}]}}
#| Si la página no contiene comprobantes, devuelve {{"documentos": []}}."""
#|
#|
#| class ClaudeAnalyzer:
#|     """Claude visión: muy superior en fotos, escaneos, boletas térmicas y manuscritos."""
#|     name = "claude"
#|
#|     def __init__(self, clf: Classifier, api_key: str):
#|         import anthropic
#|         self.client = anthropic.Anthropic(api_key=api_key, max_retries=5)
#|         self.cfg = CONFIG["claude"]
#|         self.clf = clf
#|         self.prompt = _PROMPT.format(conceptos=", ".join(clf.conceptos))
#|
#|     def analyze(self, u: Unidad) -> list[Item]:
#|         content = []
#|         if u.imagen is not None:
#|             img = Image.open(io.BytesIO(u.imagen))
#|             img_bytes = u.imagen
#|             if max(img.size) > self.cfg["lado_max_imagen_px"]:
#|                 img_bytes = _to_jpeg(img, self.cfg["lado_max_imagen_px"])
#|             content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
#|                                                         "data": base64.b64encode(img_bytes).decode()}})
#|         if u.texto:
#|             content.append({"type": "text", "text": f"Archivo '{u.archivo.nombre}':\n{u.texto[:self.cfg['max_chars_texto']]}"})
#|         content.append({"type": "text", "text": self.prompt})
#|         resp = self.client.messages.create(model=self.cfg["modelo"], max_tokens=self.cfg["max_tokens"],
#|                                            system=_SYSTEM, messages=[{"role": "user", "content": content}])
#|         raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
#|         try:
#|             clean = re.sub(r"```(?:json)?", "", raw)
#|             data = json.loads(clean[clean.find("{"):clean.rfind("}") + 1])
#|         except (ValueError, json.JSONDecodeError):
#|             return [Item(metodo=f"claude_{u.origen}", confianza="baja", observacion=f"Respuesta no interpretable: {raw[:100]}")]
#|         items = []
#|         for d in data.get("documentos", []):
#|             base = dict(tipo_documento=d.get("tipo_documento") or "", emisor=(d.get("emisor") or "")[:80],
#|                         rut_emisor=d.get("rut_emisor"), folio=str(d["folio"]) if d.get("folio") else None,
#|                         fecha_doc=parse_date(d.get("fecha")), metodo=f"claude_{u.origen}",
#|                         confianza="alta" if d.get("legible", True) else "baja", observacion=d.get("observacion") or "")
#|             for it in d.get("items") or [{"descripcion": base["emisor"], "monto": d.get("monto_total")}]:
#|                 c = self.clf.valid(it.get("concepto"))
#|                 rc, kw = self.clf.classify(it.get("descripcion", ""), base["emisor"])
#|                 items.append(Item(**base, descripcion=(it.get("descripcion") or "")[:150],
#|                                   monto=parse_amount(it.get("monto")), concepto=rc if c == "OTROS" else c,
#|                                   palabra_clave=kw))
#|         return items or [Item(metodo=f"claude_{u.origen}", confianza="media", observacion="Página sin comprobantes")]
#|
#|
#| # =====================================================================
#| # 8. PROCESO POR RENDICIÓN (con caché)
#| # =====================================================================
#| def _json_default(o):
#|     if isinstance(o, (dt.date, dt.datetime)):
#|         return o.isoformat()
#|     raise TypeError(type(o))
#|
#|
#| _SIN_OCR = "falta Tesseract"
#|
#|
#| def _incompleto(res: dict) -> bool:
#|     """Resultados que dependen de algo no instalado: no se guardan en caché para reintentarlos."""
#|     return res.get("estado") == "ERROR COMPRIMIDO" or any(
#|         _SIN_OCR in (it.get("observacion") or "") for it in res.get("items", []))
#|
#|
#| def process_one(r: Rendicion, analyzer, cache: Path, reprocess: bool) -> dict:
#|     res = {"rendicion": asdict(r), "estado": "OK", "archivos": [], "items": [], "errores": []}
#|     if not r.adjunto:
#|         res["estado"] = "SIN ADJUNTO"
#|         return res
#|     res_file = cache / "resultados" / f"{url_key(r.adjunto)}_{analyzer.name}.json"
#|     if res_file.exists() and not reprocess:
#|         cached = json.loads(res_file.read_text(encoding="utf-8"))
#|         if not _incompleto(cached):                     # resultados de corridas sin Tesseract/7-Zip se rehacen
#|             cached["rendicion"] = asdict(r)
#|             return cached
#|
#|     try:
#|         data = download(r.adjunto, cache / "descargas").read_bytes()
#|         res["hash_adjunto"] = sha256(data)
#|         inner = expand(filename_from_url(r.adjunto), data)
#|     except (FileNotFoundError, ConnectionError) as e:
#|         res["estado"], res["errores"] = "ERROR DESCARGA", [str(e)]
#|         return res                                          # no se guarda en caché: se reintenta
#|     except ArchiveError as e:
#|         res["estado"], res["errores"], inner = "ERROR COMPRIMIDO", [str(e)], []
#|
#|     for f in inner:
#|         finfo = {"ruta": f.ruta, "tipo": f.tipo, "hash": sha256(f.data)}
#|         try:
#|             for u in extract(f):
#|                 try:
#|                     for it in analyzer.analyze(u):
#|                         res["items"].append({**asdict(it), "archivo": f.ruta, "tipo_archivo": f.tipo, "pagina": u.pagina})
#|                 except Exception as e:  # noqa: BLE001
#|                     res["errores"].append(f"{f.ruta} p.{u.pagina}: {e}")
#|                     res["items"].append({**asdict(Item(metodo=analyzer.name, confianza="baja",
#|                                                        observacion=f"Error al analizar: {str(e)[:150]}")),
#|                                          "archivo": f.ruta, "tipo_archivo": f.tipo, "pagina": u.pagina})
#|         except UnsupportedFile as e:
#|             res["errores"].append(f"{f.ruta}: {e}")
#|         res["archivos"].append(finfo)
#|
#|     sin_ocr = sum(1 for it in res["items"] if _SIN_OCR in (it.get("observacion") or ""))
#|     if sin_ocr:
#|         res["errores"].append(f"{sin_ocr} imagen(es)/escaneado(s) sin leer: falta instalar Tesseract")
#|     if res["estado"] == "OK":
#|         if not res["items"]:
#|             res["estado"] = "SIN CONTENIDO LEGIBLE"
#|         elif sin_ocr and not any(it.get("monto") for it in res["items"]):
#|             res["estado"] = "SIN LEER (FALTA OCR)"
#|         elif res["errores"]:
#|             res["estado"] = "PARCIAL"
#|     if not _incompleto(res):
#|         res_file.parent.mkdir(parents=True, exist_ok=True)
#|         res_file.write_text(json.dumps(res, ensure_ascii=False, default=_json_default), encoding="utf-8")
#|     return res
#|
#|
#| def _safe(name: str, maxlen: int = 60) -> str:
#|     name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
#|     return (name or "sin_nombre")[:maxlen]
#|
#|
#| def download_only(rendiciones: list[Rendicion], dest_root: Path, cache: Path,
#|                   progress=None, stop_event: threading.Event | None = None) -> Path:
#|     """Descarga, descomprime y ordena los adjuntos en carpetas CECO/Rendidor/, con un índice CSV.
#|     Pensado para revisarlos luego con Claude Cowork (o cualquier herramienta) sin depender de Tesseract/API."""
#|     import csv
#|     dest_root.mkdir(parents=True, exist_ok=True)
#|     index_rows, total = [], len(rendiciones)
#|
#|     def one(r: Rendicion):
#|         rows = []
#|         if not r.adjunto:
#|             return rows, "SIN ADJUNTO"
#|         try:
#|             data = download(r.adjunto, cache / "descargas").read_bytes()
#|             inner = expand(filename_from_url(r.adjunto), data)
#|         except (FileNotFoundError, ConnectionError) as e:
#|             return rows, f"ERROR DESCARGA: {e}"
#|         except ArchiveError as e:
#|             return rows, f"ERROR COMPRIMIDO: {e}"
#|         folder = dest_root / _safe(r.ceco) / _safe(r.rendidor)
#|         folder.mkdir(parents=True, exist_ok=True)
#|         for k, f in enumerate(inner, 1):
#|             ruta_interna = f.ruta.split(" > ", 1)[1] if " > " in f.ruta else f.nombre
#|             fname = f"{r.id}__{k:02d}__{_safe(Path(ruta_interna).stem, 50)}{Path(f.nombre).suffix or '.' + f.tipo}"
#|             (folder / fname).write_bytes(f.data)
#|             rows.append([r.id, r.ceco, r.rendidor, r.concepto_declarado, r.fecha, r.monto_declarado, r.estado,
#|                          str((folder / fname).relative_to(dest_root)), f.tipo, f.ruta])
#|         return rows, "OK"
#|
#|     done = 0
#|     with ThreadPoolExecutor(max_workers=CONFIG["descarga"]["workers"]) as ex:
#|         futs = {ex.submit(one, r): r for r in rendiciones}
#|         for fut in as_completed(futs):
#|             r = futs[fut]
#|             try:
#|                 rows, st = fut.result()
#|             except Exception as e:  # noqa: BLE001
#|                 rows, st = [], f"ERROR: {e}"
#|             if not rows:
#|                 index_rows.append([r.id, r.ceco, r.rendidor, r.concepto_declarado, r.fecha, r.monto_declarado,
#|                                    r.estado, "", "", st])
#|             index_rows.extend(rows)
#|             done += 1
#|             log.info("[%d/%d] %s | %s | %s | %d archivo(s)", done, total, r.id, r.rendidor[:25], st, len(rows))
#|             if progress:
#|                 progress(done, total)
#|             if stop_event is not None and stop_event.is_set():
#|                 log.warning("Detenido por el usuario.")
#|                 for f in futs:
#|                     f.cancel()
#|                 break
#|
#|     idx_path = dest_root / "indice_adjuntos.csv"
#|     with open(idx_path, "w", newline="", encoding="utf-8-sig") as fh:
#|         w = csv.writer(fh, delimiter=";")
#|         w.writerow(["ID", "CECO", "Rendidor", "Cuenta contable", "Fecha rendicion", "Monto declarado", "Estado",
#|                     "Archivo (relativo)", "Tipo", "Origen / estado"])
#|         w.writerows(index_rows)
#|     log.info("Adjuntos ordenados en: %s", dest_root)
#|     log.info("Índice: %s", idx_path)
#|     return dest_root
#|
#|
#| # =====================================================================
#| # 9. AUDITORÍA
#| # =====================================================================
#| def audit(results: list[dict]) -> None:
#|     acfg = CONFIG["auditoria"]
#|     alert_c, tol, max_days = set(acfg["conceptos_alerta"]), acfg["tolerancia_diferencia_pct"] / 100, acfg["dias_max_antiguedad_doc"]
#|     by_adj, by_file, by_folio, by_trip = (defaultdict(set) for _ in range(4))
#|     trip = lambda it: (normalize(it["emisor"])[:25], str(it["fecha_doc"]), it["monto"])  # noqa: E731
#|     for res in results:
#|         rid = res["rendicion"]["id"]
#|         if res.get("hash_adjunto"):
#|             by_adj[res["hash_adjunto"]].add(rid)
#|         for f in res["archivos"]:
#|             by_file[f["hash"]].add(rid)
#|         for it in res["items"]:
#|             if it.get("rut_emisor") and it.get("folio"):
#|                 by_folio[(it["rut_emisor"], str(it["folio"]))].add(rid)
#|             elif it.get("monto") and it.get("fecha_doc") and it.get("emisor"):
#|                 by_trip[trip(it)].add(rid)
#|
#|     for res in results:
#|         r, rid = res["rendicion"], res["rendicion"]["id"]
#|         f_rend, decl = parse_date(r.get("fecha")), normalize(r.get("concepto_declarado"))
#|         for it in res["items"]:
#|             a, c = [], it.get("concepto") or "OTROS"
#|             if c in alert_c:
#|                 a.append(f"ALERTA {c}")
#|                 if c == "APPS_TRANSPORTE" and r.get("usa_cabify") == "SI":
#|                     a.append("Rinde app de transporte y además es usuario Cabify corporativo")
#|             if c == "COMBUSTIBLE" and "movilizacion" in decl:
#|                 a.append("Combustible rendido como Movilización")
#|             fd = parse_date(it.get("fecha_doc"))
#|             if fd and f_rend:
#|                 days = (f_rend - fd).days
#|                 if days > max_days:
#|                     a.append(f"Documento antiguo ({days} días antes de la rendición)")
#|                 elif days < -1:
#|                     a.append("Fecha del documento posterior a la rendición")
#|             if it.get("rut_emisor") and it.get("folio"):
#|                 ids = by_folio[(it["rut_emisor"], str(it["folio"]))] - {rid}
#|                 if ids:
#|                     a.append(f"Comprobante (RUT+folio) repetido en ID {', '.join(sorted(ids))}")
#|             elif it.get("monto") and it.get("fecha_doc") and it.get("emisor"):
#|                 ids = by_trip[trip(it)] - {rid}
#|                 if ids:
#|                     a.append(f"Posible duplicado (emisor+fecha+monto) en ID {', '.join(sorted(ids))}")
#|             it["alertas"] = "; ".join(a)
#|
#|         r_alerts = []
#|         if res.get("hash_adjunto") and (ids := by_adj[res["hash_adjunto"]] - {rid}):
#|             r_alerts.append(f"Mismo adjunto usado en ID {', '.join(sorted(ids))}")
#|         dup = set().union(*[by_file[f["hash"]] - {rid} for f in res["archivos"]]) if res["archivos"] else set()
#|         if dup and not r_alerts:
#|             r_alerts.append(f"Archivo interno repetido en ID {', '.join(sorted(dup))}")
#|         detected = sum(it["monto"] for it in res["items"] if it.get("monto"))
#|         res["monto_detectado"] = detected or None
#|         declared = r.get("monto_declarado")
#|         if declared and detected and detected > 2 * declared:
#|             r_alerts.append("Monto detectado > 2x lo declarado: revisar (posible doble conteo en planilla)")
#|         elif declared and detected and abs(detected - declared) > tol * declared:
#|             r_alerts.append(f"Diferencia de monto {detected - declared:+,.0f}".replace(",", "."))
#|         elif declared and not detected and res["estado"] in ("OK", "PARCIAL"):
#|             r_alerts.append("No se pudo detectar monto en los respaldos")
#|         res["conceptos_detectados"] = ", ".join(sorted({it["concepto"] for it in res["items"] if it.get("monto")}))
#|         item_alerts = sorted({x for it in res["items"] for x in (it.get("alertas") or "").split("; ") if x})
#|         res["alertas"] = "; ".join(r_alerts + item_alerts)
#|
#|
#| SIN_ID = "SIN_IDENTIFICAR"
#|
#|
#| def add_cuadratura(results: list[dict]) -> None:
#|     """Agrega un ítem SIN_IDENTIFICAR por la diferencia declarado - detectado (si es positiva),
#|     para que los totales por concepto cuadren con lo rendido en Talana."""
#|     for res in results:
#|         declared = res["rendicion"].get("monto_declarado") or 0
#|         detected = sum(it["monto"] for it in res["items"] if it.get("monto") and it.get("concepto") != SIN_ID)
#|         gap = declared - detected
#|         if gap > 1:
#|             motivo = {"SIN ADJUNTO": "Rendición sin adjunto",
#|                       "ERROR DESCARGA": "Adjunto no descargable",
#|                       "ERROR COMPRIMIDO": "Comprimido no se pudo abrir",
#|                       "SIN LEER (FALTA OCR)": "Imagen/escaneado sin leer (falta OCR)"}.get(
#|                 res["estado"], "Parte del monto declarado no se identificó en los respaldos")
#|             res["items"].append({**asdict(Item(descripcion=motivo, monto=gap, concepto=SIN_ID,
#|                                                metodo="cuadratura", confianza="")),
#|                                  "archivo": "", "tipo_archivo": "", "pagina": None, "alertas": ""})
#|
#|
#| # =====================================================================
#| # 10. REPORTE EXCEL
#| # =====================================================================
#| MONEY = '_ * #,##0_ ;_ * \\-#,##0_ ;_ * "-"_ ;_ @_ '
#| HDR_FILL, HDR_FONT = PatternFill("solid", start_color="1F4E78"), Font(bold=True, color="FFFFFF")
#| ALERT_FILL, WARN_FILL = PatternFill("solid", start_color="FFC7CE"), PatternFill("solid", start_color="FFF2CC")
#| TOTAL_FILL = PatternFill("solid", start_color="D9E1F2")
#| BORDER = Border(*(Side(style="thin", color="BFBFBF"),) * 4)
#|
#|
#| def _header(ws, headers):
#|     for c, h in enumerate(headers, 1):
#|         cell = ws.cell(row=1, column=c, value=h)
#|         cell.font, cell.fill, cell.border = HDR_FONT, HDR_FILL, BORDER
#|         cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
#|     ws.row_dimensions[1].height = 32
#|     ws.freeze_panes = "C2"
#|
#|
#| def _widths(ws, widths):
#|     for i, w in enumerate(widths, 1):
#|         ws.column_dimensions[get_column_letter(i)].width = w
#|
#|
#| def _total_row(ws, row, c_from, c_to, label_col=1):
#|     ws.cell(row=row, column=label_col, value="TOTAL")
#|     for j in range(c_from, c_to + 1):
#|         col = get_column_letter(j)
#|         ws.cell(row=row, column=j, value=f"=SUM({col}2:{col}{row - 1})").number_format = MONEY
#|     for j in range(1, c_to + 1):
#|         ws.cell(row=row, column=j).fill, ws.cell(row=row, column=j).font = TOTAL_FILL, Font(bold=True)
#|
#|
#| def write_report(results: list[dict], out_path: Path, meta: dict) -> Path:
#|     wb = openpyxl.Workbook()
#|
#|     ws = wb.active
#|     ws.title = "Resumen x Rendición"
#|     h = ["ID", "Rendidor", "Usuario Cabify", "CECO", "Concepto declarado", "Fecha rendición", "Monto declarado",
#|          "Monto detectado", "Diferencia", "N° archivos", "N° ítems", "Conceptos detectados", "Estado", "Alertas", "Adjunto"]
#|     _header(ws, h)
#|     for i, res in enumerate(results, 2):
#|         r = res["rendicion"]
#|         vals = [r["id"], r["rendidor"], r["usa_cabify"], r["ceco"], r["concepto_declarado"], parse_date(r.get("fecha")),
#|                 r.get("monto_declarado"), res.get("monto_detectado"),
#|                 f'=IF(AND(ISNUMBER(G{i}),ISNUMBER(H{i})),H{i}-G{i},"")', len(res["archivos"]), len(res["items"]),
#|                 res.get("conceptos_detectados", ""), res["estado"], res.get("alertas", ""), "Abrir" if r.get("adjunto") else ""]
#|         for c, v in enumerate(vals, 1):
#|             ws.cell(row=i, column=c, value=v)
#|         ws.cell(row=i, column=6).number_format = "DD-MM-YYYY"
#|         for c in (7, 8, 9):
#|             ws.cell(row=i, column=c).number_format = MONEY
#|         if r.get("adjunto"):
#|             ws.cell(row=i, column=15).hyperlink = r["adjunto"]
#|             ws.cell(row=i, column=15).font = Font(color="0563C1", underline="single")
#|         fill = ALERT_FILL if res.get("alertas") else (WARN_FILL if res["estado"] != "OK" else None)
#|         if fill:
#|             for c in range(1, len(h) + 1):
#|                 ws.cell(row=i, column=c).fill = fill
#|     _widths(ws, [11, 28, 9, 26, 24, 12, 13, 13, 13, 8, 8, 30, 18, 60, 9])
#|     ws.auto_filter.ref = f"A1:O{max(2, len(results) + 1)}"
#|
#|     wd = wb.create_sheet("Desglose x Ítem")
#|     hd = ["ID", "Rendidor", "Usuario Cabify", "CECO", "Concepto declarado", "Mes rendición", "Archivo (ruta interna)",
#|           "Tipo archivo", "Página", "Método lectura", "Tipo documento", "Emisor", "RUT emisor", "Folio",
#|           "Fecha documento", "Descripción", "Monto ítem", "Concepto detectado", "Palabra clave", "Confianza",
#|           "Alertas", "Observación"]
#|     _header(wd, hd)
#|     n = 1
#|     for res in results:
#|         r = res["rendicion"]
#|         fr = parse_date(r.get("fecha"))
#|         for it in res["items"]:
#|             n += 1
#|             vals = [r["id"], r["rendidor"], r["usa_cabify"], r["ceco"], r["concepto_declarado"],
#|                     fr.strftime("%Y-%m") if fr else "", it.get("archivo"), it.get("tipo_archivo"), it.get("pagina"),
#|                     it.get("metodo"), it.get("tipo_documento"), it.get("emisor"), it.get("rut_emisor"), it.get("folio"),
#|                     parse_date(it.get("fecha_doc")), it.get("descripcion"), it.get("monto"), it.get("concepto"),
#|                     it.get("palabra_clave"), it.get("confianza"), it.get("alertas"), it.get("observacion")]
#|             for c, v in enumerate(vals, 1):
#|                 wd.cell(row=n, column=c, value=v)
#|             wd.cell(row=n, column=15).number_format = "DD-MM-YYYY"
#|             wd.cell(row=n, column=17).number_format = MONEY
#|             if it.get("alertas"):
#|                 for c in range(1, len(hd) + 1):
#|                     wd.cell(row=n, column=c).fill = ALERT_FILL
#|     last = max(n, 2)
#|     _widths(wd, [11, 26, 9, 24, 22, 10, 40, 8, 7, 18, 14, 26, 12, 10, 12, 40, 12, 20, 14, 9, 50, 35])
#|     wd.auto_filter.ref = f"A1:V{last}"
#|
#|     D = "'Desglose x Ítem'"
#|     R_REND, R_CONC, R_MONTO, R_MES = (f"{D}!${c}$2:${c}${last}" for c in ("B", "R", "Q", "F"))
#|     used = [c for c in CONFIG["conceptos"] if any(it.get("concepto") == c for res in results for it in res["items"])] \
#|         or list(CONFIG["conceptos"])
#|     if any(it.get("concepto") == SIN_ID for res in results for it in res["items"]):
#|         used.append(SIN_ID)
#|     GREY_FILL = PatternFill("solid", start_color="E7E6E6")
#|
#|     wp = wb.create_sheet("Rendidor x Concepto")
#|     _header(wp, ["Rendidor", "Usuario Cabify"] + used + ["Total detectado"])
#|     people = sorted({(res["rendicion"]["rendidor"], res["rendicion"]["usa_cabify"]) for res in results})
#|     tc = len(used) + 3
#|     for i, (name, cab) in enumerate(people, 2):
#|         wp.cell(row=i, column=1, value=name)
#|         wp.cell(row=i, column=2, value=cab)
#|         for j in range(3, tc):
#|             col = get_column_letter(j)
#|             wp.cell(row=i, column=j, value=f"=SUMIFS({R_MONTO},{R_REND},$A{i},{R_CONC},{col}$1)").number_format = MONEY
#|         wp.cell(row=i, column=tc, value=f"=SUM(C{i}:{get_column_letter(tc - 1)}{i})").number_format = MONEY
#|     _total_row(wp, len(people) + 2, 3, tc)
#|     for j, c in enumerate(used, 3):
#|         if c in CONFIG["auditoria"]["conceptos_alerta"]:
#|             for rr in range(2, len(people) + 2):
#|                 wp.cell(row=rr, column=j).font = Font(bold=True, color="C00000")
#|     _widths(wp, [28, 9] + [14] * (len(used) + 1))
#|
#|     # CECO x Rendidor x Concepto detectado (mes a mes, fórmulas sobre Desglose)
#|     wc = wb.create_sheet("CECO x Rendidor x Concepto")
#|     R_CECO = f"{D}!$D$2:$D${last}"
#|     meses_c = sorted({parse_date(res["rendicion"].get("fecha")).strftime("%Y-%m")
#|                       for res in results if parse_date(res["rendicion"].get("fecha"))})
#|     hc = ["CECO", "Rendidor", "Concepto detectado (adjuntos)"] + meses_c + ["Total", "% del rendidor"]
#|     _header(wc, hc)
#|     wc.freeze_panes = "D2"
#|     combos_c = defaultdict(float)
#|     for res in results:
#|         rr_ = res["rendicion"]
#|         for it in res["items"]:
#|             if it.get("monto"):
#|                 combos_c[(rr_["ceco"], rr_["rendidor"], it.get("concepto") or "OTROS")] += it["monto"]
#|     tcc = len(meses_c) + 4
#|     row_c, ceco_tot_rows = 2, []
#|     for ceco in sorted({k[0] for k in combos_c}):
#|         ceco_start, rend_tot_rows = row_c, []
#|         for rend in sorted({k[1] for k in combos_c if k[0] == ceco}):
#|             start = row_c
#|             for conc in sorted({k[2] for k in combos_c if k[0] == ceco and k[1] == rend},
#|                                key=lambda c: -combos_c[(ceco, rend, c)]):
#|                 wc.cell(row=row_c, column=1, value=ceco)
#|                 wc.cell(row=row_c, column=2, value=rend)
#|                 wc.cell(row=row_c, column=3, value=conc)
#|                 for j, m in enumerate(meses_c, 4):
#|                     wc.cell(row=row_c, column=j, value=(
#|                         f"=SUMIFS({R_MONTO},{R_CECO},$A{row_c},{R_REND},$B{row_c},{R_CONC},$C{row_c},{R_MES},\"{m}\")"
#|                     )).number_format = MONEY
#|                 if conc in CONFIG["auditoria"]["conceptos_alerta"] or conc == SIN_ID:
#|                     for c in range(1, tcc + 2):
#|                         wc.cell(row=row_c, column=c).fill = ALERT_FILL if conc != SIN_ID else GREY_FILL
#|                 row_c += 1
#|             end = row_c - 1
#|             wc.cell(row=row_c, column=1, value=ceco)
#|             wc.cell(row=row_c, column=2, value=rend)
#|             wc.cell(row=row_c, column=3, value="Total rendidor")
#|             for j in range(4, tcc):
#|                 wc.cell(row=row_c, column=j, value=f"=SUM({get_column_letter(j)}{start}:{get_column_letter(j)}{end})").number_format = MONEY
#|             for x in range(start, end + 1):
#|                 wc.cell(row=x, column=tcc + 1,
#|                         value=f"=IF($"+get_column_letter(tcc)+f"${row_c}=0,\"\",{get_column_letter(tcc)}{x}/$"
#|                               + get_column_letter(tcc) + f"${row_c})").number_format = "0.0%"
#|             for c in range(1, tcc + 2):
#|                 wc.cell(row=row_c, column=c).fill, wc.cell(row=row_c, column=c).font = WARN_FILL, Font(bold=True)
#|             rend_tot_rows.append(row_c)
#|             row_c += 1
#|         wc.cell(row=row_c, column=1, value=ceco)
#|         wc.cell(row=row_c, column=2, value=f"TOTAL {ceco}")
#|         for j in range(4, tcc):
#|             col = get_column_letter(j)
#|             wc.cell(row=row_c, column=j, value="=" + "+".join(f"{col}{x}" for x in rend_tot_rows)).number_format = MONEY
#|         for c in range(1, tcc + 2):
#|             wc.cell(row=row_c, column=c).fill, wc.cell(row=row_c, column=c).font = TOTAL_FILL, Font(bold=True)
#|         ceco_tot_rows.append(row_c)
#|         row_c += 1
#|     if ceco_tot_rows:
#|         wc.cell(row=row_c, column=1, value="TOTAL GENERAL")
#|         for j in range(4, tcc):
#|             col = get_column_letter(j)
#|             wc.cell(row=row_c, column=j, value="=" + "+".join(f"{col}{x}" for x in ceco_tot_rows)).number_format = MONEY
#|         for c in range(1, tcc + 2):
#|             wc.cell(row=row_c, column=c).fill, wc.cell(row=row_c, column=c).font = TOTAL_FILL, Font(bold=True)
#|     for x in range(2, row_c + 1):
#|         wc.cell(row=x, column=tcc, value=f"=SUM(D{x}:{get_column_letter(tcc - 1)}{x})").number_format = MONEY
#|     _widths(wc, [28, 28, 26] + [12] * len(meses_c) + [14, 10])
#|
#|     # Cuenta declarada en Talana vs concepto real leído en los adjuntos
#|     wv = wb.create_sheet("Declarado vs Detectado")
#|     R_DECL = f"{D}!$E$2:$E${last}"
#|     decls = sorted({res["rendicion"]["concepto_declarado"] for res in results if res["rendicion"]["concepto_declarado"]})
#|     _header(wv, ["Cuenta declarada (Talana)"] + used + ["Total", "% identificado"])
#|     tv = len(used) + 2
#|     for i, dcl in enumerate(decls, 2):
#|         wv.cell(row=i, column=1, value=dcl)
#|         for j in range(2, tv):
#|             col = get_column_letter(j)
#|             wv.cell(row=i, column=j, value=f"=SUMIFS({R_MONTO},{R_DECL},$A{i},{R_CONC},{col}$1)").number_format = MONEY
#|         wv.cell(row=i, column=tv, value=f"=SUM(B{i}:{get_column_letter(tv - 1)}{i})").number_format = MONEY
#|     tr_v = len(decls) + 2
#|     _total_row(wv, tr_v, 2, tv)
#|     sin_col = get_column_letter(used.index(SIN_ID) + 2) if SIN_ID in used else None
#|     tot_col = get_column_letter(tv)
#|     for i in range(2, tr_v + 1):
#|         f = f"=IF({tot_col}{i}=0,\"\",1-{sin_col}{i}/{tot_col}{i})" if sin_col else f"=IF({tot_col}{i}=0,\"\",1)"
#|         wv.cell(row=i, column=tv + 1, value=f).number_format = "0.0%"
#|     wv.freeze_panes = "B2"
#|     _widths(wv, [34] + [14] * (len(used) + 2))
#|     wv.cell(row=tr_v + 2, column=1, value=(
#|         "Lectura: cada fila muestra en qué se gastó realmente lo rendido bajo esa cuenta. "
#|         "SIN_IDENTIFICAR = monto declarado que no se pudo leer en los adjuntos (sin adjunto, ilegible, "
#|         "falta OCR/7-Zip). % identificado = parte del monto explicada por los respaldos.")).font = Font(italic=True, size=9)
#|
#|     wm = wb.create_sheet("Concepto x Mes")
#|     meses = sorted({parse_date(res["rendicion"].get("fecha")).strftime("%Y-%m")
#|                     for res in results if parse_date(res["rendicion"].get("fecha"))})
#|     _header(wm, ["Concepto detectado"] + meses + ["Total"])
#|     tcm = len(meses) + 2
#|     for i, c in enumerate(used, 2):
#|         wm.cell(row=i, column=1, value=c)
#|         for j in range(2, tcm):
#|             col = get_column_letter(j)
#|             wm.cell(row=i, column=j, value=f"=SUMIFS({R_MONTO},{R_CONC},$A{i},{R_MES},{col}$1)").number_format = MONEY
#|         wm.cell(row=i, column=tcm, value=f"=SUM(B{i}:{get_column_letter(tcm - 1)}{i})").number_format = MONEY
#|     _total_row(wm, len(used) + 2, 2, tcm)
#|     _widths(wm, [24] + [13] * (len(meses) + 1))
#|
#|     we = wb.create_sheet("Errores")
#|     _header(we, ["ID", "Rendidor", "Estado", "Detalle", "Adjunto"])
#|     k = 1
#|     for res in results:
#|         for e in res["errores"] or ([] if res["estado"] == "OK" else [res["estado"]]):
#|             k += 1
#|             r = res["rendicion"]
#|             for c, v in enumerate([r["id"], r["rendidor"], res["estado"], e, r.get("adjunto")], 1):
#|                 we.cell(row=k, column=c, value=v)
#|     _widths(we, [11, 28, 20, 80, 60])
#|
#|     wpar = wb.create_sheet("Parámetros")
#|     for i, (kk, vv) in enumerate(meta.items(), 1):
#|         wpar.cell(row=i, column=1, value=kk).font = Font(bold=True)
#|         wpar.cell(row=i, column=2, value=str(vv))
#|     _widths(wpar, [28, 90])
#|
#|     wb.save(out_path)
#|     return out_path
#|
#|
#| # =====================================================================
#| # 11. EJECUCIÓN COMPLETA
#| # =====================================================================
#| def run_rpa(xlsx: Path, hoja: str, motor: str, api_key: str = "", solo_movilizacion=False, rendidor=None,
#|             limite=None, reprocesar=False, progress=None, stop_event: threading.Event | None = None, cecos: list[str] | None = None, solo_finalizadas: bool = False,
#|             solo_descargar: bool = False) -> Path:
#|     rendiciones = read_rendiciones(xlsx, hoja, solo_movilizacion, rendidor, limite, cecos, solo_finalizadas)
#|     if not rendiciones:
#|         raise ValueError("No hay rendiciones que cumplan los filtros.")
#|     if cecos:
#|         log.info("CECOs seleccionados (%d): %s", len(cecos), ", ".join(cecos))
#|     if solo_descargar:
#|         dest = xlsx.parent / f"Adjuntos_por_CECO_{dt.datetime.now():%Y%m%d_%H%M}"
#|         return download_only(rendiciones, dest, xlsx.parent / f"{xlsx.stem}_rpa_cache", progress, stop_event)
#|     clf = Classifier(CONFIG["conceptos"])
#|     if motor == "claude":
#|         if not api_key:
#|             raise ValueError("El motor Claude requiere API key.")
#|         analyzer = ClaudeAnalyzer(clf, api_key)
#|         log.info("Motor: Claude (%s)", CONFIG["claude"]["modelo"])
#|     else:
#|         analyzer = LocalAnalyzer(clf)
#|         log.info("Motor: Local (texto + OCR Tesseract + reglas)")
#|
#|     cache = xlsx.parent / f"{xlsx.stem}_rpa_cache"
#|     results: list = [None] * len(rendiciones)
#|     total, done = len(rendiciones), 0
#|     with ThreadPoolExecutor(max_workers=CONFIG["descarga"]["workers"]) as ex:
#|         futs = {ex.submit(process_one, r, analyzer, cache, reprocesar): i for i, r in enumerate(rendiciones)}
#|         for fut in as_completed(futs):
#|             i = futs[fut]
#|             r = rendiciones[i]
#|             try:
#|                 results[i] = fut.result()
#|             except Exception as e:  # noqa: BLE001
#|                 log.exception("Fallo inesperado en ID %s", r.id)
#|                 results[i] = {"rendicion": asdict(r), "estado": "ERROR", "archivos": [], "items": [], "errores": [str(e)]}
#|             done += 1
#|             res = results[i]
#|             log.info("[%d/%d] %s | %s | %s | %d ítems", done, total, r.id, r.rendidor[:25], res["estado"], len(res["items"]))
#|             if progress:
#|                 progress(done, total)
#|             if stop_event is not None and stop_event.is_set():
#|                 log.warning("Detenido por el usuario: se generará el reporte con lo procesado.")
#|                 for f in futs:
#|                     f.cancel()
#|                 break
#|     results = [r for r in results if r is not None]
#|     audit(results)
#|     add_cuadratura(results)
#|
#|     out = xlsx.parent / f"Desglose_Rendiciones_{dt.datetime.now():%Y%m%d_%H%M}.xlsx"
#|     meta = {"Archivo de entrada": xlsx.resolve(), "Hoja": hoja,
#|             "Motor": analyzer.name + (f" ({CONFIG['claude']['modelo']})" if analyzer.name == "claude" else ""),
#|             "Filtros": f"solo_movilizacion={solo_movilizacion}, solo_finalizadas={solo_finalizadas}, "
#|                        f"rendidor={rendidor}, limite={limite}",
#|             "CECOs": ", ".join(cecos) if cecos else "Todos",
#|             "Fecha ejecución": dt.datetime.now().strftime("%d-%m-%Y %H:%M"),
#|             "Rendiciones procesadas": len(results),
#|             "Con alertas": sum(1 for r in results if r.get("alertas")),
#|             "Con problemas de lectura": sum(1 for r in results if r["estado"] != "OK"),
#|             "Caché (re-ejecutar no vuelve a descargar)": cache}
#|     write_report(results, out, meta)
#|     for k in ("Rendiciones procesadas", "Con alertas", "Con problemas de lectura"):
#|         log.info("%-26s %s", k, meta[k])
#|     log.info("Reporte generado: %s", out)
#|     return out
#|
#|
#| # =====================================================================
#| # 12. INTERFAZ GRÁFICA
#| # =====================================================================
#| def check_tools(motor: str) -> list[str]:
#|     faltan = []
#|     if motor == "local":
#|         cmd = find_tesseract()
#|         if not cmd:
#|             faltan.append("Tesseract OCR (fotos y PDF escaneados): github.com/UB-Mannheim/tesseract/wiki")
#|         else:
#|             try:
#|                 import pytesseract
#|                 pytesseract.pytesseract.tesseract_cmd = cmd
#|                 pytesseract.get_tesseract_version()
#|             except Exception:  # noqa: BLE001
#|                 faltan.append(f"Tesseract encontrado en {cmd} pero no arrancó (¿instalación incompleta?)")
#|     if not find_7zip():
#|         faltan.append("7-Zip o UnRAR (archivos .rar): www.7-zip.org")
#|     return faltan
#|
#|
#| class QueueHandler(logging.Handler):
#|     def __init__(self, q):
#|         super().__init__()
#|         self.q = q
#|
#|     def emit(self, record):
#|         self.q.put(("log", self.format(record)))
#|
#|
#| def launch_gui():
#|     import tkinter as tk
#|     from tkinter import filedialog, messagebox, ttk
#|
#|     root = tk.Tk()
#|     root.title("RPA Rendiciones — Desglose por concepto")
#|     root.geometry("860x620")
#|     q: queue.Queue = queue.Queue()
#|     h = QueueHandler(q)
#|     h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
#|     log.addHandler(h)
#|     stop_event = threading.Event()
#|
#|     v_file, v_sheet = tk.StringVar(), tk.StringVar()
#|     v_motor = tk.StringVar(value="claude" if os.getenv("ANTHROPIC_API_KEY") else "local")
#|     v_key = tk.StringVar(value=os.getenv("ANTHROPIC_API_KEY", ""))
#|     v_mov, v_rep = tk.BooleanVar(), tk.BooleanVar()
#|     v_fin, v_dl = tk.BooleanVar(value=True), tk.BooleanVar()
#|     v_lim, v_rend = tk.StringVar(), tk.StringVar()
#|     state = {"out": None}
#|
#|     frm = ttk.Frame(root, padding=12)
#|     frm.pack(fill="both", expand=True)
#|     frm.columnconfigure(1, weight=1)
#|
#|     def pick_file():
#|         p = filedialog.askopenfilename(title="Selecciona el Excel de rendiciones",
#|                                        filetypes=[("Excel", "*.xlsx *.xlsm"), ("Todos", "*.*")])
#|         if not p:
#|             return
#|         v_file.set(p)
#|         try:
#|             sheets = list_sheets(Path(p))
#|             cb_sheet["values"] = sheets
#|             v_sheet.set(sheets[0])
#|             load_cecos()
#|         except Exception as e:  # noqa: BLE001
#|             messagebox.showerror("Error", f"No pude abrir el Excel:\n{e}")
#|
#|     def load_cecos(*_):
#|         lb_ceco.delete(0, "end")
#|         if not v_file.get() or not v_sheet.get():
#|             return
#|         try:
#|             for c in list_cecos(Path(v_file.get()), v_sheet.get()):
#|                 lb_ceco.insert("end", c)
#|         except Exception as e:  # noqa: BLE001
#|             log.warning("No pude leer los CECOs: %s", e)
#|
#|     ttk.Label(frm, text="1. Excel de entrada").grid(row=0, column=0, sticky="w")
#|     ttk.Entry(frm, textvariable=v_file).grid(row=0, column=1, sticky="ew", padx=6)
#|     ttk.Button(frm, text="Seleccionar...", command=pick_file).grid(row=0, column=2)
#|
#|     ttk.Label(frm, text="2. Hoja").grid(row=1, column=0, sticky="w", pady=4)
#|     cb_sheet = ttk.Combobox(frm, textvariable=v_sheet, state="readonly")
#|     cb_sheet.grid(row=1, column=1, sticky="ew", padx=6)
#|     cb_sheet.bind("<<ComboboxSelected>>", load_cecos)
#|
#|     ttk.Label(frm, text="3. Motor de lectura").grid(row=2, column=0, sticky="w")
#|     mf = ttk.Frame(frm)
#|     mf.grid(row=2, column=1, sticky="w", padx=6)
#|     ttk.Radiobutton(mf, text="Local (gratis, OCR Tesseract)", variable=v_motor, value="local",
#|                    command=lambda: refresh_tools()).pack(side="left")
#|     ttk.Radiobutton(mf, text="Claude (mejor en fotos/escaneos)", variable=v_motor, value="claude").pack(side="left", padx=10)
#|
#|     ttk.Label(frm, text="   API key Anthropic").grid(row=3, column=0, sticky="w", pady=4)
#|     ttk.Entry(frm, textvariable=v_key, show="•").grid(row=3, column=1, sticky="ew", padx=6)
#|
#|     v_tess, v_7z = tk.StringVar(), tk.StringVar()
#|
#|     def refresh_tools():
#|         v_tess.set(find_tesseract() or "no encontrado")
#|         v_7z.set(find_7zip() or "no encontrado")
#|
#|     def pick_tool(titulo: str, setting_key: str):
#|         p = filedialog.askopenfilename(title=titulo, filetypes=[("Ejecutable", "*.exe"), ("Todos", "*.*")])
#|         if p:
#|             save_setting(setting_key, p)
#|             refresh_tools()
#|
#|     tf = ttk.LabelFrame(frm, text="Programas detectados (sólo motor Local)", padding=6)
#|     tf.grid(row=4, column=0, columnspan=3, sticky="ew", pady=6)
#|     tf.columnconfigure(1, weight=1)
#|     ttk.Label(tf, text="Tesseract:").grid(row=0, column=0, sticky="w")
#|     ttk.Label(tf, textvariable=v_tess, foreground="#555").grid(row=0, column=1, sticky="w", padx=6)
#|     ttk.Button(tf, text="Buscar...", command=lambda: pick_tool(
#|         "Selecciona tesseract.exe", "tesseract_cmd")).grid(row=0, column=2)
#|     ttk.Label(tf, text="7-Zip / UnRAR:").grid(row=1, column=0, sticky="w", pady=(4, 0))
#|     ttk.Label(tf, textvariable=v_7z, foreground="#555").grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
#|     ttk.Button(tf, text="Buscar...", command=lambda: pick_tool(
#|         "Selecciona 7z.exe o UnRAR.exe", "sevenzip_cmd")).grid(row=1, column=2, pady=(4, 0))
#|     refresh_tools()
#|
#|     of = ttk.LabelFrame(frm, text="Filtros (opcional)", padding=6)
#|     of.grid(row=5, column=0, columnspan=3, sticky="ew", pady=6)
#|     ttk.Checkbutton(of, text="Sólo Movilización", variable=v_mov).grid(row=0, column=0, sticky="w")  # noqa: keep-row0
#|     ttk.Label(of, text="Rendidor contiene:").grid(row=0, column=1, padx=(16, 4))
#|     ttk.Entry(of, textvariable=v_rend, width=20).grid(row=0, column=2)
#|     ttk.Label(of, text="Límite (prueba):").grid(row=0, column=3, padx=(16, 4))
#|     ttk.Entry(of, textvariable=v_lim, width=6).grid(row=0, column=4)
#|     ttk.Checkbutton(of, text="Reprocesar (ignorar caché)", variable=v_rep).grid(row=0, column=5, padx=(16, 0))
#|     ttk.Checkbutton(of, text="Sólo pagadas / finalizadas", variable=v_fin).grid(row=1, column=0, sticky="w", pady=(6, 0))
#|     ttk.Checkbutton(of, text="Sólo descargar y ordenar en carpetas CECO/Rendidor (para revisar con Cowork)",
#|                     variable=v_dl).grid(row=1, column=1, columnspan=5, sticky="w", pady=(6, 0))
#|     ttk.Label(of, text="CECOs (Ctrl/Shift+clic; ninguno = todos):").grid(row=2, column=0, columnspan=2, sticky="nw", pady=(6, 0))
#|     lf = ttk.Frame(of)
#|     lf.grid(row=2, column=2, columnspan=4, sticky="ew", pady=(6, 0))
#|     lb_ceco = tk.Listbox(lf, selectmode="extended", height=6, exportselection=False)
#|     lb_ceco.pack(side="left", fill="x", expand=True)
#|     sb_c = ttk.Scrollbar(lf, command=lb_ceco.yview)
#|     sb_c.pack(side="left", fill="y")
#|     lb_ceco["yscrollcommand"] = sb_c.set
#|
#|     def select_operacionales():
#|         objetivo = {normalize(c) for c in CONFIG["cecos_operacionales"]}
#|         lb_ceco.selection_clear(0, "end")
#|         n = 0
#|         for i in range(lb_ceco.size()):
#|             if normalize(lb_ceco.get(i)) in objetivo:
#|                 lb_ceco.selection_set(i)
#|                 n += 1
#|         log.info("CECOs operacionales seleccionados: %d de %d", n, len(objetivo))
#|
#|     bc = ttk.Frame(of)
#|     bc.grid(row=3, column=2, columnspan=4, sticky="w", pady=(4, 0))
#|     ttk.Button(bc, text="CECOs operacionales (14)", command=select_operacionales).pack(side="left")
#|     ttk.Button(bc, text="Limpiar", command=lambda: lb_ceco.selection_clear(0, "end")).pack(side="left", padx=6)
#|
#|     bf = ttk.Frame(frm)
#|     bf.grid(row=6, column=0, columnspan=3, sticky="ew", pady=4)
#|     btn_run = ttk.Button(bf, text="▶  Iniciar")
#|     btn_run.pack(side="left")
#|     btn_stop = ttk.Button(bf, text="■  Detener", state="disabled", command=lambda: stop_event.set())
#|     btn_stop.pack(side="left", padx=6)
#|     btn_open = ttk.Button(bf, text="Abrir reporte", state="disabled",
#|                           command=lambda: os.startfile(state["out"]) if hasattr(os, "startfile") else None)
#|     btn_open.pack(side="left")
#|     pb = ttk.Progressbar(bf, mode="determinate")
#|     pb.pack(side="left", fill="x", expand=True, padx=10)
#|     lbl = ttk.Label(bf, text="")
#|     lbl.pack(side="left")
#|
#|     txt = tk.Text(frm, height=20, wrap="none", font=("Consolas", 9))
#|     txt.grid(row=7, column=0, columnspan=3, sticky="nsew")
#|     frm.rowconfigure(7, weight=1)
#|     sb = ttk.Scrollbar(frm, command=txt.yview)
#|     sb.grid(row=7, column=3, sticky="ns")
#|     txt["yscrollcommand"] = sb.set
#|
#|     def start():
#|         if not v_file.get() or not Path(v_file.get()).exists():
#|             messagebox.showwarning("Falta archivo", "Selecciona el Excel de rendiciones.")
#|             return
#|         if v_motor.get() == "claude" and not v_key.get().strip() and not v_dl.get():
#|             messagebox.showwarning("Falta API key", "El motor Claude necesita una API key de Anthropic.")
#|             return
#|         try:
#|             limite = int(v_lim.get()) if v_lim.get().strip() else None
#|         except ValueError:
#|             messagebox.showwarning("Límite", "El límite debe ser un número.")
#|             return
#|         faltan = check_tools("descarga" if v_dl.get() else v_motor.get())
#|         if faltan and not messagebox.askyesno(
#|                 "Faltan programas", "No encontré:\n\n• " + "\n• ".join(faltan) +
#|                 "\n\nLo que dependa de ellos quedará como error (y se reintentará en la próxima corrida "
#|                 "cuando los instales).\n\n¿Continuar igual?"):
#|             return
#|         stop_event.clear()
#|         btn_run["state"], btn_stop["state"], btn_open["state"] = "disabled", "normal", "disabled"
#|         pb["value"] = 0
#|
#|         sel_cecos = [lb_ceco.get(i) for i in lb_ceco.curselection()]
#|
#|         def work():
#|             try:
#|                 out = run_rpa(Path(v_file.get()), v_sheet.get(), v_motor.get(), v_key.get().strip(), v_mov.get(),
#|                               v_rend.get().strip() or None, limite, v_rep.get(),
#|                               progress=lambda d, t: q.put(("prog", (d, t))), stop_event=stop_event,
#|                               cecos=sel_cecos or None, solo_finalizadas=v_fin.get(), solo_descargar=v_dl.get())
#|                 q.put(("done", out))
#|             except Exception as e:  # noqa: BLE001
#|                 log.exception("Error")
#|                 q.put(("error", str(e)))
#|
#|         threading.Thread(target=work, daemon=True).start()
#|
#|     btn_run["command"] = start
#|
#|     def poll():
#|         try:
#|             while True:
#|                 kind, payload = q.get_nowait()
#|                 if kind == "log":
#|                     txt.insert("end", payload + "\n")
#|                     txt.see("end")
#|                 elif kind == "prog":
#|                     d, t = payload
#|                     pb["maximum"], pb["value"] = t, d
#|                     lbl["text"] = f"{d}/{t}"
#|                 elif kind in ("done", "error"):
#|                     btn_run["state"], btn_stop["state"] = "normal", "disabled"
#|                     if kind == "done":
#|                         state["out"] = str(payload)
#|                         btn_open["state"] = "normal"
#|                         messagebox.showinfo("Listo", f"Reporte generado:\n{payload}")
#|                     else:
#|                         messagebox.showerror("Error", payload)
#|         except queue.Empty:
#|             pass
#|         root.after(150, poll)
#|
#|     poll()
#|     root.mainloop()
#|
#|
#| # =====================================================================
#| # 13. MAIN
#| # =====================================================================
#| def main():
#|     logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
#|     for noisy in ("httpx", "urllib3", "anthropic", "PIL"):
#|         logging.getLogger(noisy).setLevel(logging.WARNING)
#|
#|     if "--cli" not in sys.argv:
#|         try:
#|             launch_gui()
#|             return
#|         except ImportError:
#|             print("Tkinter no disponible: usa el modo --cli")
#|             return
#|
#|     p = argparse.ArgumentParser()
#|     p.add_argument("--cli", required=True, metavar="EXCEL")
#|     p.add_argument("--hoja", default=CONFIG["hojas_soportadas"][0])
#|     p.add_argument("--motor", choices=["local", "claude"], default="local")
#|     p.add_argument("--solo-movilizacion", action="store_true")
#|     p.add_argument("--rendidor")
#|     p.add_argument("--limite", type=int)
#|     p.add_argument("--reprocesar", action="store_true")
#|     p.add_argument("--ceco", action="append", help="Repetible: --ceco \"230 - SANTIAGO\" --ceco \"290 - TRADE\"")
#|     p.add_argument("--solo-finalizadas", action="store_true")
#|     p.add_argument("--cecos-operacionales", action="store_true", help="Usa los 14 CECOs de CONFIG['cecos_operacionales']")
#|     p.add_argument("--solo-descargar", action="store_true", help="Sólo descarga y ordena en carpetas CECO/Rendidor")
#|     a = p.parse_args()
#|     run_rpa(Path(a.cli), a.hoja, a.motor, os.getenv("ANTHROPIC_API_KEY", ""), a.solo_movilizacion,
#|             a.rendidor, a.limite, a.reprocesar,
#|             cecos=(CONFIG["cecos_operacionales"] if a.cecos_operacionales else a.ceco),
#|             solo_finalizadas=a.solo_finalizadas, solo_descargar=a.solo_descargar)
#|
#|
#| if __name__ == "__main__":
#|     main()
# <<< FIN rpa_rendiciones

# >>> MODULO pipeline_rendiciones
#| # -*- coding: utf-8 -*-
#| """
#| PIPELINE DE RENDICIONES — lógica compartida (lectura de adjuntos → conceptos → cuadratura con Talana)
#| =====================================================================================================
#| Lo usan `generar_base_powerbi.py` (base única para Power BI) y `post_etapa2.py` (Excel de análisis).
#|
#| Etapas, en orden (el orden importa y se mantiene idéntico al análisis ya entregado):
#|   1. lecturas manuales   -> comprobantes leídos a mano (lecturas/lote_*.json) reemplazan lo que el OCR no resolvió
#|   2. depuración          -> corrige montos de OCR dudosos y reclasifica OTROS con diccionarios (ES/EN)
#|   3. re-lectura planillas-> planillas Excel donde el parser tomó la columna equivocada (ej. "Valor por Km")
#|   4. formularios         -> "Formulario de rendición" interno = detalle oficial de la rendición
#|   5. cuadratura          -> auditoría + SIN_IDENTIFICAR por la diferencia contra lo declarado en Talana
#|   6. prorrateo           -> si lo leído supera lo declarado, se escala para que los totales cuadren con Talana
#|   7. probables           -> concepto probable y detalle (carpetas/correos) para lo que sigue sin detalle
#| """
#| from __future__ import annotations
#|
#| import glob
#| import json
#| import logging
#| import pickle
#| import re as _re
#| import unicodedata
#| from collections import defaultdict
#| from concurrent.futures import ThreadPoolExecutor
#| from dataclasses import asdict, dataclass, field
#| from pathlib import Path
#|
#| import rpa_rendiciones as R
#|
#| log = logging.getLogger("pipeline")
#|
#| CONCEPTOS_OK = {"APPS_TRANSPORTE", "COMBUSTIBLE", "ESTACIONAMIENTO", "PEAJE_TAG", "TAXI_COLECTIVO",
#|                 "TRANSPORTE_PUBLICO", "BUS_INTERURBANO", "PASAJE_AEREO", "ALOJAMIENTO", "ALIMENTACION",
#|                 "UTILES_OFICINA", "NOTARIA", "COURIER", "FERRETERIA_MANTENCION", "SUPERMERCADO", "FARMACIA", "OTROS"}
#| MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
#|             "Noviembre", "Diciembre"]
#| FAMILIAS = {
#|     "Movilización": {"APPS_TRANSPORTE", "COMBUSTIBLE", "ESTACIONAMIENTO", "PEAJE_TAG", "TAXI_COLECTIVO",
#|                      "TRANSPORTE_PUBLICO", "BUS_INTERURBANO"},
#|     "Viaje (aéreo y alojamiento)": {"PASAJE_AEREO", "ALOJAMIENTO"},
#|     "Alimentación": {"ALIMENTACION", "SUPERMERCADO"},
#|     "Oficina y operación": {"UTILES_OFICINA", "FERRETERIA_MANTENCION", "COURIER", "NOTARIA", "FARMACIA"},
#| }
#|
#|
#| def norm(s):
#|     s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
#|     return " ".join(s.replace("-", " ").split())
#|
#|
#| def familia(concepto: str) -> str:
#|     for fam, cs in FAMILIAS.items():
#|         if concepto in cs:
#|             return fam
#|     return "Sin detalle" if concepto == R.SIN_ID else "OTROS (ver subtipo)"
#|
#|
#| # ======================================================================================
#| # REGLAS (copiadas textualmente del análisis entregado; no modificar sin re-validar)
#| # ======================================================================================
#| EXTRA = [  # emisor/descripción -> concepto, para ítems automáticos que quedaron en OTROS
#|     ("TRANSPORTE_PUBLICO", ["tarjeta bip", "carga bip", "recarga bip", "compra tarjeta bip", "metro", "red movilidad",
#|                             "biotren", "merval"]),
#|     ("TAXI_COLECTIVO", ["taxi", "transvip", "radio taxi", "colectivo", "traslado taxi"]),
#|     ("ALOJAMIENTO", ["hospedaje", "alojamiento", "hostal", "hotel", "cabana", "cabanas", "apart hotel"]),
#|     ("PASAJE_AEREO", ["pasaje aereo", "vuelo", "latam", "sky airline", "jetsmart", "flight", "airline"]),
#|     ("UTILES_OFICINA", ["impresiones", "impresion", "anillado", "fotocopias", "fotocopia", "office supplies",
#|                         "papel", "tinta", "toner", "libro de asistencia", "carpetas", "archivador"]),
#|     ("ALIMENTACION", ["cena", "almuerzo", "desayuno", "bebestibles", "snack", "snacks", "agua mineral",
#|                       "office water", "water", "cake", "torta", "pastel", "comida", "food", "lunch", "dinner",
#|                       "breakfast", "coffee", "cafe", "galletas", "bebidas"]),
#|     ("PEAJE_TAG", ["peajes", "tag", "pase diario", "toll", "tolls", "road roll", "road toll", "highway"]),
#|     ("PASAJE_AEREO", ["avion", "boletos de avion", "pasaje avion", "pasajes avion", "vuelo", "vuelos", "flight", "flights", "airfare", "air ticket", "aereo"]),
#|     ("TAXI_COLECTIVO", ["traslado aeropuerto", "traslado casa aeropuerto", "aeropuerto casa", "ses aeropuerto"]),
#|     ("ALOJAMIENTO", ["estadia", "noches", "lodging", "accommodation", "holiday inn", "hilton", "marriott", "ibis", "novotel"]),
#|     ("COMBUSTIBLE", ["fuel", "gas station", "gasoline", "combustible"]),
#|     ("ESTACIONAMIENTO", ["parking", "estacionamiento", "estacionamientos"]),
#|     ("APPS_TRANSPORTE", ["ride", "rides", "road to", "uber home", "trip with uber", "trip via uber", "traslado reunion",
#|                          "traslados"]),
#|     ("ALIMENTACION", ["lunch", "dinner", "meal", "meals", "drinks", "restaurant", "coffee with", "food"]),
#|     ("COMBUSTIBLE", ["rendicion km", "kilometraje", "km recorridos", "rendicion_km"]),
#|     ("ALIMENTACION", ["uber eats", "opcion para llevar", "entregas", "banquete", "banquetes", "catering",
#|                       "coffee break", "boleta alimentacion", "alimentacion", "consumo", "colacion"]),
#|     ("APPS_TRANSPORTE", ["uber", "uberx", "uber black", "cabify", "didi", "indrive"]),
#|     ("UTILES_OFICINA", ["materiales de escritorio", "articulos de oficina"]),
#|     ("COMBUSTIBLE", ["copec", "enex", "aramco", "esmax", "shell", "petrobras", "bencina", "gasolina", "diesel"]),
#|     ("PEAJE_TAG", ["autopista", "peaje", "costanera norte", "vespucio"]),
#|     ("ESTACIONAMIENTO", ["estacionamiento", "parking", "saba"]),
#|     ("ALOJAMIENTO", ["hotel", "hostal", "hospedaje", "cabanas", "apart", "airbnb", "booking"]),
#|     ("PASAJE_AEREO", ["latam", "sky airline", "jetsmart", "aerolinea"]),
#|     ("BUS_INTERURBANO", ["turbus", "pullman", "buses", "terminal rodoviario"]),
#|     ("TRANSPORTE_PUBLICO", ["metro de santiago", "bip", "efe", "movired"]),
#|     ("TAXI_COLECTIVO", ["taxi", "radiotaxi", "transfer"]),
#|     ("ALIMENTACION", ["restaurant", "restaurante", "resto", "coffee", "coffe", "cafe", "cafeteria", "sushi", "pizza",
#|                       "burger", "gastronom", "comida", "panaderia", "pasteleria", "dulceria", "chocolate", "almuerzo",
#|                       "colacion", "cena", "coffee break", "starbucks", "mcdonald", "juan maestro", "doggis"]),
#|     ("SUPERMERCADO", ["lider", "jumbo", "tottus", "unimarc", "santa isabel", "acuenta", "supermercado", "oxxo"]),
#|     ("FERRETERIA_MANTENCION", ["sodimac", "easy", "construmart", "ferreteria", "aseo", "limpieza", "epp",
#|                                "seguridad industrial"]),
#|     ("UTILES_OFICINA", ["libreria", "lapiz lopez", "imprenta", "impresion", "toner", "resma"]),
#|     ("COURIER", ["chilexpress", "starken", "correos de chile", "bluexpress", "blue express"]),
#|     ("NOTARIA", ["notaria", "notario", "conservador"]),
#|     ("FARMACIA", ["farmacia", "cruz verde", "salcobrand", "ahumada"]),
#| ]
#| EXTRA_RX = [(c, kw, _re.compile(rf"(?<![a-z0-9]){_re.escape(kw)}(?![a-z0-9])")) for c, kws in EXTRA for kw in kws]
#|
#|
#| MONTO_COLS = ["monto a rendir", "monto a reembolsar", "total a rendir", "monto total", "valor total", "monto", "total",
#|               "importe", "valor"]
#| NO_MONTO = ["por km", "unitario", "unit", "km recorridos", "kms", "litros", "cantidad", "rut", "fecha", "n°", "nro"]
#| DESC_COLS = ["motivo", "detalle", "descripcion", "concepto", "glosa", "item", "proveedor", "destino"]
#|
#|
#| def _relee_planilla(f):
#|     """Devuelve [(descripcion, monto, es_km, fecha)] o None si no encuentra una tabla con columna de monto."""
#|     out = None
#|     for u in R.extract(f):
#|         if not u.filas:
#|             continue
#|         for hi, row in enumerate(u.filas[:30]):
#|             hdr = [R.normalize(v) if v is not None else "" for v in row]
#|             mcol = next((j for key in MONTO_COLS for j, h in enumerate(hdr)
#|                          if key in h and not any(b in h for b in NO_MONTO)), None)
#|             if mcol is None:
#|                 continue
#|             dcol = next((j for key in DESC_COLS for j, h in enumerate(hdr) if key in h), None)
#|             fcol = next((j for j, h in enumerate(hdr) if "fecha" in h), None)
#|             es_km = any("km" in h for h in hdr)
#|             rows = []
#|             for r in u.filas[hi + 1:]:
#|                 if mcol >= len(r):
#|                     continue
#|                 txt = " ".join(R.normalize(v) for v in r if isinstance(v, str))
#|                 if "total" in txt and dcol is not None and not (dcol < len(r) and r[dcol]):
#|                     continue                                     # fila TOTAL
#|                 v = R.parse_amount(r[mcol])
#|                 if not v or v < 100:
#|                     continue
#|                 desc = str(r[dcol]) if dcol is not None and dcol < len(r) and r[dcol] is not None else ""
#|                 rows.append((desc, v, es_km, r[fcol] if fcol is not None and fcol < len(r) else None))
#|             if rows:
#|                 out = (out or []) + rows
#|             break
#|     return out
#|
#|
#| def _es_total(txt):
#|     return _re.search(r"total\s*(rendicion|rendido|general|a rendir)?\s*$", txt) is not None
#|
#|
#| def _lee_formulario(f):
#|     """Filas del formulario (1ª hoja con 'TOTAL RENDICION'): [(fecha, descripcion, monto)] o None."""
#|     for u in R.extract(f):
#|         if not u.filas:
#|             continue
#|         txts = [" ".join(R.normalize(v) for v in fila if isinstance(v, str)) for fila in u.filas]
#|         tot_i = next((i for i, t in enumerate(txts) if "total rendicion" in t or "total rendido" in t), None)
#|         if tot_i is None:
#|             continue
#|         rows = []
#|         for fila in u.filas[:tot_i]:
#|             nums = [R.parse_amount(v) for v in fila if isinstance(v, (int, float)) and not isinstance(v, bool)]
#|             nums = [v for v in nums if v and v >= 100]
#|             strs = [str(v).strip() for v in fila if isinstance(v, str) and len(str(v).strip()) > 2
#|                     and not str(v).strip().replace(".", "").replace("-", "").isdigit()]
#|             if not nums or not strs:
#|                 continue
#|             desc = max(strs, key=len)
#|             if R.normalize(desc) in ("clp", "usd", "bol", "boleta", "factura", "adjunto"):
#|                 continue
#|             fch = next((v for v in fila if hasattr(v, "year")), None)
#|             rows.append((fch, desc, nums[-1]))
#|         if rows:
#|             return rows
#|     return None
#|
#|
#| def clasifica_texto(desc):
#|     t = R.normalize(desc)
#|     for c, kw, rx in EXTRA_RX:
#|         if rx.search(t):
#|             return c, f"formulario:{kw}"
#|     c, kw = R.Classifier(R.CONFIG["conceptos"]).classify(desc)
#|     return c, (f"formulario:{kw}" if kw else "")
#|
#|
#| def subtipo_otros(it):
#|     if it.get("concepto") != "OTROS":
#|         return ""
#|     raw = f"{it.get('emisor') or ''} {it.get('descripcion') or ''}"
#|     t = R.normalize(raw)
#|     for lab, kws in [
#|             ("Arriendo cowork / oficina (botón de pago)", ["comprobante de pago nro", "cowork", "martin bianchi",
#|                                                           "gilbert hermosilla", "arriendo oficina"]),
#|             ("Publicidad digital / agencias (Meta Ads, grabaciones)", ["manpower ads", "meta anuncios", "facebook",
#|                                                                       "recording session", "agency", "agencia"]),
#|             ("Telefonía / internet (SIM, planes, wifi)", ["movistar", "entel", "claro", "wom", "sim", "post paid",
#|                                                          "plan movil", "telecomunicaciones", "telefonico", "telefonia",
#|                                                          "telefono", "internet", "wifi", "cel"]),
#|             ("Actividades internas / team building / celebraciones",
#|              ["team building", "after office", "birthday", "cumpleanos", "baby shower", "end of month activity",
#|               "navidad", "afternoon tea", "actividad equipo", "salida equipo", "celebracion", "aniversario",
#|               "fiesta", "office monthly"]),
#|             ("Transferencia a persona (sin boleta)", ["transferencia", "nombre pagador", "team honor", "transferir"]),
#|             ("Premios / regalos / gift cards / bonos", ["gift", "premio", "regalo", "tarjeta regalo", "concurso", "bono"]),
#|             ("Transporte de personal contratado", ["servicio de transporte", "transporte de pasajeros",
#|                                                    "transportes rojas"]),
#|             ("Arriendo de vehículo", ["car rental", "rent a car", "econorent", "arriendo vehiculo", "arriendo auto"]),
#|             ("Equipamiento / tecnología", ["monitor", "moniters", "notebook", "mouse", "teclado", "starlink",
#|                                            "compresores", "celular nuevo", "audifonos", "impresora"]),
#|             ("Salud / vacunas / bienestar", ["vacuna", "saluvac", "employee care", "emploee care", "examen", "clinica",
#|                                              "mutual"]),
#|             ("Suscripciones / inscripciones / licencias", ["inscripcion", "suscripcion", "uptodate", "licencia",
#|                                                           "mercadopublico", "membresia"]),
#|             ("Viáticos / per diem", ["per diem", "viatico"]),
#|             ("Servicios de terceros (técnicos, alarma, mantención)", ["servicio autorizado", "alarma", "mantencion",
#|                                                                        "tecnico", "reparacion", "instalacion"]),
#|             ("Pago de cuentas / Servipag", ["servipag", "comprobante de pago", "aguas", "enel", "cuenta"]),
#|             ("Arriendo (sala, espacio, otros)", ["arriendo", "sala"]),
#|             ("Eventos / publicidad / producciones", ["evento", "eventos", "produccion", "productora", "publicidad",
#|                                                      "pendon", "impresos"]),
#|             ("Vestuario / uniformes", ["vestuario", "corbata", "uniforme", "deportes"]),
#|             ("Capacitación / cursos / seguros", ["capacitacion", "curso", "universidad", "seguro", "taller"]),
#|             ("Reunión con cliente (sin detalle del gasto)", ["reunion cliente", "reunion con cliente", "visits",
#|                                                              "meeting"]),
#|             ("Traslados / visitas anotados en planilla (sin medio de transporte)",
#|              ["visita", "traslado", "traslados", "prospeccion", "reunion", "actividad cliente", "presentacion",
#|               "retorno", "ida", "vuelta", "ingreso", "ingresos", "regreso"]),
#|             ("Gastos de oficina (planilla)", ["oficina"]),
#|             ("Voucher / boleta sin detalle del producto", ["transbank", "getnet", "mercado pago", "mercado", "klap",
#|                                                            "boleta electronica", "compra afecta", "tuu", "venta",
#|                                                            "boleta/voucher"]),
#|             ("Bazar / compras varias", ["bazar", "mall chino", "comercial", "importadora"])]:
#|         if any(_re.search(rf"(?<![a-z0-9]){_re.escape(k)}(?![a-z0-9])", t) for k in kws):
#|             return lab
#|     if it.get("metodo") == "planilla_reglas":
#|         if "exchange rate" in t or "base instalada" in t or "staffing" in t or _re.match(r"\d{5}\s", str(it.get("descripcion") or "")):
#|             return "Planilla: listado sin montos de gasto (revisar)"
#|         return "Planilla: fila sin categoría de gasto"
#|     if it.get("metodo") == "ocr_tesseract":
#|         return "Comprobante con OCR poco legible (revisar)"
#|     return "Otros sin clasificar"
#|
#|
#| def motivo_sin(it):
#|     t = R.normalize(f"{it.get('descripcion') or ''} {it.get('observacion') or ''}")
#|     for lab, k in [("Adjunto no descargable", "no descargable"), ("Comprimido no se pudo abrir", "comprimido"),
#|                    ("Rendición sin adjunto", "sin adjunto"), ("Comprobante ilegible", "ilegible"),
#|                    ("Comprobante en moneda extranjera", "usd"), ("Solo mapas / kilometraje sin montos", "mapas"),
#|                    ("Lo leído no alcanza a lo declarado (revisado a mano)", "lo leido en los comprobantes"),
#|                    ("Diferencia sin respaldo legible", "no respaldada")]:
#|         if k in t:
#|             return lab
#|     return "Respaldo incompleto o no leído"
#|
#|
#| DECL_MAP = {"003": "ALIMENTACION", "905": "UTILES_OFICINA", "506": "ESTACIONAMIENTO", "803": "NOTARIA",
#|             "707": "OTROS · Agua potable / dispensadores"}
#| _ruido = _re.compile(r"(image\d+\.(png|jpg|gif)|correo\.txt|whatsapp image|img[-_]\d+|picture-attachment|"
#|                      r"\.(pdf|jpe?g|png|xlsx?|msg|zip|rar)$|^re_ |^rv_ |^fw_ )", _re.I)
#|
#|
#| def detalle_rendicion(res):
#|     """Textos descriptivos: carpetas/archivos del adjunto y asuntos de correos .msg."""
#|     partes = []
#|     for f in res.get("archivos", []):
#|         for seg in f["ruta"].split(" > ")[1:] or [f["ruta"]]:
#|             for p in seg.split("/"):
#|                 p2 = _re.sub(r"\.(pdf|jpe?g|png|xlsx?|msg|docx?)$", "", p, flags=_re.I).strip()
#|                 if len(p2) > 6 and not _re.fullmatch(r"[\w\-\. ()]*\d{6,}[\w\-\. ()]*", p2) and \
#|                         not _re.search(r"whatsapp image|picture-attachment|^img|archivo_escaneado|^receipt_", p2, _re.I):
#|                     partes.append(p2)
#|     for e in res.get("errores", []):
#|         m = _re.search(r"([^/>]+)\.msg", e)
#|         if m:
#|             partes.append(m.group(1).strip())
#|     vistos, out = set(), []
#|     for p in partes:
#|         k = R.normalize(p)
#|         if k not in vistos:
#|             vistos.add(k)
#|             out.append(p)
#|     return out
#|
#|
#| def concepto_probable(res):
#|     votos = defaultdict(float)
#|     for p in detalle_rendicion(res):
#|         t = R.normalize(p)
#|         for c, kw, rx in EXTRA_RX:
#|             if rx.search(t):
#|                 votos[c] += 1
#|                 break
#|         else:
#|             c, kw = R.Classifier(R.CONFIG["conceptos"]).classify(p)
#|             if kw:
#|                 votos[c] += 1
#|     if votos:
#|         top = max(votos, key=votos.get)
#|         if len(votos) >= 3 and votos[top] < 0.5 * sum(votos.values()):
#|             return "Mixto (ver detalle de la rendición)", "por descripción de archivos/correos"
#|         return top, "por descripción de archivos/correos"
#|     ident = defaultdict(float)
#|     for it in res["items"]:
#|         if it.get("monto") and it.get("concepto") not in (R.SIN_ID, "OTROS"):
#|             ident[it["concepto"]] += it["monto"]
#|     tot = sum(ident.values())
#|     if tot and max(ident.values()) >= 0.4 * tot:
#|         return max(ident, key=ident.get), "por lo identificado en la misma rendición"
#|     cod = str(res["rendicion"].get("concepto_declarado") or "")[:3]
#|     if cod == "002":
#|         return (max(ident, key=ident.get) if tot else "Movilización (sin detalle)"), "por cuenta declarada (movilización)"
#|     if cod in DECL_MAP:
#|         return DECL_MAP[cod], "por cuenta declarada"
#|     return "", ""
#|
#|
#| GENERICOS = {"Comprobante con OCR poco legible (revisar)", "Otros sin clasificar", "Planilla: fila sin categoría de gasto",
#|              "Voucher / boleta sin detalle del producto"}
#|
#|
#| # ======================================================================================
#| # CARGA
#| # ======================================================================================
#| def cargar_resultados(xlsx: Path, pkl: Path | None = None, reprocesar: bool = False,
#|                       hoja: str = "Reembolsos Consolidado") -> list[dict]:
#|     """Resultado de la lectura automática (descarga + texto + OCR) de las rendiciones de los CECOs operacionales.
#|     Usa `pkl` si existe (rápido). Con `reprocesar=True` vuelve a leer desde la caché de adjuntos
#|     (`<excel>_rpa_cache`), que descarga sólo lo que falte."""
#|     if pkl and Path(pkl).exists() and not reprocesar:
#|         log.info("Resultados del RPA desde %s", pkl)
#|         return pickle.load(open(pkl, "rb"))
#|     rendiciones = R.read_rendiciones(xlsx, hoja, cecos=R.CONFIG["cecos_operacionales"], solo_finalizadas=True)
#|     cache = xlsx.parent / f"{xlsx.stem}_rpa_cache"
#|     analyzer = R.LocalAnalyzer(R.Classifier(R.CONFIG["conceptos"]))
#|     log.info("Leyendo %d rendiciones (caché: %s)", len(rendiciones), cache)
#|     with ThreadPoolExecutor(R.CONFIG["descarga"]["workers"]) as ex:
#|         results = list(ex.map(lambda r: R.process_one(r, analyzer, cache, False), rendiciones))
#|     if pkl:
#|         pickle.dump(results, open(pkl, "wb"))
#|     return results
#|
#|
#| def cargar_lecturas(dir_lecturas: Path, indice_json: Path) -> tuple[dict, dict]:
#|     """Lecturas manuales (lecturas/lote_*.json) + índice de páginas exportadas (revisar/indice.json)."""
#|     lecturas = {}
#|     for fn in sorted(glob.glob(str(Path(dir_lecturas) / "lote_*.json"))):      # los .jsonl son borradores
#|         for x in json.load(open(fn, encoding="utf-8")):
#|             lecturas[x["id"]] = x
#|     index = {}
#|     if Path(indice_json).exists():
#|         index = {x["id"]: x for x in json.load(open(indice_json, encoding="utf-8"))}
#|     elif lecturas:
#|         log.warning("No existe %s: las lecturas manuales no podrán reemplazar las páginas leídas por OCR", indice_json)
#|     return lecturas, index
#|
#|
#| # ======================================================================================
#| # ETAPAS
#| # ======================================================================================
#| def aplicar_lecturas_manuales(results, lecturas, index):
#|     ilegible_ids, n_items = set(), 0
#|     for res in results:
#|         rid = res["rendicion"]["id"]
#|         if rid not in lecturas:
#|             continue
#|         lec, pags = lecturas[rid], {p["img"]: p for p in index.get(rid, {}).get("paginas", [])}
#|         leidas = {(p["archivo"], p["pagina"]) for p in pags.values()}   # páginas leídas a mano (sin monto o sólo OCR)
#|         res["items"] = [it for it in res["items"] if (it.get("archivo"), it.get("pagina")) not in leidas]
#|         for it in lec.get("items", []):
#|             c = it["concepto"].strip().upper()
#|             assert c in CONCEPTOS_OK, (rid, c)
#|             p = pags.get(it.get("img"), {})
#|             item = asdict(R.Item(tipo_documento=it.get("tipo", ""), emisor=it.get("emisor", ""),
#|                                  fecha_doc=R.parse_date(it.get("fecha")), descripcion=it.get("descripcion", ""),
#|                                  monto=float(it["monto"]), concepto=c, metodo="lectura_manual", confianza="media",
#|                                  observacion=it.get("obs", "")))
#|             res["items"].append({**item, "archivo": p.get("archivo", ""), "tipo_archivo": p.get("tipo_archivo", ""),
#|                                  "pagina": p.get("pagina"), "alertas": ""})
#|             n_items += 1
#|         if lec.get("ilegibles"):
#|             ilegible_ids.add(rid)
#|         res["estado"] = (res["estado"] + " + LECTURA MANUAL") if lec.get("items") else (res["estado"] + " + ILEGIBLE")
#|     return n_items, ilegible_ids
#|
#|
#| def depurar_automaticos(results):
#|     n_ocr_dudoso = n_reclas = 0
#|     for res in results:
#|         declared = res["rendicion"].get("monto_declarado") or 0
#|         decl_n = R.normalize(res["rendicion"].get("concepto_declarado"))
#|         for it in res["items"]:
#|             if it.get("metodo") == "lectura_manual":        # lo leído a mano no se toca
#|                 continue
#|             raw = f"{it.get('emisor') or ''} {it.get('descripcion') or ''}"
#|             if it.get("metodo") in ("ocr_tesseract", "texto_pdf_texto") and it.get("monto"):
#|                 m_exp = _re.search(r"monto\s*\$\s*([\d\.]{3,})", raw, _re.I)
#|                 if m_exp:                                   # "Monto $140.000" explícito en el comprobante
#|                     v = R.parse_amount(m_exp.group(1))
#|                     if v and v != it["monto"]:
#|                         it["observacion"] = f"Monto corregido al 'Monto $' explícito (OCR leía {it['monto']:,.0f})".replace(",", ".")
#|                         it["monto"] = v
#|                 elif _re.search(rf"\b{int(it['monto'])}\b", raw.replace(".", "")) and _re.search(r"\d{7}\s+\w", raw) \
#|                         and str(int(it["monto"])).endswith("000") and len(str(int(it["monto"]))) == 7:
#|                     it["observacion"] = "Monto OCR era un código postal: no se considera"
#|                     if _re.search(r",\s*CL\b", raw):
#|                         it["concepto"], it["palabra_clave"] = "APPS_TRANSPORTE", "reclasif:recibo app (direccion, CL)"
#|                     it["monto"], n_ocr_dudoso = None, n_ocr_dudoso + 1
#|                     continue
#|             if it.get("metodo") == "planilla_reglas" and it.get("monto") and it.get("concepto") == "OTROS" \
#|                     and "movilizacion" in decl_n:
#|                 it["concepto"], it["palabra_clave"] = "COMBUSTIBLE", "reclasif:planilla movilizacion (km vehiculo propio)"
#|                 n_reclas += 1
#|             if it.get("metodo") == "ocr_tesseract" and it.get("monto") and declared and it["monto"] > declared * 1.05:
#|                 it["observacion"] = f"Monto OCR dudoso ({it['monto']:,.0f} > declarado): no se considera".replace(",", ".")
#|                 it["monto"], n_ocr_dudoso = None, n_ocr_dudoso + 1
#|             if it.get("monto") and it.get("concepto") == "OTROS" and it.get("metodo") != "planilla_reglas" \
#|                     and _re.search(r"\d{1,2}:\d{2}.*,\s*(CL|Chile)\b", raw):   # recibo de viaje app (hora + dirección, CL)
#|                 it["concepto"], it["palabra_clave"], n_reclas = "APPS_TRANSPORTE", "reclasif:recibo viaje app", n_reclas + 1
#|             if it.get("monto") and it.get("concepto") == "OTROS" and it.get("metodo") == "planilla_reglas" \
#|                     and _re.search(r"(?<![a-z])(pasajes?|buses?|terminal|turbus|pullman)(?![a-z])", R.normalize(raw)) \
#|                     and not _re.search(r"(?<![a-z])(uber|taxi|cabify|avion|aereo)(?![a-z])", R.normalize(raw)):
#|                 it["concepto"], it["palabra_clave"], n_reclas = "BUS_INTERURBANO", "reclasif:pasaje/terminal (planilla)", n_reclas + 1
#|             if it.get("monto") and it.get("concepto") == "OTROS":
#|                 t = R.normalize(f"{it.get('emisor') or ''} {it.get('descripcion') or ''}")
#|                 for c, kw, rx in EXTRA_RX:
#|                     if rx.search(t):
#|                         it["concepto"], it["palabra_clave"], n_reclas = c, f"reclasif:{kw}", n_reclas + 1
#|                         break
#|     return n_ocr_dudoso, n_reclas
#|
#|
#| def rele_planillas(results, lecturas, cache: Path):
#|     n_planillas = 0
#|     for res in results:
#|         rid, r = res["rendicion"]["id"], res["rendicion"]
#|         declared = r.get("monto_declarado") or 0
#|         if rid in lecturas or not declared:
#|             continue
#|         pl = [it for it in res["items"] if it.get("metodo") == "planilla_reglas"]
#|         detected = sum(it["monto"] for it in res["items"] if it.get("monto") and it.get("concepto") != R.SIN_ID)
#|         if not pl or detected >= 0.6 * declared:
#|             continue
#|         try:
#|             data = R.download(r["adjunto"], cache / "descargas").read_bytes()
#|             files = {f.ruta: f for f in R.expand(R.filename_from_url(r["adjunto"]), data)}
#|         except Exception:  # noqa: BLE001
#|             continue
#|         nuevos, rutas = [], set()
#|         for ruta in {it["archivo"] for it in pl}:
#|             f = files.get(ruta)
#|             rows = _relee_planilla(f) if f else None
#|             if not rows:
#|                 continue
#|             s = sum(v for _, v, _, _ in rows)
#|             if not (0.5 * declared <= s <= 1.5 * declared):          # sólo si la re-lectura cuadra razonablemente
#|                 continue
#|             rutas.add(ruta)
#|             clf = R.Classifier(R.CONFIG["conceptos"])
#|             for desc, v, es_km, fch in rows:
#|                 c, kw = ("COMBUSTIBLE", "planilla km (vehiculo propio)") if es_km else clf.classify(desc)
#|                 nuevos.append({**asdict(R.Item(tipo_documento="Planilla", descripcion=desc[:120], monto=v, concepto=c,
#|                                                palabra_clave=kw, metodo="planilla_relectura", confianza="media",
#|                                                fecha_doc=R.parse_date(fch))),
#|                                "archivo": ruta, "tipo_archivo": "xlsx", "pagina": 1, "alertas": ""})
#|         if rutas:
#|             res["items"] = [it for it in res["items"] if not (it.get("metodo") == "planilla_reglas" and it["archivo"] in rutas)]
#|             res["items"] += nuevos
#|             n_planillas += 1
#|     return n_planillas
#|
#|
#| def formularios_rendicion(results, lecturas, cache: Path):
#|     n_form = 0
#|     for res in results:
#|         r, rid = res["rendicion"], res["rendicion"]["id"]
#|         declared = r.get("monto_declarado") or 0
#|         if rid in lecturas or not declared or not any(it.get("metodo") == "planilla_reglas" for it in res["items"]):
#|             continue
#|         try:
#|             data = R.download(r["adjunto"], cache / "descargas").read_bytes()
#|             files = R.expand(R.filename_from_url(r["adjunto"]), data)
#|         except Exception:  # noqa: BLE001
#|             continue
#|         vistos, filas = set(), []
#|         for f in files:
#|             if f.tipo not in ("xlsx", "xls", "xlsm", "csv"):
#|                 continue
#|             try:
#|                 rows = _lee_formulario(f)
#|             except Exception:  # noqa: BLE001
#|                 rows = None
#|             if not rows:
#|                 continue
#|             firma = tuple(sorted((R.normalize(d), round(v)) for _, d, v in rows))
#|             if firma in vistos:                                  # copia del mismo formulario
#|                 continue
#|             vistos.add(firma)
#|             filas += [(f.ruta, fch, d, v) for fch, d, v in rows]
#|         s_f = sum(v for *_, v in filas)
#|         if not filas or s_f < 0.8 * declared:
#|             continue
#|         nuevos = []
#|         for ruta, fch, desc, v in filas:
#|             c, kw = clasifica_texto(desc)
#|             nuevos.append({**asdict(R.Item(tipo_documento="Formulario rendición", descripcion=desc[:120], monto=v,
#|                                            concepto=c, palabra_clave=kw, metodo="formulario_rendicion", confianza="media",
#|                                            fecha_doc=R.parse_date(fch))),
#|                            "archivo": ruta, "tipo_archivo": "xlsx", "pagina": 1, "alertas": ""})
#|         res["items"] = [it for it in res["items"] if it.get("metodo") in ("lectura_manual",)] + nuevos
#|         n_form += 1
#|     return n_form
#|
#|
#| def cuadrar(results, lecturas, ilegible_ids):
#|     R.audit(results)
#|     R.add_cuadratura(results)
#|     for res in results:                      # observación de la diferencia en las revisadas a mano
#|         rid = res["rendicion"]["id"]
#|         if rid in lecturas:
#|             for it in res["items"]:
#|                 if it.get("concepto") == R.SIN_ID and it.get("metodo") == "cuadratura":
#|                     if lecturas[rid].get("obs_sin"):
#|                         it["descripcion"] = "Diferencia no respaldada en comprobantes legibles en CLP"
#|                         it["observacion"] = lecturas[rid]["obs_sin"]
#|                     elif rid in ilegible_ids:
#|                         it["descripcion"], it["observacion"] = "Comprobante(s) ilegible(s)", "ilegible"
#|                     else:
#|                         it["descripcion"] = "Diferencia: lo leído en los comprobantes no alcanza al monto declarado"
#|                         it["observacion"] = "no respaldado en comprobantes visibles"
#|
#|
#| def prorratear(results):
#|     """Si lo leído supera lo declarado (planilla resumen + detalle, boletas de otra rendición), los montos se
#|     escalan a lo declarado para que los totales por concepto cuadren con lo pagado en Talana."""
#|     n_prorr = 0
#|     for res in results:
#|         declared = res["rendicion"].get("monto_declarado") or 0
#|         items = [it for it in res["items"] if it.get("monto") and it.get("concepto") != R.SIN_ID]
#|         detected = sum(it["monto"] for it in items)
#|         f = declared / detected if declared and detected > declared + 1 else 1.0
#|         n_prorr += f < 1
#|         ids = {id(it) for it in items}
#|         for it in res["items"]:
#|             it["monto_bruto"], it["factor"] = it.get("monto"), f
#|             if f < 1 and id(it) in ids:
#|                 it["monto"] = round(it["monto"] * f, 2)
#|         if f < 1:  # el residuo de redondeo va al ítem mayor: la suma queda exactamente igual a lo declarado
#|             mayor = max(items, key=lambda x: x["monto"])
#|             mayor["monto"] = round(mayor["monto"] + declared - sum(x["monto"] for x in items), 2)
#|     return n_prorr
#|
#|
#| def calcular_probables(results):
#|     prob = {}
#|     for res in results:
#|         c, base = concepto_probable(res)
#|         prob[res["rendicion"]["id"]] = (c, base, " | ".join(detalle_rendicion(res))[:250])
#|     return prob
#|
#|
#| @dataclass
#| class Estado:
#|     results: list
#|     lecturas: dict
#|     prob: dict
#|     stats: dict = field(default_factory=dict)
#|
#|
#| def ejecutar_pipeline(results, lecturas, index, cache: Path) -> Estado:
#|     """Aplica todas las etapas sobre `results` (se modifica en el lugar)."""
#|     n_items, ilegible_ids = aplicar_lecturas_manuales(results, lecturas, index)
#|     n_ocr_dudoso, n_reclas = depurar_automaticos(results)
#|     n_planillas = rele_planillas(results, lecturas, cache)
#|     n_form = formularios_rendicion(results, lecturas, cache)
#|     cuadrar(results, lecturas, ilegible_ids)
#|     n_prorr = prorratear(results)
#|     prob = calcular_probables(results)
#|     stats = {"Rendiciones procesadas": len(results), "Revisadas a mano (falta OCR)": len(lecturas),
#|              "Ítems leídos a mano": n_items, "Con comprobantes ilegibles": len(ilegible_ids),
#|              "Rendiciones prorrateadas (leído > declarado)": n_prorr,
#|              "Ítems OCR descartados (monto > declarado)": n_ocr_dudoso, "Ítems OTROS reclasificados": n_reclas,
#|              "Planillas re-leídas (columna de monto corregida)": n_planillas,
#|              "Rendiciones con formulario de rendición como detalle oficial": n_form}
#|     return Estado(results, lecturas, prob, stats)
# <<< FIN pipeline_rendiciones

# >>> MODULO generar_base_powerbi
#| # -*- coding: utf-8 -*-
#| """
#| GENERADOR DE BASE ÚNICA PARA POWER BI — Rendiciones por CECO / Rendidor / Concepto (+ Cabify corporativo)
#| =========================================================================================================
#| Un solo comando genera una tabla plana (una fila por comprobante/ítem) con todo el detalle, lista para Power BI:
#|
#|     python generar_base_powerbi.py --excel Presupuestos_Rendiciones_actualizado.xlsx
#|
#| Entradas
#|   --excel     Excel de Talana (hojas "Reembolsos Consolidado", "Cabify Extracto", "Auditoria Cabify vs Rend")
#|   --lecturas  carpeta con las lecturas manuales de comprobantes (lecturas/lote_*.json)           [opcional]
#|   --indice    índice de páginas exportadas para esas lecturas (revisar/indice.json)              [opcional]
#|   --pkl       resultados del RPA (lectura automática). Si no existe se lee desde la caché de adjuntos
#|
#| Salidas (carpeta --salida, por defecto salida_powerbi/)
#|   Base_Rendiciones_PowerBI.xlsx    ← ARCHIVO MAESTRO. Tabla "tblBase" + Diccionario + Conciliación.
#|                                      Al final tiene las columnas de SEGUIMIENTO DE FACTURACIÓN (se editan aquí).
#|   Base_Rendiciones_PowerBI.csv     copia de sólo lectura (UTF-8)
#|   Base_Rendiciones_PowerBI.parquet copia de sólo lectura, tipada y más rápida para Power BI
#|   historial/                       respaldo del maestro anterior en cada ejecución
#|   facturacion_huerfanos.csv        seguimiento de registros que ya no existen en la nueva base (si los hubiera)
#|
#| Seguimiento de facturación: al regenerar la base, lo que se haya anotado en las columnas finales
#| (Estado_Facturacion … Obs_Facturacion) se conserva, asociado a la columna Clave_Item (estable entre ejecuciones).
#| """
#| from __future__ import annotations
#|
#| import argparse
#| import datetime as dt
#| import hashlib
#| import logging
#| import os
#| import pickle
#| import re
#| import shutil
#| import sys
#| from pathlib import Path
#|
#| os.environ.setdefault("OMP_THREAD_LIMIT", "1")      # Tesseract con 1 hilo por proceso: ~500x más rápido en equipos de pocos núcleos
#|
#| import pandas as pd  # noqa: E402
#|
#| import pipeline_rendiciones as P  # noqa: E402
#| import rpa_rendiciones as R  # noqa: E402
#|
#| log = logging.getLogger("base_powerbi")
#|
#| ESTADOS_FACT = ["Sin clasificar", "Facturado", "Pendiente de facturar", "No facturable", "Facturación parcial"]
#| TIPO_REG_REND, TIPO_REG_CAB = "Rendición", "Cabify corporativo"
#|
#| # (nombre, grupo, tipo, descripción). El ORDEN de esta lista es el orden de las columnas.
#| COLUMNAS = [
#|     ("Clave_Item", "Identificación", "texto", "Identificador estable del registro; se mantiene entre ejecuciones y ata el seguimiento de facturación."),
#|     ("Tipo_Registro", "Identificación", "texto", "'Rendición' (gasto rendido en Talana) o 'Cabify corporativo' (viaje del extracto Cabify, informativo)."),
#|     ("ID_Rendicion", "Identificación", "texto", "ID de la solicitud en Talana (CDR…). Vacío en filas Cabify."),
#|     ("Estado_Talana", "Identificación", "texto", "Estado de la solicitud en Talana."),
#|     ("Estado_Lectura", "Identificación", "texto", "Resultado de la lectura de adjuntos: OK, PARCIAL, ERROR…; '+ LECTURA MANUAL' si se leyó a mano."),
#|     ("Cuenta_Declarada", "Solicitud Talana", "texto", "Cuenta contable declarada por el rendidor (ej. 407 - GASTOS VARIOS, 002 - MOVILIZACIÓN)."),
#|     ("Accion_Solicitada", "Solicitud Talana", "texto", "Acción de la solicitud (reembolso, cierre de fondo, etc.)."),
#|     ("Tipo_Fondo", "Solicitud Talana", "texto", "Qué se sustenta o reembolsa (Fondo Fijo, Fondo Temporal, …)."),
#|     ("N_Requerimiento_Asociado", "Solicitud Talana", "texto", "Requerimiento al que se asocia (p. ej. el fondo que se cierra)."),
#|     ("Tipo_Documento_Declarado", "Solicitud Talana", "texto", "Tipo de documento que declaró adjuntar el rendidor."),
#|     ("Periodo_Origen", "Solicitud Talana", "texto", "Periodo de origen de la solicitud en Talana."),
#|     ("Fecha_Resolucion", "Solicitud Talana", "fecha", "Fecha de resolución de la solicitud."),
#|     ("Responsables_Aprobacion", "Solicitud Talana", "texto", "Aprobadores de la solicitud."),
#|     ("CECO", "Organización", "texto", "Centro de costo completo (ej. 230 - SANTIAGO)."),
#|     ("CECO_Codigo", "Organización", "texto", "Código del CECO (ej. 230)."),
#|     ("CECO_Nombre", "Organización", "texto", "Nombre del CECO (ej. SANTIAGO)."),
#|     ("Rendidor", "Organización", "texto", "Persona que rinde. En filas Cabify: el rendidor de Talana si el pasajero calzó con uno; si no, el pasajero."),
#|     ("Rut_Rendidor", "Organización", "texto", "RUT del creador de la solicitud en Talana."),
#|     ("Usuario_Cabify", "Organización", "texto", "SI/NO: figura como usuario Cabify en la hoja 'Auditoria Cabify vs Rend' (en filas Cabify siempre SI)."),
#|     ("Cabify_Pasajero_Match", "Organización", "texto", "Nombre(s) del pasajero Cabify que calzan con este rendidor (cruce por nombre)."),
#|     ("Cabify_Match_Fuente", "Organización", "texto", "Cómo se hizo el cruce: 'Auditoría Cabify vs Rend' o 'Nombre + apellido' (validar homónimos)."),
#|     ("Fecha_Reporte", "Fechas", "fecha", "Fecha para el eje de tiempo: fecha de la rendición; en Cabify, el 1° del mes del viaje."),
#|     ("Anio", "Fechas", "entero", "Año de Fecha_Reporte."),
#|     ("Mes_Num", "Fechas", "entero", "Mes (1-12) de Fecha_Reporte."),
#|     ("Mes_Nombre", "Fechas", "texto", "Nombre del mes en español."),
#|     ("Periodo", "Fechas", "texto", "AAAA-MM de Fecha_Reporte (ordena bien como texto)."),
#|     ("Fecha_Documento", "Fechas", "fecha", "Fecha del comprobante (boleta, factura, voucher) cuando se pudo leer."),
#|     ("Dias_Doc_a_Rendicion", "Fechas", "entero", "Días entre el comprobante y la rendición (negativo: comprobante posterior)."),
#|     ("Concepto", "Clasificación", "texto", "Concepto oficial de los 17 definidos (+ SIN_IDENTIFICAR)."),
#|     ("Familia_Concepto", "Clasificación", "texto", "Agrupación de conceptos: Movilización, Viaje, Alimentación, Oficina y operación, OTROS, Sin detalle."),
#|     ("Concepto_Detallado", "Clasificación", "texto", "Concepto con su apertura: 'OTROS · <subtipo>' o 'SIN_IDENTIFICAR · <motivo>'."),
#|     ("Subtipo_OTROS", "Clasificación", "texto", "Qué hay dentro de OTROS (transferencias, arriendo cowork, telefonía, eventos…)."),
#|     ("Nivel_Detalle", "Clasificación", "texto", "Calidad del detalle: Concepto identificado / OTROS con subtipo / Sin detalle · con concepto probable / Sin detalle · sin pista."),
#|     ("Concepto_Probable", "Clasificación", "texto", "Mejor estimación del concepto cuando no hay comprobante legible (no es un dato leído)."),
#|     ("Base_Probable", "Clasificación", "texto", "En qué se basa el concepto probable."),
#|     ("Concepto_Estimado", "Clasificación", "texto", "Concepto + probable para lo que no tiene detalle. Filtrar por Nivel_Detalle para separar lo estimado."),
#|     ("Detalle_Rendicion", "Clasificación", "texto", "Nombres de carpetas/archivos/correos del adjunto (pistas de qué se rindió)."),
#|     ("Emisor", "Comprobante", "texto", "Comercio o persona que emite el comprobante."),
#|     ("RUT_Emisor", "Comprobante", "texto", "RUT del emisor, si se pudo leer."),
#|     ("Folio", "Comprobante", "texto", "N° de boleta/factura, si se pudo leer."),
#|     ("Tipo_Documento", "Comprobante", "texto", "Tipo de documento leído (boleta, factura, planilla, formulario…)."),
#|     ("Descripcion", "Comprobante", "texto", "Descripción del ítem tal como se leyó."),
#|     ("Palabra_Clave", "Comprobante", "texto", "Regla o palabra clave que determinó el concepto."),
#|     ("Metodo_Lectura", "Comprobante", "texto", "Cómo se leyó: texto_pdf_texto, ocr_tesseract, planilla_reglas, planilla_relectura, formulario_rendicion, lectura_manual, cuadratura, extracto_cabify."),
#|     ("Confianza", "Comprobante", "texto", "Confianza de la lectura (alta/media/baja), si aplica."),
#|     ("Archivo_Interno", "Comprobante", "texto", "Ruta del archivo dentro del adjunto."),
#|     ("Tipo_Archivo", "Comprobante", "texto", "pdf, jpg, xlsx…"),
#|     ("Pagina", "Comprobante", "entero", "Página o foto dentro del archivo."),
#|     ("Facturable_Segun_Carpeta", "Comprobante", "texto", "FACTURABLE / NO FACTURABLE según el nombre de la carpeta del adjunto (hoy sólo viene en Trade). Es una SUGERENCIA, no el seguimiento."),
#|     ("Detalle_Carpeta", "Comprobante", "texto", "Descripción del gasto según el nombre de la carpeta (Trade)."),
#|     ("Persona_Carpeta", "Comprobante", "texto", "Persona indicada en el nombre de la carpeta (Trade)."),
#|     ("Ticket_Cabify", "Cabify", "texto", "Código de ticket del viaje."),
#|     ("Pasajero_Cabify", "Cabify", "texto", "Pasajero del viaje."),
#|     ("Solicitante_Cabify", "Cabify", "texto", "Quien solicitó el viaje."),
#|     ("Vertical_Cabify", "Cabify", "texto", "Vertical del solicitante."),
#|     ("Estado_Viaje_Cabify", "Cabify", "texto", "Estado final del viaje (completado, cancelado…)."),
#|     ("Link_Adjunto", "Comprobante", "texto", "Link al adjunto en Talana (en Power BI: categoría de datos 'URL web')."),
#|     ("Monto_CLP", "Montos", "moneda", "GASTO en CLP, cuadrado a lo declarado en Talana (suma por rendición = declarado). 0 en filas Cabify."),
#|     ("Monto_Bruto_Leido_CLP", "Montos", "moneda", "Monto leído en el comprobante antes del prorrateo."),
#|     ("Factor_Prorrateo", "Montos", "decimal", "Monto_CLP / Monto_Bruto_Leido. <1 cuando lo leído superaba lo declarado (planilla resumen + detalle, boletas repetidas)."),
#|     ("Monto_Cabify_CLP", "Montos", "moneda", "Precio del viaje Cabify en CLP (sólo filas Cabify; informativo, no es parte de Monto_CLP)."),
#|     ("Declarado_Rendicion_CLP", "Montos", "moneda", "Monto declarado en Talana. SOLO en la primera fila de cada rendición (evita sumarlo varias veces)."),
#|     ("Fila_Principal", "Montos", "entero", "1 en la primera fila de cada rendición, 0 en el resto."),
#|     ("Alertas", "Calidad y auditoría", "texto", "Alertas del ítem (alerta de concepto, documento antiguo, comprobante repetido…)."),
#|     ("Alertas_Rendicion", "Calidad y auditoría", "texto", "Alertas de la rendición completa (mismo adjunto en otra rendición, diferencia de monto…)."),
#|     ("Observacion", "Calidad y auditoría", "texto", "Observaciones de la lectura (OCR corregido, comprobante duplicado, ilegible, etc.)."),
#|     ("Alerta_Duplicado", "Calidad y auditoría", "texto", "SI si alguna alerta u observación indica posible duplicidad o comprobante usado en otra rendición."),
#|     ("Estado_Facturacion", "Seguimiento de facturación", "texto", "EDITAR. Sin clasificar / Facturado / Pendiente de facturar / No facturable / Facturación parcial."),
#|     ("Cliente_Facturado", "Seguimiento de facturación", "texto", "EDITAR. Cliente al que se facturó (o se facturará)."),
#|     ("N_Factura", "Seguimiento de facturación", "texto", "EDITAR. N° de la factura emitida."),
#|     ("Fecha_Factura", "Seguimiento de facturación", "fecha", "EDITAR. Fecha de la factura."),
#|     ("Monto_Facturado_CLP", "Seguimiento de facturación", "moneda", "EDITAR. Monto facturado (CLP)."),
#|     ("Obs_Facturacion", "Seguimiento de facturación", "texto", "EDITAR. Comentario libre del seguimiento."),
#| ]
#| COLS = [c[0] for c in COLUMNAS]
#| TIPOS = {c[0]: c[2] for c in COLUMNAS}
#| TRACK = [c[0] for c in COLUMNAS if c[1] == "Seguimiento de facturación"]
#|
#| CARPETA_RX = re.compile(r"(?:^|/|> )([^/>]*?) - (\d{4})_(\d{2})_(\d{2}) - ([A-Za-zÁÉÍÓÚáéíóú]+)[^/>]*? - "
#|                         r"(FACTURABLE|NO FACTURABLE|NO) - ([^/>]+)", re.I)
#| DUP_RX = re.compile(r"duplic|repetid|mismo (adjunto|voucher|detalle|ticket|comprobante|pago)|también (respalda|usado|en )|"
#|                     r"ya rendid|misma boleta|usado en id", re.I)
#|
#|
#| # ======================================================================================
#| # DATOS DE TALANA
#| # ======================================================================================
#| def _col(df, *prefijos):
#|     for p in prefijos:
#|         for c in df.columns:
#|             if P.norm(c).startswith(P.norm(p)):
#|                 return c
#|     return None
#|
#|
#| def leer_talana(xlsx: Path, hoja: str = "Reembolsos Consolidado") -> dict:
#|     """Campos adicionales de la solicitud en Talana, por ID."""
#|     df = pd.read_excel(xlsx, sheet_name=hoja)
#|     if "ID" not in df.columns:
#|         df = pd.read_excel(xlsx, sheet_name=hoja, header=1)
#|     campos = {"rut": _col(df, "Rut Creador"), "accion": _col(df, "Seleccione que acción requiere"),
#|               "fondo": _col(df, "Seleccione que desea sustentar"), "req": _col(df, "Indicar el número del requerimiento"),
#|               "tipo_doc": _col(df, "Indique el tipo de documento"), "periodo": _col(df, "Periodo Origen"),
#|               "fres": _col(df, "Fecha resolución"), "resp": _col(df, "Nombre(s) Responsable")}
#|     out = {}
#|     for _, row in df.iterrows():
#|         rid = row["ID"]
#|         if pd.isna(rid):
#|             continue
#|         d = {}
#|         for k, c in campos.items():
#|             v = row[c] if c else None
#|             d[k] = None if v is None or (not isinstance(v, str) and pd.isna(v)) else v
#|         out[str(rid)] = d
#|     return out
#|
#|
#| def _txt(v):
#|     return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v).strip()
#|
#|
#| def _fecha(v):
#|     if v is None or (not isinstance(v, str) and pd.isna(v)):
#|         return None
#|     try:
#|         return R.parse_date(v)
#|     except Exception:  # noqa: BLE001
#|         return None
#|
#|
#| def info_carpeta(ruta: str) -> tuple[str, str, str]:
#|     """(facturable, detalle, persona) según el nombre de carpeta 'PERSONA - AAAA_MM_DD - Tipo - FACTURABLE - Detalle'."""
#|     m = CARPETA_RX.search(ruta or "")
#|     if not m:
#|         return "", "", ""
#|     fact = "FACTURABLE" if m.group(6).upper() == "FACTURABLE" else "NO FACTURABLE"
#|     return fact, m.group(7).strip()[:140], m.group(1).strip()
#|
#|
#| def estimado(prob: str) -> str:
#|     if not prob:
#|         return "SIN_IDENTIFICAR"
#|     if prob.startswith("Mixto"):
#|         return "MIXTO"
#|     if prob.startswith("Movilización"):
#|         return "MOVILIZACION_SIN_DETALLE"
#|     if prob.startswith("OTROS"):
#|         return "OTROS"
#|     return prob
#|
#|
#| # ======================================================================================
#| # FILAS
#| # ======================================================================================
#| class Claves:
#|     def __init__(self):
#|         self.occ = {}
#|
#|     def __call__(self, base: str) -> str:
#|         n = self.occ[base] = self.occ.get(base, 0) + 1
#|         return hashlib.sha1(f"{base}|{n}".encode()).hexdigest()[:12]
#|
#|
#| def _periodo_cols(f: dt.date | None) -> dict:
#|     if not f:
#|         return {"Fecha_Reporte": None, "Anio": None, "Mes_Num": None, "Mes_Nombre": "", "Periodo": ""}
#|     return {"Fecha_Reporte": f, "Anio": f.year, "Mes_Num": f.month, "Mes_Nombre": P.MESES_ES[f.month - 1],
#|             "Periodo": f"{f.year}-{f.month:02d}"}
#|
#|
#| def cruce_cabify(xlsx: Path, rendidores: list[str], hoja_cabify: str = "Cabify Extracto",
#|                  hoja_auditoria: str = "Auditoria Cabify vs Rend"):
#|     """Cruce Talana→Cabify por nombre. Devuelve (cabify_df, {rendidor:(pasajeros, fuente)}, {pasajero_norm: rendidor})."""
#|     cab = pd.read_excel(xlsx, sheet_name=hoja_cabify)
#|     cab = cab[cab["Estado final"] != "No encontrado"].copy()
#|     cab["pn"] = cab["Pasajero"].map(P.norm)
#|     aud = pd.read_excel(xlsx, sheet_name=hoja_auditoria, header=1)
#|     manual = {}
#|     for _, a in aud[aud["¿Coincide en Cabify?"] == "SI"].iterrows():
#|         manual[P.norm(a["Nombre (origen: Talana)"])] = {P.norm(n) for n in str(a["Nombre (origen: Cabify)"]).split(";")}
#|     pas = set(cab["pn"])
#|     disp = cab.drop_duplicates("pn").set_index("pn")["Pasajero"].map(_txt).to_dict()
#|     match, pas2rend = {}, {}
#|     for rend in sorted(set(rendidores)):
#|         t = P.norm(rend)
#|         if t in manual:
#|             names, how = manual[t], "Auditoría Cabify vs Rend"
#|         else:
#|             tk = t.split()
#|             names = {p for p in pas if len(p.split()) >= 2 and tk and p.split()[0] == tk[0] and p.split()[-1] in tk[1:]}
#|             how = "Nombre + apellido" if names else ""
#|         names = names & pas
#|         if names:
#|             match[rend] = (sorted(disp[p] for p in names), how)
#|             for p in names:
#|                 pas2rend.setdefault(p, rend)
#|     return cab, match, pas2rend
#|
#|
#| def filas_rendiciones(estado: P.Estado, talana: dict, cab_match: dict) -> list[dict]:
#|     claves, rows = Claves(), []
#|     for res in estado.results:
#|         r = res["rendicion"]
#|         rid = r["id"]
#|         fecha = R.parse_date(r.get("fecha"))
#|         t = talana.get(rid, {})
#|         c_ceco = str(r["ceco"] or "")
#|         cod, _, nom = c_ceco.partition(" - ")
#|         prob, base_prob, detalle = estado.prob.get(rid, ("", "", ""))
#|         cm = cab_match.get(r["rendidor"])
#|         items = [it for it in res["items"] if it.get("monto")]
#|         first = True
#|         for it in items:
#|             concepto = it.get("concepto") or "OTROS"
#|             sub = P.subtipo_otros(it)
#|             generico = concepto == R.SIN_ID or sub in P.GENERICOS
#|             if concepto == R.SIN_ID:
#|                 cd = f"SIN_IDENTIFICAR · {P.motivo_sin(it)}"
#|             elif concepto == "OTROS":
#|                 cd = f"OTROS · {sub}"
#|             else:
#|                 cd = concepto
#|             if generico:
#|                 nivel = "Sin detalle · con concepto probable" if prob else "Sin detalle · sin pista"
#|             elif concepto == "OTROS":
#|                 nivel = "OTROS con subtipo"
#|             else:
#|                 nivel = "Concepto identificado"
#|             est = estimado(prob) if generico else concepto
#|             archivo = it.get("archivo") or ""
#|             fact, det_carp, pers = info_carpeta(archivo)
#|             fdoc = _fecha(it.get("fecha_doc"))
#|             alert_it, alert_r, obs = _txt(it.get("alertas")), _txt(res.get("alertas"))[:600], _txt(it.get("observacion"))
#|             key = (f"{rid}|cuadratura" if it.get("metodo") == "cuadratura" else
#|                    "|".join([rid, archivo, str(it.get("pagina") or ""), str(it.get("metodo") or ""),
#|                              P.norm(it.get("descripcion"))[:60], P.norm(it.get("emisor"))[:40],
#|                              str(round(it.get("monto_bruto") or 0))]))
#|             row = {
#|                 "Clave_Item": claves(key), "Tipo_Registro": TIPO_REG_REND, "ID_Rendicion": rid,
#|                 "Estado_Talana": _txt(r.get("estado")), "Estado_Lectura": res.get("estado", ""),
#|                 "Cuenta_Declarada": _txt(r.get("concepto_declarado")), "Accion_Solicitada": _txt(t.get("accion")),
#|                 "Tipo_Fondo": _txt(t.get("fondo")), "N_Requerimiento_Asociado": _txt(t.get("req")),
#|                 "Tipo_Documento_Declarado": _txt(t.get("tipo_doc")), "Periodo_Origen": _txt(t.get("periodo")),
#|                 "Fecha_Resolucion": _fecha(t.get("fres")), "Responsables_Aprobacion": _txt(t.get("resp")),
#|                 "CECO": c_ceco, "CECO_Codigo": cod.strip(), "CECO_Nombre": nom.strip(), "Rendidor": r["rendidor"],
#|                 "Rut_Rendidor": _txt(t.get("rut")), "Usuario_Cabify": r.get("usa_cabify") or "NO",
#|                 "Cabify_Pasajero_Match": "; ".join(cm[0]) if cm else "",
#|                 "Cabify_Match_Fuente": cm[1] if cm else "",
#|                 **_periodo_cols(fecha), "Fecha_Documento": fdoc,
#|                 "Dias_Doc_a_Rendicion": (fecha - fdoc).days if fecha and fdoc else None,
#|                 "Concepto": concepto, "Familia_Concepto": P.familia(concepto), "Concepto_Detallado": cd,
#|                 "Subtipo_OTROS": sub if concepto == "OTROS" else "", "Nivel_Detalle": nivel,
#|                 "Concepto_Probable": prob if generico else "", "Base_Probable": base_prob if generico and prob else "",
#|                 "Concepto_Estimado": est, "Detalle_Rendicion": detalle,
#|                 "Emisor": _txt(it.get("emisor")), "RUT_Emisor": _txt(it.get("rut_emisor")), "Folio": _txt(it.get("folio")),
#|                 "Tipo_Documento": _txt(it.get("tipo_documento")), "Descripcion": _txt(it.get("descripcion")),
#|                 "Palabra_Clave": _txt(it.get("palabra_clave")), "Metodo_Lectura": _txt(it.get("metodo")),
#|                 "Confianza": _txt(it.get("confianza")), "Archivo_Interno": archivo, "Tipo_Archivo": _txt(it.get("tipo_archivo")),
#|                 "Pagina": it.get("pagina"), "Facturable_Segun_Carpeta": fact, "Detalle_Carpeta": det_carp,
#|                 "Persona_Carpeta": pers, "Link_Adjunto": _txt(r.get("adjunto")),
#|                 "Monto_CLP": float(it["monto"]),
#|                 "Monto_Bruto_Leido_CLP": float(it["monto_bruto"]) if it.get("monto_bruto") is not None else None,
#|                 "Factor_Prorrateo": round(float(it.get("factor") or 1.0), 6),
#|                 "Declarado_Rendicion_CLP": float(r.get("monto_declarado") or 0) if first else None,
#|                 "Fila_Principal": 1 if first else 0,
#|                 "Alertas": alert_it, "Alertas_Rendicion": alert_r, "Observacion": obs,
#|                 "Alerta_Duplicado": "SI" if DUP_RX.search(f"{alert_it} {alert_r} {obs}") else "NO",
#|             }
#|             first = False
#|             rows.append(row)
#|         if first:   # rendición sin ningún ítem con monto: se deja una fila para que no desaparezca del conteo
#|             rows.append({
#|                 "Clave_Item": claves(f"{rid}|sin_items"), "Tipo_Registro": TIPO_REG_REND, "ID_Rendicion": rid,
#|                 "Estado_Talana": _txt(r.get("estado")), "Estado_Lectura": res.get("estado", ""),
#|                 "Cuenta_Declarada": _txt(r.get("concepto_declarado")), "Accion_Solicitada": _txt(t.get("accion")),
#|                 "Tipo_Fondo": _txt(t.get("fondo")), "N_Requerimiento_Asociado": _txt(t.get("req")),
#|                 "Tipo_Documento_Declarado": _txt(t.get("tipo_doc")), "Periodo_Origen": _txt(t.get("periodo")),
#|                 "Fecha_Resolucion": _fecha(t.get("fres")), "Responsables_Aprobacion": _txt(t.get("resp")),
#|                 "CECO": c_ceco, "CECO_Codigo": cod.strip(), "CECO_Nombre": nom.strip(), "Rendidor": r["rendidor"],
#|                 "Rut_Rendidor": _txt(t.get("rut")), "Usuario_Cabify": r.get("usa_cabify") or "NO",
#|                 **_periodo_cols(fecha), "Concepto": R.SIN_ID, "Familia_Concepto": P.familia(R.SIN_ID),
#|                 "Concepto_Detallado": "SIN_IDENTIFICAR · Rendición sin monto declarado", "Nivel_Detalle": "Sin detalle · sin pista",
#|                 "Concepto_Estimado": "SIN_IDENTIFICAR", "Detalle_Rendicion": detalle, "Link_Adjunto": _txt(r.get("adjunto")),
#|                 "Descripcion": "Rendición sin monto declarado ni ítems", "Metodo_Lectura": "cuadratura",
#|                 "Monto_CLP": 0.0, "Declarado_Rendicion_CLP": float(r.get("monto_declarado") or 0), "Fila_Principal": 1,
#|                 "Alerta_Duplicado": "NO"})
#|     return rows
#|
#|
#| def filas_cabify(cab: pd.DataFrame, pas2rend: dict, cecos_full: dict, claves: Claves) -> list[dict]:
#|     cab = cab.copy()
#|     cab["cod"] = cab["CECO"].astype(str).str.extract(r"(\d{3})")[0]
#|     cab = cab[cab["cod"].isin(cecos_full)]
#|     rows = []
#|     for _, x in cab.iterrows():
#|         try:
#|             mes = P.MESES_ES.index(str(x["Mes"]).strip().capitalize()) + 1
#|             fecha = dt.date(int(x["Año"]), mes, 1)
#|         except (ValueError, TypeError):
#|             continue
#|         cod = x["cod"]
#|         ceco = cecos_full[cod]
#|         pas = _txt(x["Pasajero"])
#|         rend = pas2rend.get(x["pn"])
#|         ticket = _txt(x.get("Código ticket"))
#|         rows.append({
#|             "Clave_Item": claves(f"CAB|{ticket}" if ticket else f"CAB|{cod}|{fecha}|{pas}|{x['Precio Fnal']}"),
#|             "Tipo_Registro": TIPO_REG_CAB, "CECO": ceco, "CECO_Codigo": cod, "CECO_Nombre": ceco.partition(" - ")[2],
#|             "Rendidor": rend or pas, "Usuario_Cabify": "SI", "Cabify_Pasajero_Match": pas if rend else "",
#|             "Cabify_Match_Fuente": "Cruce por nombre con Talana" if rend else "Sin match con Talana",
#|             **_periodo_cols(fecha), "Concepto": "APPS_TRANSPORTE", "Familia_Concepto": P.familia("APPS_TRANSPORTE"),
#|             "Concepto_Detallado": "APPS_TRANSPORTE · Cabify corporativo", "Nivel_Detalle": "Concepto identificado",
#|             "Concepto_Estimado": "APPS_TRANSPORTE", "Emisor": "Cabify (Maxi Mobility Chile)",
#|             "Descripcion": f"Viaje Cabify — {_txt(x['Estado final'])}", "Metodo_Lectura": "extracto_cabify",
#|             "Ticket_Cabify": ticket, "Pasajero_Cabify": pas, "Solicitante_Cabify": _txt(x["Solicitado por"]),
#|             "Vertical_Cabify": _txt(x["Vertical Solicitante"]), "Estado_Viaje_Cabify": _txt(x["Estado final"]),
#|             "Monto_CLP": 0.0, "Monto_Cabify_CLP": round(float(x["Precio Fnal"] or 0), 2), "Fila_Principal": 0,
#|             "Alerta_Duplicado": "NO"})
#|     return rows
#|
#|
#| # ======================================================================================
#| # SEGUIMIENTO DE FACTURACIÓN (se conserva entre ejecuciones)
#| # ======================================================================================
#| def aplicar_facturacion_previa(df: pd.DataFrame, previo: Path, salida: Path) -> tuple[pd.DataFrame, int, int]:
#|     df["Estado_Facturacion"] = "Sin clasificar"
#|     for c in TRACK[1:]:
#|         df[c] = None
#|     if not previo.exists():
#|         return df, 0, 0
#|     try:
#|         old = pd.read_excel(previo, sheet_name="Base", usecols=["Clave_Item"] + TRACK, dtype=object)
#|     except Exception as e:  # noqa: BLE001
#|         log.warning("No pude leer el seguimiento previo (%s): %s", previo.name, e)
#|         return df, 0, 0
#|     old = old[old["Clave_Item"].notna()].drop_duplicates("Clave_Item").set_index("Clave_Item")
#|     tiene = (old["Estado_Facturacion"].fillna("Sin clasificar") != "Sin clasificar") | \
#|         old[TRACK[1:]].notna().any(axis=1)
#|     old = old[tiene]
#|     if old.empty:
#|         return df, 0, 0
#|     idx = df.set_index("Clave_Item").index
#|     comunes = old.index.intersection(idx)
#|     d = df.set_index("Clave_Item")
#|     for c in TRACK:
#|         d.loc[comunes, c] = old.loc[comunes, c].values
#|     d["Estado_Facturacion"] = d["Estado_Facturacion"].fillna("Sin clasificar")
#|     huerf = old.drop(comunes)
#|     if len(huerf):
#|         huerf.reset_index().to_csv(salida / "facturacion_huerfanos.csv", index=False, encoding="utf-8-sig")
#|     return d.reset_index(), len(comunes), len(huerf)
#|
#|
#| # ======================================================================================
#| # SALIDAS
#| # ======================================================================================
#| def _como_date(v):
#|     """Cualquier valor de fecha (date, datetime, Timestamp, texto) -> date, o None."""
#|     if v is None:
#|         return None
#|     if isinstance(v, (dt.datetime, pd.Timestamp)):
#|         return None if pd.isna(v) else v.date()
#|     if isinstance(v, dt.date):
#|         return v
#|     if isinstance(v, str):
#|         try:
#|             return pd.to_datetime(v).date()
#|         except Exception:  # noqa: BLE001
#|             return None
#|     return None
#|
#|
#| def tipar(df: pd.DataFrame) -> pd.DataFrame:
#|     for c, t in TIPOS.items():
#|         if t == "fecha":
#|             df[c] = pd.to_datetime(df[c].map(_como_date), errors="coerce")
#|         elif t in ("moneda", "decimal"):
#|             df[c] = pd.to_numeric(df[c], errors="coerce")
#|         elif t == "entero":
#|             df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
#|         else:
#|             df[c] = df[c].where(df[c].notna(), "").astype(str)
#|     return df[COLS]
#|
#|
#| def conciliacion(df: pd.DataFrame, estado: P.Estado, extra: dict) -> list[tuple[str, object]]:
#|     rend = df[df["Tipo_Registro"] == TIPO_REG_REND]
#|     declarado = rend["Declarado_Rendicion_CLP"].sum()
#|     gasto = rend["Monto_CLP"].sum()
#|     por_rend = rend.groupby("ID_Rendicion").agg(g=("Monto_CLP", "sum"), d=("Declarado_Rendicion_CLP", "sum"))
#|     dif = por_rend[(por_rend["g"] - por_rend["d"]).abs() > 1]
#|     cab = df[df["Tipo_Registro"] == TIPO_REG_CAB]
#|     out = [("Fecha de generación", dt.datetime.now().strftime("%d-%m-%Y %H:%M")),
#|            ("Filas totales", len(df)), ("Filas de rendiciones", len(rend)), ("Rendiciones únicas", rend["ID_Rendicion"].nunique()),
#|            ("Gasto rendido (suma Monto_CLP)", round(gasto)), ("Declarado en Talana (suma 1ª fila)", round(declarado)),
#|            ("Diferencia gasto − declarado", round(gasto - declarado)),
#|            ("Rendiciones que no cuadran (|dif| > $1)", len(dif)),
#|            ("Viajes Cabify (filas)", len(cab)), ("Monto Cabify corporativo", round(cab["Monto_Cabify_CLP"].sum()))]
#|     out += [(k, v) for k, v in estado.stats.items()] + list(extra.items())
#|     return out
#|
#|
#| def escribir_xlsx(df: pd.DataFrame, path: Path, concil: list):
#|     import xlsxwriter
#|     wb = xlsxwriter.Workbook(str(path), {"nan_inf_to_errors": True, "strings_to_urls": False,
#|                                          "default_date_format": "yyyy-mm-dd"})
#|     f_hdr = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#1F4E78", "text_wrap": True, "valign": "vcenter"})
#|     f_trk = wb.add_format({"bold": True, "font_color": "black", "bg_color": "#FFC000", "text_wrap": True, "valign": "vcenter"})
#|     f_money = wb.add_format({"num_format": "#,##0"})
#|     f_dec = wb.add_format({"num_format": "0.000"})
#|     f_date = wb.add_format({"num_format": "yyyy-mm-dd"})
#|     f_bold = wb.add_format({"bold": True})
#|     f_wrap = wb.add_format({"text_wrap": True, "valign": "top"})
#|
#|     ws = wb.add_worksheet("Base")
#|     cols = []
#|     for c in COLS:
#|         t = TIPOS[c]
#|         cols.append({"header": c, "header_format": f_trk if c in TRACK else f_hdr,
#|                      "format": {"moneda": f_money, "decimal": f_dec, "fecha": f_date}.get(t)})
#|     datos = df.astype(object)
#|     for c in COLS:
#|         if TIPOS[c] == "fecha":
#|             datos[c] = df[c].dt.date
#|         elif TIPOS[c] == "entero":
#|             datos[c] = df[c].map(lambda v: None if pd.isna(v) else int(v))
#|
#|     def _limpio(v):
#|         return None if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and v != v) else v
#|     data = [[_limpio(v) for v in fila] for fila in datos.values.tolist()]
#|     ws.add_table(0, 0, len(df), len(COLS) - 1, {"name": "tblBase", "style": "Table Style Light 9", "columns": cols,
#|                                                 "data": data})
#|     anchos = {"Detalle_Rendicion": 60, "Descripcion": 50, "Emisor": 34, "Archivo_Interno": 50, "Link_Adjunto": 30,
#|               "Concepto_Detallado": 55, "Alertas": 40, "Alertas_Rendicion": 40, "Observacion": 45,
#|               "Responsables_Aprobacion": 30, "Obs_Facturacion": 40, "Cliente_Facturado": 24, "Detalle_Carpeta": 40,
#|               "Rendidor": 30, "CECO": 28, "Estado_Facturacion": 22, "Subtipo_OTROS": 40, "Cuenta_Declarada": 28}
#|     for i, c in enumerate(COLS):
#|         ws.set_column(i, i, anchos.get(c, 14))
#|     ws.set_row(0, 34)
#|     ws.freeze_panes(1, 3)
#|     i_est = COLS.index("Estado_Facturacion")
#|     ws.data_validation(1, i_est, len(df), i_est, {"validate": "list", "source": ESTADOS_FACT, "error_title": "Valor no válido",
#|                                                   "error_message": "Elige un estado de la lista."})
#|     i_ff = COLS.index("Fecha_Factura")
#|     ws.data_validation(1, i_ff, len(df), i_ff, {"validate": "date", "criteria": ">", "value": dt.date(2020, 1, 1),
#|                                                 "ignore_blank": True, "error_title": "Fecha no válida",
#|                                                 "error_message": "Ingresa una fecha (AAAA-MM-DD)."})
#|
#|     wd = wb.add_worksheet("Diccionario")
#|     for j, h in enumerate(["Columna", "Grupo", "Tipo", "Descripción"]):
#|         wd.write(0, j, h, f_hdr)
#|     for i, (n, g, t, d) in enumerate(COLUMNAS, 1):
#|         wd.write(i, 0, n, f_bold)
#|         wd.write(i, 1, g)
#|         wd.write(i, 2, t)
#|         wd.write(i, 3, d, f_wrap)
#|     wd.set_column(0, 0, 28)
#|     wd.set_column(1, 1, 26)
#|     wd.set_column(2, 2, 10)
#|     wd.set_column(3, 3, 120)
#|     wd.freeze_panes(1, 0)
#|
#|     wc = wb.add_worksheet("Conciliacion")
#|     wc.write(0, 0, "Control de la base", f_hdr)
#|     wc.write(0, 1, "Valor", f_hdr)
#|     for i, (k, v) in enumerate(concil, 1):
#|         wc.write(i, 0, k)
#|         wc.write(i, 1, v, f_money if isinstance(v, (int, float)) else None)
#|     r0 = len(concil) + 3
#|     wc.write(r0, 0, "Seguimiento de facturación (se recalcula al abrir)", f_bold)
#|     for j, h in enumerate(["Estado", "N° filas", "Gasto CLP"]):
#|         wc.write(r0 + 1, j, h, f_hdr)
#|     for i, e in enumerate(ESTADOS_FACT, r0 + 2):
#|         wc.write(i, 0, e)
#|         wc.write_formula(i, 1, f'=COUNTIFS(tblBase[Estado_Facturacion],A{i + 1},tblBase[Tipo_Registro],"{TIPO_REG_REND}")')
#|         wc.write_formula(i, 2, f'=SUMIFS(tblBase[Monto_CLP],tblBase[Estado_Facturacion],A{i + 1})', f_money)
#|     wc.set_column(0, 0, 58)
#|     wc.set_column(1, 2, 18)
#|     wb.close()
#|
#|
#| def main(argv=None):
#|     ap = argparse.ArgumentParser(description="Genera la base única (una fila por comprobante) para Power BI.")
#|     ap.add_argument("--excel", default="Presupuestos_Rendiciones_actualizado.xlsx")
#|     ap.add_argument("--salida", default="salida_powerbi")
#|     ap.add_argument("--lecturas", default="lecturas")
#|     ap.add_argument("--indice", default="revisar/indice.json")
#|     ap.add_argument("--pkl", default="resultados_base.pkl")
#|     ap.add_argument("--nombre", default="Base_Rendiciones_PowerBI")
#|     ap.add_argument("--reprocesar", action="store_true", help="vuelve a leer los adjuntos (usa la caché)")
#|     ap.add_argument("--hoja-rendiciones", default="Reembolsos Consolidado", help="hoja con las solicitudes de Talana")
#|     ap.add_argument("--hoja-cabify", default="Cabify Extracto", help="hoja con el extracto de viajes Cabify")
#|     ap.add_argument("--hoja-auditoria", default="Auditoria Cabify vs Rend", help="hoja con el cruce manual Talana↔Cabify")
#|     ap.add_argument("--sin-cabify", action="store_true", help="no incluye los viajes Cabify")
#|     ap.add_argument("--sin-parquet", action="store_true")
#|     ap.add_argument("--estado-pkl", default=None,
#|                     help="guarda/reutiliza el estado ya procesado (acelera ejecuciones repetidas al ajustar el formato)")
#|     a = ap.parse_args(argv)
#|     logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
#|
#|     xlsx, salida = Path(a.excel), Path(a.salida)
#|     if not xlsx.exists():
#|         sys.exit(f"No existe el Excel de entrada: {xlsx}")
#|     salida.mkdir(parents=True, exist_ok=True)
#|     maestro = salida / f"{a.nombre}.xlsx"
#|     cache = xlsx.parent / f"{xlsx.stem}_rpa_cache"
#|
#|     if a.estado_pkl and Path(a.estado_pkl).exists():
#|         log.info("Reutilizando estado procesado: %s (borra el archivo para recalcular)", a.estado_pkl)
#|         estado = pickle.load(open(a.estado_pkl, "rb"))
#|     else:
#|         results = P.cargar_resultados(xlsx, Path(a.pkl), a.reprocesar, a.hoja_rendiciones)
#|         lecturas, index = P.cargar_lecturas(Path(a.lecturas), Path(a.indice))
#|         log.info("Aplicando pipeline (%d rendiciones, %d con lectura manual)...", len(results), len(lecturas))
#|         estado = P.ejecutar_pipeline(results, lecturas, index, cache)
#|         if a.estado_pkl:
#|             pickle.dump(estado, open(a.estado_pkl, "wb"))
#|     results = estado.results
#|
#|     log.info("Leyendo datos de Talana...")
#|     talana = leer_talana(xlsx, a.hoja_rendiciones)
#|     rendidores = [r["rendicion"]["rendidor"] for r in results]
#|     cecos_full = {c.split(" - ")[0]: c for c in R.CONFIG["cecos_operacionales"]}
#|     cab, cab_match, pas2rend = (None, {}, {}) if a.sin_cabify else cruce_cabify(xlsx, rendidores, a.hoja_cabify, a.hoja_auditoria)
#|
#|     log.info("Construyendo filas...")
#|     rows = filas_rendiciones(estado, talana, cab_match)
#|     if cab is not None:
#|         rows += filas_cabify(cab, pas2rend, cecos_full, Claves())
#|     df = tipar(pd.DataFrame(rows, columns=COLS))
#|
#|     # seguimiento de facturación previo (conserva lo anotado) + respaldo del maestro anterior
#|     df, conservados, huerfanos = aplicar_facturacion_previa(df, maestro, salida)
#|     if maestro.exists():
#|         (salida / "historial").mkdir(exist_ok=True)
#|         shutil.copy2(maestro, salida / "historial" / f"{a.nombre}_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
#|     df = tipar(df)
#|
#|     extra = {"Registros con seguimiento de facturación conservado": conservados,
#|              "Registros de seguimiento sin equivalente (ver facturacion_huerfanos.csv)": huerfanos}
#|     concil = conciliacion(df, estado, extra)
#|     log.info("Escribiendo %s ...", maestro)
#|     escribir_xlsx(df, maestro, concil)
#|     df.to_csv(salida / f"{a.nombre}.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
#|     if not a.sin_parquet:
#|         df.to_parquet(salida / f"{a.nombre}.parquet", index=False)
#|
#|     print("\n=== BASE GENERADA ===")
#|     for k, v in concil[1:12]:
#|         print(f"  {k:<48} {v:>16,}".replace(",", ".") if isinstance(v, (int, float)) else f"  {k:<48} {v}")
#|     print(f"  Columnas: {len(COLS)} | Archivo maestro: {maestro}")
#|
#|
#| if __name__ == "__main__":
#|     main()
# <<< FIN generar_base_powerbi

# >>> MODULO ingesta_requerimientos
#| # -*- coding: utf-8 -*-
#| """Ingesta del libro "Centro de Requerimientos" (Talana): unifica las hojas de reembolso en una sola lista.
#|
#| - Toma todas las hojas cuyo nombre contenga "reembolso" (o las que se indiquen) y las unifica por nombre de campo.
#| - Agrega los datos de la hoja "Resumen" (razón social, centro de costo del cliente, jefatura, etapa, días sin movimiento).
#| - Entrega `Rendicion` (formato del RPA) + un diccionario de campos extra por ID para la base de Power BI.
#| """
#| from __future__ import annotations
#|
#| import datetime as dt
#| import re
#| from pathlib import Path
#|
#| import pandas as pd
#|
#| import pipeline_rendiciones as P
#| import rpa_rendiciones as R
#|
#| # campo destino → prefijos (normalizados) de columnas origen; el primero con valor gana
#| CAMPOS = {
#|     "rut": ["rut creador"], "rendidor": ["nombre creador"], "etapas": ["etapas canal"], "responsables_n": ["responsables"],
#|     "estado": ["estado"], "fecha_res": ["fecha resolucion"], "dias_res": ["tiempo de resolucion"],
#|     "rut_resp": ["rut responsable"], "resp": ["nombre(s) responsable"],
#|     "empresa": ["nombre de empresa"], "linea": ["seleccione la linea de negocio"],
#|     "proyecto_sel": ["indique el numero de proyecto"], "proyecto": ["escriba el numero de proyecto", "indicar n° de proyecto", "indicar n de proyecto"],
#|     "accion": ["seleccione que accion requiere"], "fondo": ["seleccione que desea sustentar"],
#|     "req": ["indicar el numero del requerimiento"], "monto": ["indique monto a sustentar"],
#|     "tipo_doc": ["indique el tipo de documento"], "ceco": ["indique el ceco"], "cuenta": ["indique cuenta contable"],
#|     "rut_asoc": ["rut del asociado"], "nom_asoc": ["nombre y apellido del asociado"],
#|     "estado_sol": ["indique estado de la solicitud"],
#| }
#| URL_PREFIJOS = ["adjuntar el archivo", "adjuntar captura", "adjuntar archivo"]
#|
#|
#| def _s(v) -> str:
#|     return "" if v is None or (not isinstance(v, str) and pd.isna(v)) or str(v).strip() in ("Sin información", "nan") else str(v).strip()
#|
#|
#| def _monto(v) -> float | None:
#|     t = re.sub(r"[^\d,.\-]", "", _s(v))
#|     if not t:
#|         return None
#|     if "," in t and "." in t:
#|         t = t.replace(".", "").replace(",", ".")
#|     elif t.count(".") > 1 or re.fullmatch(r"-?\d{1,3}\.\d{3}", t):
#|         t = t.replace(".", "")
#|     elif "," in t:
#|         t = t.replace(",", ".")
#|     try:
#|         return float(t)
#|     except ValueError:
#|         return None
#|
#|
#| def hojas_reembolso(libro: Path, patron: str = "reembolso") -> list[str]:
#|     return [s for s in pd.ExcelFile(libro).sheet_names if patron in P.norm(s)]
#|
#|
#| def leer_libro(libro: Path, hojas: list[str]):
#|     """Devuelve (rendiciones: list[Rendicion], extra: dict[id -> dict], urls: dict[id -> list[str]])."""
#|     resumen = pd.read_excel(libro, sheet_name="Resumen", dtype=str)
#|     resumen = resumen.drop_duplicates("ID").set_index("ID")
#|     rend, extra, urls = [], {}, {}
#|     for hoja in hojas:
#|         df = pd.read_excel(libro, sheet_name=hoja, dtype=str)
#|         norm_cols = {c: P.norm(re.sub(r"\.\d+$", "", c)) for c in df.columns}
#|         # motivos: la plantilla repite "Indicar motivo..." y "Otro motivo..." (etapa 1 y etapa 2)
#|         mot_cols = [c for c, n in norm_cols.items() if n.startswith("indicar motivo de aprobacion")]
#|         otro_cols = [c for c, n in norm_cols.items() if n.startswith("otro motivo de rechazo")]
#|         url_cols = [c for c, n in norm_cols.items() if any(n.startswith(p) for p in URL_PREFIJOS)]
#|         for _, row in df.iterrows():
#|             rid = _s(row.get("ID"))
#|             if not rid or rid in extra:
#|                 continue
#|             d = {}
#|             for k, prefs in CAMPOS.items():
#|                 col = next((c for c, n in norm_cols.items() if any(n.startswith(p) for p in prefs)), None)
#|                 d[k] = _s(row[col]) if col else ""
#|             d["motivo_e1"] = _s(row[mot_cols[0]]) if len(mot_cols) > 0 else ""
#|             d["motivo_e2"] = _s(row[mot_cols[1]]) if len(mot_cols) > 1 else ""
#|             d["otro_e1"] = _s(row[otro_cols[0]]) if len(otro_cols) > 0 else ""
#|             d["otro_e2"] = _s(row[otro_cols[1]]) if len(otro_cols) > 1 else ""
#|             us = []
#|             for c in url_cols:
#|                 v = _s(row[c])
#|                 if v.lower().startswith("http") and v not in us:
#|                     us.append(v)
#|             rs = resumen.loc[rid] if rid in resumen.index else None
#|             d.update({
#|                 "hoja": hoja, "n_adjuntos_links": len(us),
#|                 "razon_social": _s(rs["Razón Social"]) if rs is not None else "",
#|                 "cod_cc_cliente": _s(rs["Código Centro Costo"]) if rs is not None else "",
#|                 "cc_cliente": _s(rs["Centro Costo"]) if rs is not None else "",
#|                 "jefatura": _s(rs["Jefatura"]) if rs is not None else "",
#|                 "fecha_sol": _s(rs["Fecha"]) if rs is not None else "",
#|                 "canal": _s(rs["Canal"]) if rs is not None else hoja,
#|                 "etapa": _s(rs["Etapa en curso"]) if rs is not None else "",
#|                 "dias_sin_mov": _s(rs["Días que lleva sin movimiento"]) if rs is not None else "",
#|                 "estado_resumen": _s(rs["Estado"]) if rs is not None else "",
#|             })
#|             extra[rid], urls[rid] = d, us
#|             try:
#|                 f = R.parse_date(d["fecha_sol"] or d["fecha_res"])
#|             except Exception:  # noqa: BLE001
#|                 f = None
#|             rend.append(R.Rendicion(id=rid, rendidor=d["rendidor"], ceco=d["ceco"] or "Sin CECO",
#|                                     concepto_declarado=d["cuenta"], fecha=f, monto_declarado=_monto(d["monto"]),
#|                                     adjunto=us[0] if us else None, usa_cabify="NO", estado=d["estado"]))
#|     return rend, extra, urls
#|
#|
#| def _hoja_corta(h: str) -> str:
#|     """'6. (N) INT - Reembolso AGR PI' y '8. (N) INT - Reembolso AGR PI' son el mismo formulario en libros distintos."""
#|     return re.sub(r"^\s*\d+\.\s*", "", h).strip()
#|
#|
#| def leer_libros(libros: list[Path], hojas: list[str] | None = None):
#|     """Unifica varios libros y elimina duplicados por ID. Gana el registro del libro MÁS RECIENTE (estado más actualizado).
#|     Devuelve (rendiciones, extra, urls, informe) donde informe resume duplicados descartados."""
#|     cargados = []
#|     for lib in libros:
#|         lib = Path(lib)
#|         rend, extra, urls = leer_libro(lib, hojas or hojas_reembolso(lib))
#|         res = pd.read_excel(lib, sheet_name="Resumen", usecols=["Fecha"], dtype=str)["Fecha"].dropna()
#|         cargados.append((lib.stem, str(res.max()), rend, extra, urls))
#|     cargados.sort(key=lambda x: x[1], reverse=True)          # más reciente primero
#|     vistos: dict[str, dict] = {}
#|     rend_f, extra_f, urls_f, informe = [], {}, {}, []
#|     for nombre, fmax, rend, extra, urls in cargados:
#|         n_dup = 0
#|         for r in rend:
#|             d = extra[r.id]
#|             d["hoja"] = _hoja_corta(d["hoja"])
#|             if r.id in vistos:
#|                 n_dup += 1
#|                 vistos[r.id]["otros"].append(f"{nombre}: {d['estado']}")
#|                 continue
#|             d["libro"] = nombre
#|             vistos[r.id] = d
#|             d["otros"] = []
#|             rend_f.append(r); extra_f[r.id] = d; urls_f[r.id] = urls[r.id]
#|         informe.append((nombre, fmax[:10], len(rend), n_dup))
#|     return rend_f, extra_f, urls_f, informe
# <<< FIN ingesta_requerimientos

# >>> MODULO lectura_requerimientos
#| # -*- coding: utf-8 -*-
#| """Lectura automática (descarga + texto + OCR) de TODOS los adjuntos de las rendiciones del libro de requerimientos.
#| Reanudable: cada adjunto queda en la caché y no se vuelve a procesar."""
#| from __future__ import annotations
#|
#| import logging
#| import os
#| import pickle
#| import sys
#| import time
#| from concurrent.futures import ProcessPoolExecutor, as_completed
#| from dataclasses import replace
#| from pathlib import Path
#|
#| os.environ.setdefault("OMP_THREAD_LIMIT", "1")
#|
#| import ingesta_requerimientos as I  # noqa: E402
#| import rpa_rendiciones as R  # noqa: E402
#|
#| log = logging.getLogger("lectura_req")
#|
#|
#| _AN = None
#|
#|
#| def _init():
#|     global _AN
#|     os.environ["OMP_THREAD_LIMIT"] = "1"
#|     _AN = R.LocalAnalyzer(R.Classifier(R.CONFIG["conceptos"]))
#|
#|
#| def _trabajo(rr, cache):
#|     return R.process_one(rr, _AN, cache, False)
#|
#|
#| def huella_entrada(rend: list, urls: dict) -> str:
#|     import hashlib
#|     h = hashlib.sha1()
#|     for r in sorted(rend, key=lambda x: x.id):
#|         h.update(f"{r.id}|{'|'.join(urls.get(r.id, []))}".encode())
#|     return h.hexdigest()[:16]
#|
#|
#| def leer_adjuntos(rend: list, urls: dict, cache: Path, pkl: Path | None = None, workers: int | None = None,
#|                   fp: str = "") -> list[dict]:
#|     """`fp` identifica la entrada (ids + links); si el .pkl guardado es de otra entrada se vuelve a leer (usa la caché)."""
#|     sello = Path(f"{pkl}.fp") if pkl else None
#|     if pkl and Path(pkl).exists() and (not fp or (sello.exists() and sello.read_text().strip() == fp)):
#|         log.info("Resultados desde %s", pkl)
#|         return pickle.load(open(pkl, "rb"))
#|     trabajos = []                                  # (indice rendicion, Rendicion con un solo link)
#|     for i, r in enumerate(rend):
#|         for u in (urls.get(r.id) or [None]):
#|             trabajos.append((i, replace(r, adjunto=u)))
#|     out: dict[int, list] = {}
#|     t0, n = time.time(), len(trabajos)
#|     log.info("Procesando %d adjuntos de %d rendiciones (caché: %s)", n, len(rend), cache)
#|     with ProcessPoolExecutor(workers or int(os.environ.get("RPA_PROCS", 6)), initializer=_init) as ex:
#|         fut = {ex.submit(_trabajo, rr, cache): i for i, rr in trabajos}
#|         for k, f in enumerate(as_completed(fut), 1):
#|             try:
#|                 out.setdefault(fut[f], []).append(f.result())
#|             except Exception as e:  # noqa: BLE001
#|                 out.setdefault(fut[f], []).append({"rendicion": {}, "estado": "ERROR", "archivos": [], "items": [], "errores": [str(e)]})
#|             if k % 50 == 0 or k == n:
#|                 log.info("  %d/%d adjuntos (%.0f min)", k, n, (time.time() - t0) / 60)
#|     resultados = []
#|     for i, r in enumerate(rend):
#|         partes = out.get(i, [])
#|         base = partes[0]
#|         for p in partes[1:]:
#|             base["items"] += p["items"]; base["archivos"] += p["archivos"]; base["errores"] += p["errores"]
#|             if base["estado"] != "OK" and p["estado"] == "OK":
#|                 base["estado"] = "OK"
#|         base["rendicion"] = {**base.get("rendicion", {}), **{k: v for k, v in r.__dict__.items()}}
#|         resultados.append(base)
#|     if pkl:
#|         pickle.dump(resultados, open(pkl, "wb"))
#|         if fp:
#|             sello.write_text(fp)
#|     return resultados
#|
#|
#| if __name__ == "__main__":
#|     logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
#|     cache, pkl = Path(sys.argv[1]), Path(sys.argv[2])
#|     rend, extra, urls, _ = I.leer_libros([Path(x) for x in sys.argv[3:]])
#|     leer_adjuntos(rend, urls, cache, pkl, fp=huella_entrada(rend, urls))
#|     log.info("LISTO")
# <<< FIN lectura_requerimientos

# >>> MODULO generar_base_requerimientos
#| # -*- coding: utf-8 -*-
#| """
#| BASE ÚNICA PARA POWER BI — Rendiciones desde el libro "Centro de Requerimientos" (todas las hojas de reembolso)
#| ==============================================================================================================
#|     python generar_base_requerimientos.py --libro Centro_de_Requerimientos.xlsx --salida salida_req
#|
#| - Toma todas las hojas cuyo nombre contiene "reembolso" (o las indicadas con --hojas) y las unifica.
#| - Lee TODOS los adjuntos (PDF, imágenes con OCR, planillas, comprimidos, correos) y abre el gasto por comprobante y concepto.
#| - No incluye Cabify. Conserva el estado de cada solicitud (Abierto / En progreso / Finalizado / Rechazado, etapa, pagada…).
#| - Al final de la tabla quedan las columnas de seguimiento de facturación (se conservan al regenerar).
#| Reutiliza el motor de lectura y las reglas de clasificación de rpa_rendiciones.py / pipeline_rendiciones.py.
#| """
#| from __future__ import annotations
#|
#| import argparse
#| import datetime as dt
#| import logging
#| import os
#| import pickle
#| import re
#| import shutil
#| import sys
#| from pathlib import Path
#|
#| os.environ.setdefault("OMP_THREAD_LIMIT", "1")
#|
#| import pandas as pd  # noqa: E402
#|
#| import generar_base_powerbi as G  # noqa: E402
#| import ingesta_requerimientos as I  # noqa: E402
#| import lectura_requerimientos as L  # noqa: E402
#| import pipeline_rendiciones as P  # noqa: E402
#| import rpa_rendiciones as R  # noqa: E402
#|
#| log = logging.getLogger("base_req")
#|
#| # --------------------------------------------------------------------------- columnas
#| QUITAR = {"Usuario_Cabify", "Cabify_Pasajero_Match", "Cabify_Match_Fuente", "Ticket_Cabify", "Pasajero_Cabify",
#|           "Solicitante_Cabify", "Vertical_Cabify", "Estado_Viaje_Cabify", "Monto_Cabify_CLP", "Periodo_Origen", "Accion_Solicitada",
#|           "Tipo_Fondo", "N_Requerimiento_Asociado", "Tipo_Documento_Declarado"}
#| NUEVAS_SOLICITUD = [
#|     ("Libro_Origen", "Solicitud", "texto", "Libro del que se tomó el registro. Si el ID estaba en varios libros se usa el más reciente."),
#|     ("N_Libros_Donde_Aparece", "Solicitud", "entero", "En cuántos libros aparecía el ID (duplicados eliminados = valor − 1)."),
#|     ("Estado_En_Otros_Libros", "Solicitud", "texto", "Estado que tenía el ID en los libros más antiguos (ayuda a ver la evolución)."),
#|     ("Comparte_Adjunto_Con", "Calidad y auditoría", "texto", "Otros IDs con exactamente el mismo archivo adjunto (posible rendición duplicada)."),
#|     ("Hoja_Origen", "Solicitud", "texto", "Formulario/hoja de la que viene la solicitud (sin el número de hoja, que cambia entre libros)."),
#|     ("Canal", "Solicitud", "texto", "Canal/formulario de la solicitud según la hoja Resumen."),
#|     ("Etapa_En_Curso", "Solicitud", "texto", "Etapa del flujo en que está la solicitud (Inicial, Derivado a Etapa 2, Finalizado…)."),
#|     ("Estado_Solicitud", "Solicitud", "texto", "Estado final de la solicitud: Pagada / Aprobado para pago / Rechazada / vacío si sigue en curso."),
#|     ("Etapas_Canal", "Solicitud", "texto", "N° de etapas del canal."),
#|     ("Dias_Sin_Movimiento", "Solicitud", "entero", "Días que lleva sin movimiento la solicitud (hoja Resumen)."),
#|     ("Tiempo_Resolucion_Dias", "Solicitud", "entero", "Días que tardó en resolverse."),
#|     ("Accion_Solicitada", "Solicitud", "texto", "Acción pedida (solicitar reembolso, cerrar fondo…)."),
#|     ("Tipo_Fondo", "Solicitud", "texto", "Qué se sustenta o reembolsa (Fondo Fijo, Otros gastos…)."),
#|     ("N_Requerimiento_Asociado", "Solicitud", "texto", "Requerimiento al que se asocia (p. ej. el fondo que se rinde)."),
#|     ("Tipo_Documento_Declarado", "Solicitud", "texto", "Tipo de documento que dijo adjuntar el rendidor (boleta, factura, otros…)."),
#|     ("N_Proyecto", "Cliente / facturación", "texto", "N° de proyecto declarado en la solicitud (clave para refacturar)."),
#|     ("Linea_Negocio", "Cliente / facturación", "texto", "Línea de negocio declarada."),
#|     ("Empresa_Cliente", "Cliente / facturación", "texto", "Empresa/cliente declarado en el formulario."),
#|     ("Razon_Social", "Cliente / facturación", "texto", "Razón social que emite (hoja Resumen)."),
#|     ("Cod_CC_Cliente", "Cliente / facturación", "texto", "Código del centro de costo del cliente (hoja Resumen)."),
#|     ("Centro_Costo_Cliente", "Cliente / facturación", "texto", "Centro de costo del cliente (hoja Resumen). Distinto del CECO interno."),
#|     ("RUT_Asociado", "Cliente / facturación", "texto", "RUT del asociado al que corresponde el gasto (hojas Trade/Vivo)."),
#|     ("Nombre_Asociado", "Cliente / facturación", "texto", "Nombre del asociado al que corresponde el gasto."),
#|     ("Jefatura_RUT", "Organización", "texto", "RUT de la jefatura del solicitante."),
#|     ("Motivo_Etapa1", "Revisión", "texto", "Motivo de aprobación/rechazo de la primera revisión."),
#|     ("Motivo_Etapa2", "Revisión", "texto", "Motivo de aprobación/rechazo de la segunda revisión (incluye 'Aprobado - Refacturable')."),
#|     ("Detalle_Rechazo", "Revisión", "texto", "Texto libre del motivo de rechazo, si lo hay."),
#|     ("Refacturable_Segun_Aprobador", "Revisión", "texto", "SI si el aprobador marcó el gasto como refacturable; NO si lo marcó no refacturable; vacío si no se pronunció."),
#|     ("Facturable_Sugerido", "Revisión", "texto", "SUGERENCIA para tu validación: FACTURABLE / NO FACTURABLE / POR REVISAR (aprobador > carpeta > proyecto declarado)."),
#|     ("Fuente_Sugerencia", "Revisión", "texto", "De dónde sale la sugerencia de Facturable_Sugerido."),
#|     ("Link_Adjunto_2", "Adjunto", "texto", "Segundo link de adjunto de la solicitud (captura de pantalla / archivo adicional), si existe."),
#|     ("N_Links_Adjuntos", "Adjunto", "entero", "N° de links de adjuntos de la solicitud."),
#|     ("Nombre_Adjunto", "Adjunto", "texto", "Nombre del archivo adjunto principal (parte final del link)."),
#|     ("Extension_Adjunto", "Adjunto", "texto", "Tipo del archivo subido: pdf, jpg, zip, rar, xlsx…"),
#|     ("N_Archivos_En_Adjunto", "Adjunto", "entero", "Archivos que contenía el adjunto (si es comprimido)."),
#|     ("Resumen_Tipos_Adjunto", "Adjunto", "texto", "Composición del adjunto, ej. '3 jpg, 1 pdf'."),
#|     ("Clase_Adjunto", "Adjunto", "texto", "Clase del comprobante de la fila: Imagen (OCR) / PDF con texto / PDF escaneado (OCR) / Planilla / Correo / Word / Lectura manual / Sin adjunto…"),
#|     ("Nombre_Archivo", "Adjunto", "texto", "Nombre del archivo de la fila (sin carpetas)."),
#|     ("Carpeta_Interna", "Adjunto", "texto", "Carpeta o comprimido que contiene el archivo de la fila."),
#| ]
#| COLUMNAS = []
#| _nuevas_ins = False
#| for c in G.COLUMNAS:
#|     if c[0] in QUITAR:
#|         continue
#|     if c[1] == "Organización" and not _nuevas_ins:
#|         COLUMNAS += NUEVAS_SOLICITUD
#|         _nuevas_ins = True
#|     COLUMNAS.append(c)
#| COLS = [c[0] for c in COLUMNAS]
#| TIPOS = {c[0]: c[2] for c in COLUMNAS}
#| G.COLUMNAS, G.COLS, G.TIPOS = COLUMNAS, COLS, TIPOS
#| G.TRACK = [c[0] for c in COLUMNAS if c[1] == "Seguimiento de facturación"]
#| EXT_COMPR = {"zip", "rar", "7z"}
#|
#|
#| def _ext(nombre: str) -> str:
#|     m = re.search(r"\.([A-Za-z0-9]{2,5})(?:\?|$)", nombre or "")
#|     return m.group(1).lower() if m else ""
#|
#|
#| def clase(tipo_archivo: str, metodo: str, estado_lectura: str) -> str:
#|     t, m = (tipo_archivo or "").lower(), (metodo or "").lower()
#|     if "manual" in m:
#|         return "Lectura manual (imagen/PDF revisado)"
#|     if m == "cuadratura":
#|         return "Cuadratura (sin comprobante legible)"
#|     if t == "pdf":
#|         return "PDF escaneado (OCR)" if "ocr" in m else "PDF con texto"
#|     if t in ("jpg", "jpeg", "png", "webp", "heic", "gif", "bmp", "tif", "tiff"):
#|         return "Imagen / foto (OCR)"
#|     if t in ("xls", "xlsx", "xlsm", "csv", "ods") or "planilla" in m:
#|         return "Planilla Excel"
#|     if t in ("doc", "docx"):
#|         return "Documento Word"
#|     if t in ("msg", "eml"):
#|         return "Correo"
#|     if t == "txt":
#|         return "Texto"
#|     return t or ("Sin adjunto" if estado_lectura == "SIN ADJUNTO" else "Otro")
#|
#|
#| def huella(libros: list[Path], *extra: Path) -> str:
#|     """Identifica la entrada (libros + reglas + lecturas manuales) para no reutilizar una caché obsoleta."""
#|     import hashlib
#|     h = hashlib.sha1()
#|     for f in sorted(libros):
#|         st = Path(f).stat()
#|         h.update(f"{Path(f).name}|{st.st_size}|{int(st.st_mtime)}".encode())
#|     for f in (Path(P.__file__), Path(R.__file__), Path(__file__), Path(I.__file__)):
#|         h.update(f.read_bytes())
#|     for d in extra:
#|         if Path(d).is_dir():
#|             for f in sorted(Path(d).glob("*.json")):
#|                 h.update(f.read_bytes())
#|     return h.hexdigest()[:16]
#|
#|
#| def cargar_estado(pkl: Path, fp: str):
#|     """Estado procesado si existe y corresponde a la misma entrada; si no, None."""
#|     sello = Path(f"{pkl}.fp")
#|     if not pkl.exists():
#|         return None
#|     if not sello.exists() or sello.read_text().strip() != fp:
#|         log.warning("%s es de otra entrada o versión del código: se recalcula", pkl.name)
#|         return None
#|     return pickle.load(open(pkl, "rb"))
#|
#|
#| def _int(v):
#|     try:
#|         return int(float(v))
#|     except (TypeError, ValueError):
#|         return None
#|
#|
#| def sugerencia(refact: str, carpeta: str, proyecto: str) -> tuple[str, str]:
#|     if refact == "SI":
#|         return "FACTURABLE", "Aprobador marcó refacturable"
#|     if refact == "NO":
#|         return "NO FACTURABLE", "Aprobador marcó no refacturable"
#|     if carpeta:
#|         return carpeta, "Nombre de la carpeta del adjunto"
#|     return "POR REVISAR", "Sin pista de facturación" + (" (tiene N° de proyecto)" if proyecto else "")
#|
#|
#| def enriquecer(rows: list[dict], extra: dict, urls: dict, res_by_id: dict):
#|     # solicitudes con monto declarado 0: una lectura OCR no puede crear gasto → se deja 0 y se alerta
#|     decl = {r["ID_Rendicion"]: r["Declarado_Rendicion_CLP"] for r in rows if r.get("Fila_Principal") == 1}
#|     for r in rows:
#|         if (decl.get(r["ID_Rendicion"]) or 0) == 0 and (r.get("Monto_CLP") or 0) > 0:
#|             r["Monto_CLP"] = 0.0
#|             r["Alertas_Rendicion"] = ((r.get("Alertas_Rendicion") or "") + " Monto declarado = 0: lectura no cuadrada al gasto.").strip()
#|     por_hash: dict[str, set] = {}
#|     for i, x in res_by_id.items():
#|         if x.get("hash_adjunto"):
#|             por_hash.setdefault(x["hash_adjunto"], set()).add(i)
#|     comparte = {i: ", ".join(sorted(g - {i})) for g in por_hash.values() if len(g) > 1 for i in g}
#|     for r in rows:
#|         d, u = extra.get(r["ID_Rendicion"], {}), urls.get(r["ID_Rendicion"], [])
#|         res = res_by_id.get(r["ID_Rendicion"], {})
#|         arch = res.get("archivos") or []
#|         tipos = pd.Series([a.get("tipo") for a in arch]).value_counts() if arch else pd.Series(dtype=int)
#|         nombre_adj = (u[0].rsplit("/", 1)[-1] if u else "")
#|         from urllib.parse import unquote
#|         nombre_adj = unquote(nombre_adj)
#|         m2 = (d.get("motivo_e2") or "") + " " + (d.get("motivo_e1") or "")
#|         refact = "NO" if re.search(r"no\s+refacturable", m2, re.I) else ("SI" if re.search(r"refacturable", m2, re.I) else "")
#|         fact_sug, fuente = sugerencia(refact, r.get("Facturable_Segun_Carpeta", ""), d.get("proyecto", "") or d.get("proyecto_sel", ""))
#|         interno = r.get("Archivo_Interno") or ""
#|         r.update({
#|             "Libro_Origen": d.get("libro", ""), "N_Libros_Donde_Aparece": 1 + len(d.get("otros", [])),
#|             "Estado_En_Otros_Libros": "; ".join(d.get("otros", [])), "Comparte_Adjunto_Con": comparte.get(r["ID_Rendicion"], ""),
#|             "Hoja_Origen": d.get("hoja", ""), "Canal": d.get("canal", ""), "Etapa_En_Curso": d.get("etapa", ""),
#|             "Estado_Solicitud": d.get("estado_sol", ""), "Etapas_Canal": d.get("etapas", ""),
#|             "Dias_Sin_Movimiento": _int(d.get("dias_sin_mov")), "Tiempo_Resolucion_Dias": _int(d.get("dias_res")),
#|             "Accion_Solicitada": d.get("accion", ""), "Tipo_Fondo": d.get("fondo", ""),
#|             "N_Requerimiento_Asociado": d.get("req", ""), "Tipo_Documento_Declarado": d.get("tipo_doc", ""),
#|             "N_Proyecto": d.get("proyecto", "") or d.get("proyecto_sel", ""), "Linea_Negocio": d.get("linea", ""),
#|             "Empresa_Cliente": d.get("empresa", ""), "Razon_Social": d.get("razon_social", ""),
#|             "Cod_CC_Cliente": d.get("cod_cc_cliente", ""), "Centro_Costo_Cliente": d.get("cc_cliente", ""),
#|             "RUT_Asociado": d.get("rut_asoc", ""), "Nombre_Asociado": d.get("nom_asoc", ""),
#|             "Jefatura_RUT": d.get("jefatura", ""), "Motivo_Etapa1": d.get("motivo_e1", ""), "Motivo_Etapa2": d.get("motivo_e2", ""),
#|             "Detalle_Rechazo": " | ".join(x for x in (d.get("otro_e1", ""), d.get("otro_e2", "")) if x),
#|             "Refacturable_Segun_Aprobador": refact, "Facturable_Sugerido": fact_sug, "Fuente_Sugerencia": fuente,
#|             "Link_Adjunto_2": u[1] if len(u) > 1 else "", "N_Links_Adjuntos": len(u), "Nombre_Adjunto": nombre_adj,
#|             "Extension_Adjunto": _ext(nombre_adj), "N_Archivos_En_Adjunto": len(arch),
#|             "Resumen_Tipos_Adjunto": ", ".join(f"{n} {t}" for t, n in tipos.items()) if len(tipos) else "",
#|             "Clase_Adjunto": clase(r.get("Tipo_Archivo"), r.get("Metodo_Lectura"), r.get("Estado_Lectura")),
#|             "Nombre_Archivo": interno.split(">")[-1].strip().rsplit("/", 1)[-1] if interno else "",
#|             "Carpeta_Interna": interno.rsplit(">", 1)[0].strip() if ">" in interno else "",
#|             "Rut_Rendidor": d.get("rut", ""), "Fecha_Resolucion": d.get("fecha_res") or None,
#|             "Responsables_Aprobacion": d.get("resp", ""),
#|         })
#|         r["Estado_Talana"] = d.get("estado", r.get("Estado_Talana", ""))
#|         r["Tipo_Registro"] = G.TIPO_REG_REND
#|
#|
#| def conciliacion(df: pd.DataFrame, estado: P.Estado, extra: dict) -> list:
#|     declarado = df["Declarado_Rendicion_CLP"].sum()
#|     gasto = df["Monto_CLP"].sum()
#|     por = df.groupby("ID_Rendicion").agg(g=("Monto_CLP", "sum"), d=("Declarado_Rendicion_CLP", "sum"))
#|     out = [("Fecha de generación", dt.datetime.now().strftime("%d-%m-%Y %H:%M")), ("Filas totales", len(df)),
#|            ("Rendiciones únicas", df["ID_Rendicion"].nunique()), ("Gasto rendido (suma Monto_CLP)", round(gasto)),
#|            ("Declarado en Talana (suma 1ª fila)", round(declarado)), ("Diferencia gasto − declarado", round(gasto - declarado)),
#|            ("Rendiciones que no cuadran (|dif| > $1)", int(((por.g - por.d).abs() > 1).sum()))]
#|     for est, g in df.groupby("Estado_Talana")["Monto_CLP"].sum().items():
#|         out.append((f"Gasto con estado «{est}»", round(g)))
#|     return out + list(estado.stats.items()) + list(extra.items())
#|
#|
#| def main(argv=None):
#|     ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
#|     ap.add_argument("--libros", nargs="+", required=True, help="uno o más Excel 'Centro de Requerimientos' (se eliminan duplicados por ID)")
#|     ap.add_argument("--hojas", nargs="*", help="hojas a analizar (por defecto: todas las que contienen 'reembolso')")
#|     ap.add_argument("--salida", default="salida_req")
#|     ap.add_argument("--nombre", default="Base_Rendiciones_Requerimientos")
#|     ap.add_argument("--cache", default=None, help="carpeta de caché de adjuntos (se reutiliza entre libros)")
#|     ap.add_argument("--lecturas", default="lecturas")
#|     ap.add_argument("--indice", default="revisar/indice.json")
#|     ap.add_argument("--pkl", default=None, help="resultados de la lectura de adjuntos")
#|     ap.add_argument("--estado-pkl", default=None)
#|     ap.add_argument("--sin-parquet", action="store_true")
#|     a = ap.parse_args(argv)
#|     logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
#|
#|     libros, salida = [Path(x) for x in a.libros], Path(a.salida)
#|     salida.mkdir(parents=True, exist_ok=True)
#|     cache = Path(a.cache) if a.cache else libros[0].parent / "cache_adjuntos"
#|     rend, extra, urls, informe = I.leer_libros(libros, a.hojas)
#|     for nombre, fmax, n, dup in informe:
#|         log.info("Libro %-40s (hasta %s): %5d solicitudes de reembolso, %5d duplicadas descartadas", nombre, fmax, n, dup)
#|     maestro = salida / f"{a.nombre}.xlsx"
#|
#|     fp = huella(libros, Path(a.lecturas))
#|     estado = cargar_estado(Path(a.estado_pkl), fp) if a.estado_pkl else None
#|     if estado is None:
#|         results = L.leer_adjuntos(rend, urls, cache, Path(a.pkl) if a.pkl else None, fp=L.huella_entrada(rend, urls))
#|         for res, r in zip(results, rend):                      # datos de la solicitud siempre desde el libro
#|             res["rendicion"] = {**r.__dict__}
#|         lecturas, index = P.cargar_lecturas(Path(a.lecturas), Path(a.indice))
#|         ids = {r.id for r in rend}
#|         lecturas = {k: v for k, v in lecturas.items() if k in ids}
#|         log.info("Pipeline: %d rendiciones, %d con lectura manual previa", len(results), len(lecturas))
#|         estado = P.ejecutar_pipeline(results, lecturas, index, cache)
#|         if a.estado_pkl:
#|             pickle.dump(estado, open(a.estado_pkl, "wb"))
#|             Path(f"{a.estado_pkl}.fp").write_text(fp)
#|
#|     talana = {i: {"rut": d["rut"], "accion": d["accion"], "fondo": d["fondo"], "req": d["req"], "tipo_doc": d["tipo_doc"],
#|                   "periodo": "", "fres": d["fecha_res"], "resp": d["resp"]} for i, d in extra.items()}
#|     rows = G.filas_rendiciones(estado, talana, {})
#|     enriquecer(rows, extra, urls, {x["rendicion"]["id"]: x for x in estado.results})
#|     df = G.tipar(pd.DataFrame(rows, columns=COLS))
#|     df, conservados, huerfanos = G.aplicar_facturacion_previa(df, maestro, salida)
#|     if maestro.exists():
#|         (salida / "historial").mkdir(exist_ok=True)
#|         shutil.copy2(maestro, salida / "historial" / f"{a.nombre}_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
#|     df = G.tipar(df)
#|     concil = conciliacion(df, estado, {**{f"Libro «{n}»: solicitudes leídas / duplicados descartados": f"{k} / {d}" for n, _, k, d in informe},
#|                                        "Registros con seguimiento de facturación conservado": conservados,
#|                                        "Seguimiento sin equivalente (ver facturacion_huerfanos.csv)": huerfanos})
#|     G.escribir_xlsx(df, maestro, concil)
#|     df.to_csv(salida / f"{a.nombre}.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
#|     if not a.sin_parquet:
#|         try:
#|             df.to_parquet(salida / f"{a.nombre}.parquet", index=False)
#|         except Exception as e:  # noqa: BLE001
#|             log.warning("Sin parquet: %s", e)
#|     print("\n=== BASE GENERADA ===")
#|     for k, v in concil:
#|         print(f"  {k:<52} {v:>16,}".replace(",", ".") if isinstance(v, (int, float)) else f"  {k:<52} {v}")
#|     print(f"  Columnas: {len(COLS)} | Archivo maestro: {maestro}")
#|
#|
#| if __name__ == "__main__":
#|     main()
# <<< FIN generar_base_requerimientos

