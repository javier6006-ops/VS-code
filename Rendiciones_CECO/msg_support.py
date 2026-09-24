# -*- coding: utf-8 -*-
"""Soporte de correos Outlook .msg para el RPA (sin extract-msg): expande el .msg en su cuerpo (txt) y adjuntos."""
import io

import olefile

import rpa_rendiciones as R


def _s(ole, name):
    for suf, enc in (("001F", "utf-16-le"), ("001E", "latin-1")):
        p = f"{name}{suf}"
        if ole.exists(p):
            return ole.openstream(p).read().decode(enc, "replace").strip("\x00")
    return ""


def msg_members(data):
    ole = olefile.OleFileIO(io.BytesIO(data))
    subj = _s(ole, "__substg1.0_0037")
    body = _s(ole, "__substg1.0_1000")
    frm = _s(ole, "__substg1.0_0C1A")
    yield "correo.txt", f"ASUNTO: {subj}\nDE: {frm}\n{body}".encode("utf-8")
    for st in {e[0] for e in ole.listdir() if e[0].startswith("__attach_version1.0_")}:
        name = _s(ole, f"{st}/__substg1.0_3707") or _s(ole, f"{st}/__substg1.0_3704") or "adjunto"
        p = f"{st}/__substg1.0_37010102"
        if ole.exists(p):
            yield name, ole.openstream(p).read()


_orig_expand = R.expand


def expand(name, data, depth=0, prefix=""):
    if name.lower().endswith(".msg") and depth <= 3:
        out = []
        try:
            for n, d in msg_members(data):
                out += _orig_expand(n, d, depth + 1, f"{prefix}{name} > ")
        except Exception:  # noqa: BLE001
            return []
        return out
    res = []
    for f in _orig_expand(name, data, depth, prefix):
        if f.ruta.lower().endswith(".msg"):
            res += expand(f.ruta.split(" > ")[-1], f.data, depth + 1, f.ruta.rsplit(" > ", 1)[0] + " > " if " > " in f.ruta else "")
        else:
            res.append(f)
    return res


R.expand = expand
