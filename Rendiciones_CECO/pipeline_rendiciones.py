# -*- coding: utf-8 -*-
"""
PIPELINE DE RENDICIONES — lógica compartida (lectura de adjuntos → conceptos → cuadratura con Talana)
=====================================================================================================
Lo usan `generar_base_powerbi.py` (base única para Power BI) y `post_etapa2.py` (Excel de análisis).

Etapas, en orden (el orden importa y se mantiene idéntico al análisis ya entregado):
  1. lecturas manuales   -> comprobantes leídos a mano (lecturas/lote_*.json) reemplazan lo que el OCR no resolvió
  2. depuración          -> corrige montos de OCR dudosos y reclasifica OTROS con diccionarios (ES/EN)
  3. re-lectura planillas-> planillas Excel donde el parser tomó la columna equivocada (ej. "Valor por Km")
  4. formularios         -> "Formulario de rendición" interno = detalle oficial de la rendición
  5. cuadratura          -> auditoría + SIN_IDENTIFICAR por la diferencia contra lo declarado en Talana
  6. prorrateo           -> si lo leído supera lo declarado, se escala para que los totales cuadren con Talana
  7. probables           -> concepto probable y detalle (carpetas/correos) para lo que sigue sin detalle
"""
from __future__ import annotations

import glob
import json
import logging
import pickle
import re as _re
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import rpa_rendiciones as R

log = logging.getLogger("pipeline")

CONCEPTOS_OK = {"APPS_TRANSPORTE", "COMBUSTIBLE", "ESTACIONAMIENTO", "PEAJE_TAG", "TAXI_COLECTIVO",
                "TRANSPORTE_PUBLICO", "BUS_INTERURBANO", "PASAJE_AEREO", "ALOJAMIENTO", "ALIMENTACION",
                "UTILES_OFICINA", "NOTARIA", "COURIER", "FERRETERIA_MANTENCION", "SUPERMERCADO", "FARMACIA", "OTROS"}
MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
            "Noviembre", "Diciembre"]
FAMILIAS = {
    "Movilización": {"APPS_TRANSPORTE", "COMBUSTIBLE", "ESTACIONAMIENTO", "PEAJE_TAG", "TAXI_COLECTIVO",
                     "TRANSPORTE_PUBLICO", "BUS_INTERURBANO"},
    "Viaje (aéreo y alojamiento)": {"PASAJE_AEREO", "ALOJAMIENTO"},
    "Alimentación": {"ALIMENTACION", "SUPERMERCADO"},
    "Oficina y operación": {"UTILES_OFICINA", "FERRETERIA_MANTENCION", "COURIER", "NOTARIA", "FARMACIA"},
}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").split())


def familia(concepto: str) -> str:
    for fam, cs in FAMILIAS.items():
        if concepto in cs:
            return fam
    return "Sin detalle" if concepto == R.SIN_ID else "OTROS (ver subtipo)"


