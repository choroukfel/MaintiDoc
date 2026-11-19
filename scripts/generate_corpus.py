"""Génère le corpus MaintiDoc : PDF fictifs, metadata.json et photos de test.

Usage (depuis la racine du projet) :
    .venv\\Scripts\\python.exe scripts\\generate_corpus.py
"""
from __future__ import annotations

import json
import sys
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures  # noqa: E402
from corpus_content import DOCUMENTS  # noqa: E402
from corpus_content_extra import EXTRA_DOCUMENTS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'corpus'
FIG_DIR = ROOT / 'data' / 'build' / 'figures'
PHOTO_DIR = ROOT / 'eval' / 'photos'

# Manuel fourni avec le sujet : déjà présent dans corpus/, on l'ajoute seulement aux métadonnées.
EXISTING = {
    'document_id': 'SAN-MAN-001',
    'fichier': 'Manuel_Maintenance_SAN_Projet_Integrateur.pdf',
    'titre': "SAN-LAB 2000 - Manuel de maintenance d'un système SAN",
    'version': '1.1', 'date': '2026-10-01', 'famille': 'SAN', 'modeles': ['SAN-LAB 2000'],
    'groupes_acces': ['tech_san', 'responsables'], 'niveau_confiance': 'valide',
    'description': 'Manuel principal : architecture, FC/MPIO, alarmes AL-00 à AL-04, PROC-SAN-01, historique INC-104 à INC-130.',
}

ss = getSampleStyleSheet()
ST = {
    'title': ParagraphStyle('t', parent=ss['Title'], fontSize=20, leading=24, spaceAfter=6),
    'sub': ParagraphStyle('s', parent=ss['Normal'], fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor('#555')),
    'h1': ParagraphStyle('h1', parent=ss['Heading2'], textColor=colors.HexColor('#1f3a5f'), spaceBefore=12, keepWithNext=1),
    'p': ParagraphStyle('p', parent=ss['BodyText'], fontSize=10, leading=14),
    'cell': ParagraphStyle('c', parent=ss['BodyText'], fontSize=8.5, leading=10.5),
    'cellh': ParagraphStyle('ch', parent=ss['BodyText'], fontSize=8.5, leading=10.5, fontName='Helvetica-Bold'),
    'cap': ParagraphStyle('cap', parent=ss['BodyText'], fontSize=9, leading=12, textColor=colors.HexColor('#333'),
                          fontName='Helvetica-Oblique', spaceBefore=4, spaceAfter=8),
    'warn': ParagraphStyle('w', parent=ss['BodyText'], fontSize=9.5, leading=13, backColor=colors.HexColor('#fff3cd'),
                           borderPadding=6, spaceBefore=8, spaceAfter=10),
    'note': ParagraphStyle('n', parent=ss['BodyText'], fontSize=9.5, leading=13, backColor=colors.HexColor('#e8f1fb'),
                           borderPadding=6, spaceBefore=8, spaceAfter=10),
    'step': ParagraphStyle('st', parent=ss['BodyText'], fontSize=10, leading=13.5, leftIndent=14, firstLineIndent=-14),
}


def P(text, style='p'):
    return Paragraph(escape(text), ST[style])


def on_page(canvas, doc, meta):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.HexColor('#555'))
    w, h = A4
    canvas.drawString(2 * cm, h - 1.2 * cm, f"{meta['document_id']}  |  Révision {meta['version']}  |  {meta['date']}")
    canvas.drawRightString(w - 2 * cm, h - 1.2 * cm, 'MaintiDoc - document pédagogique fictif')
    canvas.line(2 * cm, h - 1.35 * cm, w - 2 * cm, h - 1.35 * cm)
    canvas.drawString(2 * cm, 1.2 * cm, f"Diffusion : {', '.join(meta['groupes_acces'])}")
    canvas.drawRightString(w - 2 * cm, 1.2 * cm, f'Page {doc.page}')
    canvas.restoreState()


def table(header, rows, width):
    data = [[P(h, 'cellh') for h in header]] + [[P(str(v), 'cell') for v in r] for r in rows]
    t = Table(data, colWidths=[width / len(header)] * len(header), repeatRows=1)
    t.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d9e2ef')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    return t


