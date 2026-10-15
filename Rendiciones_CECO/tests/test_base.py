"""Pruebas de las reglas críticas de la base de Power BI. Ejecutar:  python -m pytest -q tests"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generar_base_powerbi as G  # noqa: E402
import generar_base_requerimientos as GR  # noqa: E402
import ingesta_requerimientos as I  # noqa: E402
import pipeline_rendiciones as P  # noqa: E402
import rpa_rendiciones as R  # noqa: E402


# ---------------------------------------------------------------- montos
@pytest.mark.parametrize("txt,esperado", [("92967", 92967.0), ("1.094.090", 1094090.0), ("1.234,56", 1234.56),
                                          ("$ 44.025", 44025.0), ("1,5", 1.5), ("Sin información", None), ("", None)])
def test_monto(txt, esperado):
    assert I._monto(txt) == esperado


# ---------------------------------------------------------------- prorrateo exacto
def _res(declarado, montos):
    return {"rendicion": {"monto_declarado": declarado},
            "items": [{"monto": m, "concepto": "OTROS"} for m in montos]}


@pytest.mark.parametrize("declarado,montos", [(100000, [60000, 50000, 33333]), (99997, [33333, 33333, 33334, 7]),
                                              (1000, [333.33, 333.33, 333.33, 500])])
def test_prorrateo_suma_exacta(declarado, montos):
    res = _res(declarado, montos)
    P.prorratear([res])
    assert sum(i["monto"] for i in res["items"]) == pytest.approx(declarado, abs=0.005)


def test_prorrateo_no_toca_si_no_excede():
    res = _res(100000, [40000, 50000])
    assert P.prorratear([res]) == 0
    assert [i["monto"] for i in res["items"]] == [40000, 50000]


# ---------------------------------------------------------------- libros: unificación y duplicados
def _libro(path, filas, fecha_max):
    cols = ["ID", "Rut Creador", "Nombre Creador", "Etapas canal", "Responsables", "Estado", "Fecha resolución",
            "Tiempo de Resolución (en días)", "Rut Responsable(s)", "Nombre(s) Responsable(s)",
            "Indique monto a sustentar o reembolsar", "Adjuntar el archivo (en caso de necesitar subir más de uno)"]
    df = pd.DataFrame(filas, columns=cols)
    res = pd.DataFrame({"ID": df["ID"], "Razón Social": "X SpA", "Código Centro Costo": "01", "Centro Costo": "CC",
                        "Jefatura": "1-9", "Fecha": fecha_max, "Canal": "INT - Reembolso", "Etapa en curso": "Inicial",
                        "Estado": df["Estado"], "Días que lleva sin movimiento": "0"})
    with pd.ExcelWriter(path) as w:
        res.to_excel(w, sheet_name="Resumen", index=False)
        df.to_excel(w, sheet_name="2. INT - Reembolso AGR PA", index=False)
        df.to_excel(w, sheet_name="1. Canal de Consultas", index=False)          # no debe leerse


def _fila(i, estado, monto="1.000"):
    return [i, "1-9", "ANA PEREZ", "3", "6", estado, None, "0", "2-7", "JEFE", monto,
            f"https://storage.googleapis.com/helpdesk-media/media/answers/{i}.pdf"]


def test_libros_dedup_gana_el_mas_reciente(tmp_path):
    viejo, nuevo = tmp_path / "viejo.xlsx", tmp_path / "nuevo.xlsx"
    _libro(viejo, [_fila("CDR1", "En progreso"), _fila("CDR2", "Finalizado")], "2026-01-10 10:00:00")
    _libro(nuevo, [_fila("CDR1", "Finalizado"), _fila("CDR3", "Abierto")], "2026-03-10 10:00:00")
    rend, extra, urls, informe = I.leer_libros([viejo, nuevo])
    assert sorted(r.id for r in rend) == ["CDR1", "CDR2", "CDR3"]
    assert extra["CDR1"]["estado"] == "Finalizado" and extra["CDR1"]["libro"] == "nuevo"
    assert extra["CDR1"]["otros"] == ["viejo: En progreso"]
    assert {n: d for n, _, _, d in informe} == {"nuevo": 0, "viejo": 1}
    assert I.hojas_reembolso(nuevo) == ["2. INT - Reembolso AGR PA"]            # no toma la hoja de consultas
    assert all(r.monto_declarado == 1000.0 for r in rend)


def test_hoja_corta_unifica_numeracion():
    assert I._hoja_corta("6. (N) INT - Reembolso AGR PI") == I._hoja_corta("8. (N) INT - Reembolso AGR PI")


# ---------------------------------------------------------------- seguridad de la descarga
@pytest.mark.parametrize("url", ["http://storage.googleapis.com/x.pdf", "https://evil.example.com/x.pdf",
                                 "https://169.254.169.254/latest/meta-data", "file:///etc/passwd"])
def test_descarga_rechaza_hosts_no_permitidos(url, tmp_path):
    with pytest.raises((ConnectionError, FileNotFoundError, OSError)):
        R.download(url, tmp_path)


# ---------------------------------------------------------------- clasificación de adjuntos y facturable
def test_clase_adjunto():
    assert GR.clase("pdf", "texto_pdf_texto", "OK") == "PDF con texto"
    assert GR.clase("pdf", "ocr_tesseract", "OK") == "PDF escaneado (OCR)"
    assert GR.clase("jpg", "ocr_tesseract", "OK") == "Imagen / foto (OCR)"
    assert GR.clase("xlsx", "planilla_reglas", "OK") == "Planilla Excel"
    assert GR.clase("jpg", "lectura_manual", "OK").startswith("Lectura manual")


def test_sugerencia_facturable_prioriza_aprobador():
    assert GR.sugerencia("SI", "NO FACTURABLE", "")[0] == "FACTURABLE"
    assert GR.sugerencia("NO", "FACTURABLE", "")[0] == "NO FACTURABLE"
    assert GR.sugerencia("", "FACTURABLE", "")[0] == "FACTURABLE"
    assert GR.sugerencia("", "", "123")[0] == "POR REVISAR"


# ---------------------------------------------------------------- claves y seguimiento de facturación
def test_claves_estables_y_unicas():
    a, b = G.Claves(), G.Claves()
    ka = [a("x"), a("x"), a("y")]
    kb = [b("x"), b("x"), b("y")]
    assert ka == kb and len(set(ka)) == 3


def test_facturacion_se_conserva_al_regenerar(tmp_path):
    cols = ["Clave_Item"] + G.TRACK
    base = pd.DataFrame({"Clave_Item": ["a", "b", "c"]})
    previo = tmp_path / "maestro.xlsx"
    anot = pd.DataFrame([["a", "Facturado", "Cliente Z", "F-100", "2026-05-01", 5000, "ok"],
                         ["zz", "Pendiente de facturar", None, None, None, None, None]], columns=cols)
    with pd.ExcelWriter(previo) as w:
        anot.to_excel(w, sheet_name="Base", index=False)
    df, conservados, huerfanos = G.aplicar_facturacion_previa(base.copy(), previo, tmp_path)
    d = df.set_index("Clave_Item")
    assert (conservados, huerfanos) == (1, 1)
    assert d.loc["a", "Estado_Facturacion"] == "Facturado" and d.loc["a", "N_Factura"] == "F-100"
    assert d.loc["b", "Estado_Facturacion"] == "Sin clasificar"
    assert (tmp_path / "facturacion_huerfanos.csv").exists()


def test_columnas_sin_cabify_y_con_seguimiento_al_final():
    assert not any("Cabify" in c for c in GR.COLS)
    assert GR.COLS[-6:] == ["Estado_Facturacion", "Cliente_Facturado", "N_Factura", "Fecha_Factura",
                            "Monto_Facturado_CLP", "Obs_Facturacion"]
    assert len(GR.COLS) == len(set(GR.COLS))