# ======================================================================================
# REGLAS (copiadas textualmente del análisis entregado; no modificar sin re-validar)
# ======================================================================================
EXTRA = [  # emisor/descripción -> concepto, para ítems automáticos que quedaron en OTROS
    ("TRANSPORTE_PUBLICO", ["tarjeta bip", "carga bip", "recarga bip", "compra tarjeta bip", "metro", "red movilidad",
                            "biotren", "merval"]),
    ("TAXI_COLECTIVO", ["taxi", "transvip", "radio taxi", "colectivo", "traslado taxi"]),
    ("ALOJAMIENTO", ["hospedaje", "alojamiento", "hostal", "hotel", "cabana", "cabanas", "apart hotel"]),
    ("PASAJE_AEREO", ["pasaje aereo", "vuelo", "latam", "sky airline", "jetsmart", "flight", "airline"]),
    ("UTILES_OFICINA", ["impresiones", "impresion", "anillado", "fotocopias", "fotocopia", "office supplies",
                        "papel", "tinta", "toner", "libro de asistencia", "carpetas", "archivador"]),
    ("ALIMENTACION", ["cena", "almuerzo", "desayuno", "bebestibles", "snack", "snacks", "agua mineral",
                      "office water", "water", "cake", "torta", "pastel", "comida", "food", "lunch", "dinner",
                      "breakfast", "coffee", "cafe", "galletas", "bebidas"]),
    ("PEAJE_TAG", ["peajes", "tag", "pase diario", "toll", "tolls", "road roll", "road toll", "highway"]),
    ("PASAJE_AEREO", ["avion", "boletos de avion", "pasaje avion", "pasajes avion", "vuelo", "vuelos", "flight", "flights", "airfare", "air ticket", "aereo"]),
    ("TAXI_COLECTIVO", ["traslado aeropuerto", "traslado casa aeropuerto", "aeropuerto casa", "ses aeropuerto"]),
    ("ALOJAMIENTO", ["estadia", "noches", "lodging", "accommodation", "holiday inn", "hilton", "marriott", "ibis", "novotel"]),
    ("COMBUSTIBLE", ["fuel", "gas station", "gasoline", "combustible"]),
    ("ESTACIONAMIENTO", ["parking", "estacionamiento", "estacionamientos"]),
    ("APPS_TRANSPORTE", ["ride", "rides", "road to", "uber home", "trip with uber", "trip via uber", "traslado reunion",
                         "traslados"]),
    ("ALIMENTACION", ["lunch", "dinner", "meal", "meals", "drinks", "restaurant", "coffee with", "food"]),
    ("COMBUSTIBLE", ["rendicion km", "kilometraje", "km recorridos", "rendicion_km"]),
    ("ALIMENTACION", ["uber eats", "opcion para llevar", "entregas", "banquete", "banquetes", "catering",
                      "coffee break", "boleta alimentacion", "alimentacion", "consumo", "colacion"]),
    ("APPS_TRANSPORTE", ["uber", "uberx", "uber black", "cabify", "didi", "indrive"]),
    ("UTILES_OFICINA", ["materiales de escritorio", "articulos de oficina"]),
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
EXTRA_RX = [(c, kw, _re.compile(rf"(?<![a-z0-9]){_re.escape(kw)}(?![a-z0-9])")) for c, kws in EXTRA for kw in kws]


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


def _es_total(txt):
    return _re.search(r"total\s*(rendicion|rendido|general|a rendir)?\s*$", txt) is not None


def _lee_formulario(f):
    """Filas del formulario (1ª hoja con 'TOTAL RENDICION'): [(fecha, descripcion, monto)] o None."""
    for u in R.extract(f):
        if not u.filas:
            continue
        txts = [" ".join(R.normalize(v) for v in fila if isinstance(v, str)) for fila in u.filas]
        tot_i = next((i for i, t in enumerate(txts) if "total rendicion" in t or "total rendido" in t), None)
        if tot_i is None:
            continue
        rows = []
        for fila in u.filas[:tot_i]:
            nums = [R.parse_amount(v) for v in fila if isinstance(v, (int, float)) and not isinstance(v, bool)]
            nums = [v for v in nums if v and v >= 100]
            strs = [str(v).strip() for v in fila if isinstance(v, str) and len(str(v).strip()) > 2
                    and not str(v).strip().replace(".", "").replace("-", "").isdigit()]
            if not nums or not strs:
                continue
            desc = max(strs, key=len)
            if R.normalize(desc) in ("clp", "usd", "bol", "boleta", "factura", "adjunto"):
                continue
            fch = next((v for v in fila if hasattr(v, "year")), None)
            rows.append((fch, desc, nums[-1]))
        if rows:
            return rows
    return None


def clasifica_texto(desc):
    t = R.normalize(desc)
    for c, kw, rx in EXTRA_RX:
        if rx.search(t):
            return c, f"formulario:{kw}"
    c, kw = R.Classifier(R.CONFIG["conceptos"]).classify(desc)
    return c, (f"formulario:{kw}" if kw else "")


def subtipo_otros(it):
    if it.get("concepto") != "OTROS":
        return ""
    raw = f"{it.get('emisor') or ''} {it.get('descripcion') or ''}"
    t = R.normalize(raw)
    for lab, kws in [
            ("Arriendo cowork / oficina (botón de pago)", ["comprobante de pago nro", "cowork", "martin bianchi",
                                                          "gilbert hermosilla", "arriendo oficina"]),
            ("Publicidad digital / agencias (Meta Ads, grabaciones)", ["manpower ads", "meta anuncios", "facebook",
                                                                      "recording session", "agency", "agencia"]),
            ("Telefonía / internet (SIM, planes, wifi)", ["movistar", "entel", "claro", "wom", "sim", "post paid",
                                                         "plan movil", "telecomunicaciones", "telefonico", "telefonia",
                                                         "telefono", "internet", "wifi", "cel"]),
            ("Actividades internas / team building / celebraciones",
             ["team building", "after office", "birthday", "cumpleanos", "baby shower", "end of month activity",
              "navidad", "afternoon tea", "actividad equipo", "salida equipo", "celebracion", "aniversario",
              "fiesta", "office monthly"]),
            ("Transferencia a persona (sin boleta)", ["transferencia", "nombre pagador", "team honor", "transferir"]),
            ("Premios / regalos / gift cards / bonos", ["gift", "premio", "regalo", "tarjeta regalo", "concurso", "bono"]),
            ("Transporte de personal contratado", ["servicio de transporte", "transporte de pasajeros",
                                                   "transportes rojas"]),
            ("Arriendo de vehículo", ["car rental", "rent a car", "econorent", "arriendo vehiculo", "arriendo auto"]),
            ("Equipamiento / tecnología", ["monitor", "moniters", "notebook", "mouse", "teclado", "starlink",
                                           "compresores", "celular nuevo", "audifonos", "impresora"]),
            ("Salud / vacunas / bienestar", ["vacuna", "saluvac", "employee care", "emploee care", "examen", "clinica",
                                             "mutual"]),
            ("Suscripciones / inscripciones / licencias", ["inscripcion", "suscripcion", "uptodate", "licencia",
                                                          "mercadopublico", "membresia"]),
            ("Viáticos / per diem", ["per diem", "viatico"]),
            ("Servicios de terceros (técnicos, alarma, mantención)", ["servicio autorizado", "alarma", "mantencion",
                                                                       "tecnico", "reparacion", "instalacion"]),
            ("Pago de cuentas / Servipag", ["servipag", "comprobante de pago", "aguas", "enel", "cuenta"]),
            ("Arriendo (sala, espacio, otros)", ["arriendo", "sala"]),
            ("Eventos / publicidad / producciones", ["evento", "eventos", "produccion", "productora", "publicidad",
                                                     "pendon", "impresos"]),
            ("Vestuario / uniformes", ["vestuario", "corbata", "uniforme", "deportes"]),
            ("Capacitación / cursos / seguros", ["capacitacion", "curso", "universidad", "seguro", "taller"]),
            ("Reunión con cliente (sin detalle del gasto)", ["reunion cliente", "reunion con cliente", "visits",
                                                             "meeting"]),
            ("Traslados / visitas anotados en planilla (sin medio de transporte)",
             ["visita", "traslado", "traslados", "prospeccion", "reunion", "actividad cliente", "presentacion",
              "retorno", "ida", "vuelta", "ingreso", "ingresos", "regreso"]),
            ("Gastos de oficina (planilla)", ["oficina"]),
            ("Voucher / boleta sin detalle del producto", ["transbank", "getnet", "mercado pago", "mercado", "klap",
                                                           "boleta electronica", "compra afecta", "tuu", "venta",
                                                           "boleta/voucher"]),
            ("Bazar / compras varias", ["bazar", "mall chino", "comercial", "importadora"])]:
        if any(_re.search(rf"(?<![a-z0-9]){_re.escape(k)}(?![a-z0-9])", t) for k in kws):
            return lab
    if it.get("metodo") == "planilla_reglas":
        if "exchange rate" in t or "base instalada" in t or "staffing" in t or _re.match(r"\d{5}\s", str(it.get("descripcion") or "")):
            return "Planilla: listado sin montos de gasto (revisar)"
        return "Planilla: fila sin categoría de gasto"
    if it.get("metodo") == "ocr_tesseract":
        return "Comprobante con OCR poco legible (revisar)"
    return "Otros sin clasificar"


def motivo_sin(it):
    t = R.normalize(f"{it.get('descripcion') or ''} {it.get('observacion') or ''}")
    for lab, k in [("Adjunto no descargable", "no descargable"), ("Comprimido no se pudo abrir", "comprimido"),
                   ("Rendición sin adjunto", "sin adjunto"), ("Comprobante ilegible", "ilegible"),
                   ("Comprobante en moneda extranjera", "usd"), ("Solo mapas / kilometraje sin montos", "mapas"),
                   ("Lo leído no alcanza a lo declarado (revisado a mano)", "lo leido en los comprobantes"),
                   ("Diferencia sin respaldo legible", "no respaldada")]:
        if k in t:
            return lab
    return "Respaldo incompleto o no leído"


DECL_MAP = {"003": "ALIMENTACION", "905": "UTILES_OFICINA", "506": "ESTACIONAMIENTO", "803": "NOTARIA",
            "707": "OTROS · Agua potable / dispensadores"}
_ruido = _re.compile(r"(image\d+\.(png|jpg|gif)|correo\.txt|whatsapp image|img[-_]\d+|picture-attachment|"
                     r"\.(pdf|jpe?g|png|xlsx?|msg|zip|rar)$|^re_ |^rv_ |^fw_ )", _re.I)


def detalle_rendicion(res):
    """Textos descriptivos: carpetas/archivos del adjunto y asuntos de correos .msg."""
    partes = []
    for f in res.get("archivos", []):
        for seg in f["ruta"].split(" > ")[1:] or [f["ruta"]]:
            for p in seg.split("/"):
                p2 = _re.sub(r"\.(pdf|jpe?g|png|xlsx?|msg|docx?)$", "", p, flags=_re.I).strip()
                if len(p2) > 6 and not _re.fullmatch(r"[\w\-\. ()]*\d{6,}[\w\-\. ()]*", p2) and \
                        not _re.search(r"whatsapp image|picture-attachment|^img|archivo_escaneado|^receipt_", p2, _re.I):
                    partes.append(p2)
    for e in res.get("errores", []):
        m = _re.search(r"([^/>]+)\.msg", e)
        if m:
            partes.append(m.group(1).strip())
    vistos, out = set(), []
    for p in partes:
        k = R.normalize(p)
        if k not in vistos:
            vistos.add(k)
            out.append(p)
    return out


def concepto_probable(res):
    votos = defaultdict(float)
    for p in detalle_rendicion(res):
        t = R.normalize(p)
        for c, kw, rx in EXTRA_RX:
            if rx.search(t):
                votos[c] += 1
                break
        else:
            c, kw = R.Classifier(R.CONFIG["conceptos"]).classify(p)
            if kw:
                votos[c] += 1
    if votos:
        top = max(votos, key=votos.get)
        if len(votos) >= 3 and votos[top] < 0.5 * sum(votos.values()):
            return "Mixto (ver detalle de la rendición)", "por descripción de archivos/correos"
        return top, "por descripción de archivos/correos"
    ident = defaultdict(float)
    for it in res["items"]:
        if it.get("monto") and it.get("concepto") not in (R.SIN_ID, "OTROS"):
            ident[it["concepto"]] += it["monto"]
    tot = sum(ident.values())
    if tot and max(ident.values()) >= 0.4 * tot:
        return max(ident, key=ident.get), "por lo identificado en la misma rendición"
    cod = str(res["rendicion"].get("concepto_declarado") or "")[:3]
    if cod == "002":
        return (max(ident, key=ident.get) if tot else "Movilización (sin detalle)"), "por cuenta declarada (movilización)"
    if cod in DECL_MAP:
        return DECL_MAP[cod], "por cuenta declarada"
    return "", ""


GENERICOS = {"Comprobante con OCR poco legible (revisar)", "Otros sin clasificar", "Planilla: fila sin categoría de gasto",
             "Voucher / boleta sin detalle del producto"}


# ======================================================================================
# CARGA
# ======================================================================================
def cargar_resultados(xlsx: Path, pkl: Path | None = None, reprocesar: bool = False,
                      hoja: str = "Reembolsos Consolidado") -> list[dict]:
    """Resultado de la lectura automática (descarga + texto + OCR) de las rendiciones de los CECOs operacionales.
    Usa `pkl` si existe (rápido). Con `reprocesar=True` vuelve a leer desde la caché de adjuntos
    (`<excel>_rpa_cache`), que descarga sólo lo que falte."""
    if pkl and Path(pkl).exists() and not reprocesar:
        log.info("Resultados del RPA desde %s", pkl)
        return pickle.load(open(pkl, "rb"))
    rendiciones = R.read_rendiciones(xlsx, hoja, cecos=R.CONFIG["cecos_operacionales"], solo_finalizadas=True)
    cache = xlsx.parent / f"{xlsx.stem}_rpa_cache"
    analyzer = R.LocalAnalyzer(R.Classifier(R.CONFIG["conceptos"]))
    log.info("Leyendo %d rendiciones (caché: %s)", len(rendiciones), cache)
    with ThreadPoolExecutor(R.CONFIG["descarga"]["workers"]) as ex:
        results = list(ex.map(lambda r: R.process_one(r, analyzer, cache, False), rendiciones))
    if pkl:
        pickle.dump(results, open(pkl, "wb"))
    return results


def cargar_lecturas(dir_lecturas: Path, indice_json: Path) -> tuple[dict, dict]:
    """Lecturas manuales (lecturas/lote_*.json) + índice de páginas exportadas (revisar/indice.json)."""
    lecturas = {}
    for fn in sorted(glob.glob(str(Path(dir_lecturas) / "lote_*.json"))):      # los .jsonl son borradores
        for x in json.load(open(fn, encoding="utf-8")):
            lecturas[x["id"]] = x
    index = {}
    if Path(indice_json).exists():
        index = {x["id"]: x for x in json.load(open(indice_json, encoding="utf-8"))}
    elif lecturas:
        log.warning("No existe %s: las lecturas manuales no podrán reemplazar las páginas leídas por OCR", indice_json)
    return lecturas, index


# ======================================================================================
# ETAPAS
# ======================================================================================
def aplicar_lecturas_manuales(results, lecturas, index):
    ilegible_ids, n_items = set(), 0
    for res in results:
        rid = res["rendicion"]["id"]
        if rid not in lecturas:
            continue
        lec, pags = lecturas[rid], {p["img"]: p for p in index.get(rid, {}).get("paginas", [])}
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
    return n_items, ilegible_ids


def depurar_automaticos(results):
    n_ocr_dudoso = n_reclas = 0
    for res in results:
        declared = res["rendicion"].get("monto_declarado") or 0
        decl_n = R.normalize(res["rendicion"].get("concepto_declarado"))
        for it in res["items"]:
            if it.get("metodo") == "lectura_manual":        # lo leído a mano no se toca
                continue
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
            if it.get("monto") and it.get("concepto") == "OTROS" and it.get("metodo") != "planilla_reglas" \
                    and _re.search(r"\d{1,2}:\d{2}.*,\s*(CL|Chile)\b", raw):   # recibo de viaje app (hora + dirección, CL)
                it["concepto"], it["palabra_clave"], n_reclas = "APPS_TRANSPORTE", "reclasif:recibo viaje app", n_reclas + 1
            if it.get("monto") and it.get("concepto") == "OTROS" and it.get("metodo") == "planilla_reglas" \
                    and _re.search(r"(?<![a-z])(pasajes?|buses?|terminal|turbus|pullman)(?![a-z])", R.normalize(raw)) \
                    and not _re.search(r"(?<![a-z])(uber|taxi|cabify|avion|aereo)(?![a-z])", R.normalize(raw)):
                it["concepto"], it["palabra_clave"], n_reclas = "BUS_INTERURBANO", "reclasif:pasaje/terminal (planilla)", n_reclas + 1
            if it.get("monto") and it.get("concepto") == "OTROS":
                t = R.normalize(f"{it.get('emisor') or ''} {it.get('descripcion') or ''}")
                for c, kw, rx in EXTRA_RX:
                    if rx.search(t):
                        it["concepto"], it["palabra_clave"], n_reclas = c, f"reclasif:{kw}", n_reclas + 1
                        break
    return n_ocr_dudoso, n_reclas


def rele_planillas(results, lecturas, cache: Path):
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
            data = R.download(r["adjunto"], cache / "descargas").read_bytes()
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
    return n_planillas


def formularios_rendicion(results, lecturas, cache: Path):
    n_form = 0
    for res in results:
        r, rid = res["rendicion"], res["rendicion"]["id"]
        declared = r.get("monto_declarado") or 0
        if rid in lecturas or not declared or not any(it.get("metodo") == "planilla_reglas" for it in res["items"]):
            continue
        try:
            data = R.download(r["adjunto"], cache / "descargas").read_bytes()
            files = R.expand(R.filename_from_url(r["adjunto"]), data)
        except Exception:  # noqa: BLE001
            continue
        vistos, filas = set(), []
        for f in files:
            if f.tipo not in ("xlsx", "xls", "xlsm", "csv"):
                continue
            try:
                rows = _lee_formulario(f)
            except Exception:  # noqa: BLE001
                rows = None
            if not rows:
                continue
            firma = tuple(sorted((R.normalize(d), round(v)) for _, d, v in rows))
            if firma in vistos:                                  # copia del mismo formulario
                continue
            vistos.add(firma)
            filas += [(f.ruta, fch, d, v) for fch, d, v in rows]
        s_f = sum(v for *_, v in filas)
        if not filas or s_f < 0.8 * declared:
            continue
        nuevos = []
        for ruta, fch, desc, v in filas:
            c, kw = clasifica_texto(desc)
            nuevos.append({**asdict(R.Item(tipo_documento="Formulario rendición", descripcion=desc[:120], monto=v,
                                           concepto=c, palabra_clave=kw, metodo="formulario_rendicion", confianza="media",
                                           fecha_doc=R.parse_date(fch))),
                           "archivo": ruta, "tipo_archivo": "xlsx", "pagina": 1, "alertas": ""})
        res["items"] = [it for it in res["items"] if it.get("metodo") in ("lectura_manual",)] + nuevos
        n_form += 1
    return n_form


def cuadrar(results, lecturas, ilegible_ids):
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


def prorratear(results):
    """Si lo leído supera lo declarado (planilla resumen + detalle, boletas de otra rendición), los montos se
    escalan a lo declarado para que los totales por concepto cuadren con lo pagado en Talana."""
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
                it["monto"] = round(it["monto"] * f, 2)
        if f < 1:  # el residuo de redondeo va al ítem mayor: la suma queda exactamente igual a lo declarado
            dif = round(declared - sum(it["monto"] for it in items), 2)
            max(items, key=lambda x: x["monto"])["monto"] = round(max(items, key=lambda x: x["monto"])["monto"] + dif, 2)
    return n_prorr


def calcular_probables(results):
    prob = {}
    for res in results:
        c, base = concepto_probable(res)
        prob[res["rendicion"]["id"]] = (c, base, " | ".join(detalle_rendicion(res))[:250])
    return prob


@dataclass
class Estado:
    results: list
    lecturas: dict
    prob: dict
    stats: dict = field(default_factory=dict)


def ejecutar_pipeline(results, lecturas, index, cache: Path) -> Estado:
    """Aplica todas las etapas sobre `results` (se modifica en el lugar)."""
    n_items, ilegible_ids = aplicar_lecturas_manuales(results, lecturas, index)
    n_ocr_dudoso, n_reclas = depurar_automaticos(results)
    n_planillas = rele_planillas(results, lecturas, cache)
    n_form = formularios_rendicion(results, lecturas, cache)
    cuadrar(results, lecturas, ilegible_ids)
    n_prorr = prorratear(results)
    prob = calcular_probables(results)
    stats = {"Rendiciones procesadas": len(results), "Revisadas a mano (falta OCR)": len(lecturas),
             "Ítems leídos a mano": n_items, "Con comprobantes ilegibles": len(ilegible_ids),
             "Rendiciones prorrateadas (leído > declarado)": n_prorr,
             "Ítems OCR descartados (monto > declarado)": n_ocr_dudoso, "Ítems OTROS reclasificados": n_reclas,
             "Planillas re-leídas (columna de monto corregida)": n_planillas,
             "Rendiciones con formulario de rendición como detalle oficial": n_form}
    return Estado(results, lecturas, prob, stats)
