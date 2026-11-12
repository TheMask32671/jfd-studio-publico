"""Transforma MusicXML em páginas SVG e em PDF pronto para impressão.

Ordem de preferência: MuseScore (se estiver instalado no computador) e, em seguida,
o motor Verovio embutido. Sem nenhum dos dois, o MusicXML continua disponível.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ..config import log

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
PAGE_W, PAGE_H = 2100, 2970      # A4 em décimos de milímetro


def find_musescore() -> str | None:
    for name in ("MuseScore4", "MuseScore3", "mscore", "musescore", "mscore3"):
        hit = shutil.which(name)
        if hit:
            return hit
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            for sub in (r"MuseScore 4\bin\MuseScore4.exe", r"MuseScore 3\bin\MuseScore3.exe"):
                p = Path(base) / sub if base else None
                if p and p.exists():
                    return str(p)
    return None


def verovio_available() -> bool:
    try:
        import verovio  # noqa: F401
        return True
    except Exception:
        return False


# Glifos musicais que o Verovio escreve com a fonte Leipzig dentro de textos (andamento).
_TEXT_GLYPHS = {"\ue1d5": "\u2669", "\ue1d7": "\u266a", "\ue1d3": "\u2669", "\ue1d2": "\u2669", "\ue1e7": "."}
_BOLD = {"ending", "fing", "reh", "tempo"}
_ITALIC = {"dir", "dynam", "mNum"}


def _simplify_text(text_el, parents: dict, local) -> None:
    """Reescreve um <text> cheio de <tspan> aninhados como textos simples, que o Qt desenha bem."""
    classes: set[str] = set()
    node = text_el
    while node in parents:
        node = parents[node]
        classes.update((node.get("class") or "").split())
    base = {"x": text_el.get("x") or "0", "y": text_el.get("y") or "0",
            "anchor": text_el.get("text-anchor") or "start", "size": "", "music": False}
    runs: list[tuple[dict, str]] = []

    def visit(el, state):
        st = dict(state)
        for attr, key in (("x", "x"), ("y", "y"), ("text-anchor", "anchor")):
            if el.get(attr):
                st[key] = el.get(attr)
        size = el.get("font-size")
        if size and not size.startswith("0"):
            st["size"] = size
        if el.get("font-family"):
            st["music"] = "leipzig" in el.get("font-family").lower()
        if el.text and el.text.strip() and local(el) == "tspan":
            content = el.text
            if st["music"]:
                content = "".join(_TEXT_GLYPHS.get(ch, "\u2669" if "\ue000" <= ch <= "\uf8ff" else ch) for ch in content)
            runs.append((st, content))
        for child in el:
            if local(child) == "tspan":
                visit(child, st)

    visit(text_el, base)
    lines: list[dict] = []
    for st, content in runs:
        if lines and (lines[-1]["x"], lines[-1]["y"]) == (st["x"], st["y"]):
            lines[-1]["text"] += content
            if not st["music"] and not lines[-1]["plain_size"]:
                lines[-1]["size"], lines[-1]["plain_size"] = st["size"], True
        else:
            lines.append({"x": st["x"], "y": st["y"], "anchor": st["anchor"], "size": st["size"],
                          "plain_size": not st["music"], "text": content})
    for key in list(text_el.attrib):
        del text_el.attrib[key]
    for child in list(text_el):
        text_el.remove(child)
    text_el.tag = f"{{{SVG}}}g"
    text_el.text = None
    for ln in lines:
        t = ET.SubElement(text_el, f"{{{SVG}}}text")
        t.set("x", ln["x"])
        t.set("y", ln["y"])
        t.set("text-anchor", ln["anchor"])
        t.set("font-family", "Times New Roman")
        t.set("font-size", ln["size"] or "405px")
        t.set("stroke", "none")
        if classes & _BOLD:
            t.set("font-weight", "bold")
        if classes & _ITALIC:
            t.set("font-style", "italic")
        t.text = " ".join(ln["text"].split())


def flatten_svg(svg: str) -> str:
    """Simplifica o SVG do Verovio para leitores SVG básicos (como o do Qt).

    Troca cada <use> pelo desenho a que ele se refere e o <svg> interno por um grupo.
    """
    import copy

    ET.register_namespace("", SVG)
    ET.register_namespace("xlink", XLINK)
    root = ET.fromstring(svg)
    targets = {el.get("id"): el for el in root.iter() if el.get("id")}

    def px(value: str | None, default: float = 0.0) -> float:
        m = re.match(r"-?\d+\.?\d*", value or "")
        return float(m.group(0)) if m else default

    def local(el) -> str:
        return el.tag.split("}")[-1]

    def expand(use):
        ref = (use.get(f"{{{XLINK}}}href") or use.get("href") or "").lstrip("#")
        target = targets.get(ref)
        if target is None:
            return None
        g = ET.Element(f"{{{SVG}}}g")
        parts = []
        if use.get("transform"):
            parts.append(use.get("transform"))
        x, y = px(use.get("x")), px(use.get("y"))
        if x or y:
            parts.append(f"translate({x:g},{y:g})")
        if local(target) == "symbol":
            box = [float(v) for v in (target.get("viewBox") or "").replace(",", " ").split()]
            if len(box) == 4 and box[3] > 0 and use.get("height"):
                parts.append(f"scale({px(use.get('height'), box[3]) / box[3]:g})")
            content = [copy.deepcopy(node) for node in target]
        else:
            clone = copy.deepcopy(target)
            clone.attrib.pop("id", None)
            content = [clone]
        if parts:
            g.set("transform", " ".join(parts))
        for k in ("class", "fill", "stroke", "color"):
            if use.get(k):
                g.set(k, use.get(k))
        g.extend(content)
        return g

    def walk(parent, depth=0):
        for i, child in enumerate(list(parent)):
            tag = local(child)
            if tag == "defs":
                continue
            if tag == "use" and depth < 12:
                g = expand(child)
                if g is not None:
                    parent.remove(child)
                    parent.insert(i, g)
                    walk(g, depth + 1)
            elif tag == "svg":
                box = [float(v) for v in (child.get("viewBox") or "").replace(",", " ").split()]
                g = ET.Element(f"{{{SVG}}}g")
                if len(box) == 4 and box[2] > 0:
                    g.set("transform", f"scale({px(root.get('width'), PAGE_W) / box[2]:g})")
                color = child.get("color") or "black"
                color = "#000000" if color in ("black", "currentColor") else color
                g.set("fill", color)
                g.set("stroke", color)     # equivale à regra CSS "path, polygon… {stroke: currentColor}"
                for k, v in child.attrib.items():
                    if k in ("class", "id", "font-family"):
                        g.set(k, v)
                g.extend(list(child))
                parent.remove(child)
                parent.insert(i, g)
                walk(g, depth)
            else:
                walk(child, depth)

    walk(root)
    parents = {child: parent for parent in root.iter() for child in parent}
    for el, parent in list(parents.items()):
        hidden = "placeholder" in (el.get("class") or "").split()       # CSS: visibility:hidden
        if local(el) in ("defs", "style", "title") or hidden:
            parent.remove(el)
    parents = {child: parent for parent in root.iter() for child in parent}
    for el in [e for e in root.iter() if local(e) == "text"]:
        _simplify_text(el, parents, local)
    w, h = px(root.get("width"), PAGE_W), px(root.get("height"), PAGE_H)
    root.set("viewBox", f"0 0 {w:g} {h:g}")
    return ET.tostring(root, encoding="unicode").replace("currentColor", "#000000")


def raw_svg_pages(musicxml: str) -> list[str]:
    """Páginas SVG exatamente como o Verovio gera."""
    import verovio

    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": PAGE_W, "pageHeight": PAGE_H, "scale": 40, "adjustPageHeight": False,
                   "footer": "none", "header": "auto", "breaks": "auto",
                   "pageMarginLeft": 100, "pageMarginRight": 100, "pageMarginTop": 100, "pageMarginBottom": 100})
    if not tk.loadData(musicxml):
        raise RuntimeError("Verovio não conseguiu ler o MusicXML")
    return [tk.renderToSVG(i) for i in range(1, tk.getPageCount() + 1)]


def svg_pages(musicxml: str) -> list[str]:
    """Renderiza o MusicXML em páginas SVG simplificadas, prontas para o Qt."""
    pages = []
    for raw in raw_svg_pages(musicxml):
        try:
            pages.append(flatten_svg(raw))
        except Exception:
            log.exception("flatten_svg falhou; usando SVG original")
            pages.append(raw)
    return pages


def svg_to_png(svg: str, png_path: str | Path, width: int = 1100) -> None:
    """Desenha uma página SVG em PNG com o mesmo motor usado na tela e no PDF."""
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(width, int(width * PAGE_H / PAGE_W), QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, image.width(), image.height()))
    finally:
        painter.end()
    image.save(str(png_path))


def svgs_to_pdf(pages: list[str], pdf_path: str | Path) -> None:
    """Grava as páginas SVG em um PDF A4 usando o Qt."""
    from PySide6.QtCore import QByteArray, QMarginsF, QRectF
    from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPainter, QPdfWriter
    from PySide6.QtSvg import QSvgRenderer

    if QGuiApplication.instance() is None:
        raise RuntimeError("o Qt precisa estar iniciado para gerar o PDF")
    writer = QPdfWriter(str(pdf_path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    writer.setResolution(300)
    painter = QPainter(writer)
    try:
        for i, svg in enumerate(pages):
            if i:
                writer.newPage()
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            renderer.render(painter, QRectF(0, 0, writer.width(), writer.height()))
    finally:
        painter.end()


def render_pdf(xml_path: str | Path, pdf_path: str | Path) -> tuple[bool, str]:
    """Gera o PDF da partitura. Retorna (deu certo, motor usado ou motivo da falha)."""
    xml_path, pdf_path = Path(xml_path), Path(pdf_path)
    ms = find_musescore()
    if ms:
        try:
            flags = 0x08000000 if sys.platform == "win32" else 0
            subprocess.run([ms, "-o", str(pdf_path), str(xml_path)], timeout=180, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           creationflags=flags)
            if pdf_path.exists() and pdf_path.stat().st_size > 500:
                return True, "MuseScore"
        except Exception:
            log.exception("MuseScore falhou ao gerar PDF")
    if verovio_available():
        try:
            svgs_to_pdf(svg_pages(xml_path.read_text(encoding="utf-8")), pdf_path)
            if pdf_path.exists() and pdf_path.stat().st_size > 500:
                return True, "Verovio"
        except Exception:
            log.exception("Verovio falhou ao gerar PDF")
    return False, "Nenhum motor de partitura disponível; use o arquivo MusicXML."