def build_pdf(meta: dict, out: Path):
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title=meta['titre'], author='MaintiDoc (corpus fictif)',
                            subject=meta['description'])
    width = A4[0] - 4 * cm
    flow = [P(meta['titre'], 'title'),
            P(f"{meta['document_id']}  -  Révision {meta['version']}  -  {meta['date']}  -  "
              f"Modèles : {', '.join(meta['modeles'])}", 'sub'),
            P('Document pédagogique fictif : ne remplace pas une notice constructeur.', 'sub'),
            Spacer(1, 0.5 * cm)]
    for b in meta['blocs']:
        kind = b[0]
        if kind == 'h1':
            flow.append(P(b[1], 'h1'))
        elif kind in ('p', 'warn', 'note'):
            prefix = {'warn': 'ATTENTION : ', 'note': 'NOTE : '}.get(kind, '')
            flow.append(P(prefix + b[1], kind))
        elif kind == 'table':
            _, ref, cap, header, rows = b
            flow.append(KeepTogether([P(cap, 'cap'), table(header, rows, width)]))
            flow.append(Spacer(1, 0.3 * cm))
        elif kind == 'fig':
            _, ref, key, cap, w_cm = b
            path = FIG_DIR / f'{key}.png'
            iw, ih = figures.Image.open(path).size
            w = min(w_cm * cm, width)
            flow.append(KeepTogether([Image(str(path), width=w, height=w * ih / iw), P(cap, 'cap')]))
        elif kind == 'steps':
            _, ref, title, steps = b
            flow.append(P(title, 'h1'))
            flow += [P(f'{i}. {s}', 'step') for i, s in enumerate(steps, 1)]
            flow.append(Spacer(1, 0.2 * cm))
        elif kind == 'pagebreak':
            flow.append(PageBreak())
        else:
            raise ValueError(kind)
    cb = partial(on_page, meta=meta)
    doc.build(flow, onFirstPage=cb, onLaterPages=cb)


def scanify(src: Path, dst: Path, seed: int):
    """Simule un document papier numérisé : pages rendues en images (sans couche texte), légèrement
    inclinées, jaunies et bruitées. Leur contenu n'est accessible que par OCR."""
    import io

    import numpy as np
    import pymupdf
    from PIL import Image, ImageFilter
    rng = np.random.default_rng(seed)
    src_doc, out = pymupdf.open(src), pymupdf.open()
    for page in src_doc:
        pix = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        im = Image.frombytes('L', (pix.width, pix.height), pix.samples)
        im = im.rotate(float(rng.uniform(-1.2, 1.2)), resample=Image.BICUBIC, expand=False, fillcolor=255)
        arr = np.asarray(im, dtype=np.float32)
        arr = arr * 0.92 + rng.normal(0, 7, arr.shape) + 8
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.5))
        rgb = Image.merge('RGB', (im, im.point(lambda v: v * 0.98), im.point(lambda v: v * 0.90)))  # papier jauni
        buf = io.BytesIO()
        rgb.save(buf, format='JPEG', quality=80)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buf.getvalue())
    out.save(dst, garbage=3, deflate=True)
    out.close(); src_doc.close()


def main():
    from reportlab import rl_config
    rl_config.invariant = 1  # PDF reproductibles (pas de date ni d'identifiant aléatoire)
    CORPUS.mkdir(exist_ok=True)
    figures.build_all(FIG_DIR, PHOTO_DIR)
    metadata = [EXISTING]
    for i, meta in enumerate(DOCUMENTS + EXTRA_DOCUMENTS):
        if meta.get('scanne'):
            tmp = FIG_DIR / f"_{meta['document_id']}_texte.pdf"
            build_pdf(meta, tmp)
            scanify(tmp, CORPUS / meta['fichier'], seed=i)
            tmp.unlink()
        else:
            build_pdf(meta, CORPUS / meta['fichier'])
        metadata.append({k: v for k, v in meta.items() if k != 'blocs'})
        print('OK', meta['fichier'])
    (CORPUS / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(metadata)} documents décrits dans corpus/metadata.json')


if __name__ == '__main__':
    main()
