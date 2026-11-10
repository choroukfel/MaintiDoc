"""Ingestion des PDF : passages textuels, tableaux (indexés ligne par ligne) et figures.

Chaque élément conserve : document_id, version, page, element_id, type, modèles, famille,
groupes d'accès et niveau de confiance (section 4.1 du sujet).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import pymupdf
from PIL import Image

from . import config

HEADER_FOOTER = re.compile(
    r'(\|\s*R[ée]vision|^Page \d+$|^Diffusion :|document p[ée]dagogique fictif|Manuel pedagogique fictif)', re.I)
TAB_REF = re.compile(r'^((?:[A-Z]+-)?TAB-\d+)\s*:')
FIG_REF = re.compile(r'^(FIG-\d+)\s*:')
CHUNK_CHARS = 700


def load_metadata() -> dict[str, dict]:
    meta = json.loads((config.CORPUS_DIR / 'metadata.json').read_text(encoding='utf-8'))
    return {m['document_id']: m for m in meta}


def corpus_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(config.CORPUS_DIR.glob('*')):
        if p.is_file():
            h.update(p.name.encode()); h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _clean(text: str) -> str:
    return ' '.join(text.split())


def _blocks(page) -> list[tuple[pymupdf.Rect, str]]:
    out = []
    for b in page.get_text('blocks', sort=True):
        if b[6] != 0:  # bloc image
            continue
        text = b[4].strip()
        if not text or HEADER_FOOTER.search(text.splitlines()[0]):
            continue
        out.append((pymupdf.Rect(b[:4]), text))
    return out


def _caption_near(blocks, rect: pymupdf.Rect, pattern: re.Pattern, above: bool) -> tuple[str | None, str | None]:
    best = None
    for r, text in blocks:
        first = _clean(text)
        if not pattern.match(first):
            continue
        dist = (rect.y0 - r.y1) if above else (r.y0 - rect.y1)
        if -5 <= dist <= 60 and (best is None or dist < best[0]):
            best = (dist, first)
    if best is None:
        return None, None
    return pattern.match(best[1]).group(1), best[1]


def _base(meta: dict, page: int) -> dict:
    return {'document_id': meta['document_id'], 'titre': meta['titre'], 'version': meta['version'], 'page': page,
            'modeles': meta['modeles'], 'famille': meta['famille'], 'groupes_acces': meta['groupes_acces'],
            'niveau_confiance': meta['niveau_confiance']}


def parse_document(meta: dict, image_dir: Path) -> tuple[list[dict], list[dict]]:
    """Retourne (elements, units). Un élément est ce qui est cité ; une unité est ce qui est indexé."""
    pdf = pymupdf.open(config.CORPUS_DIR / meta['fichier'])
    elements, units = [], []
    doc_id, title = meta['document_id'], meta['titre']
    for pno, page in enumerate(pdf, 1):
        blocks = _blocks(page)
        excluded: list[pymupdf.Rect] = []
        # ---------------------------------------------------------- tableaux
        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        for j, tab in enumerate(tables, 1):
            rect = pymupdf.Rect(tab.bbox)
            excluded.append(rect)
            rows = [[_clean(page.get_textbox(pymupdf.Rect(c))) if c else '' for c in r.cells] for r in tab.rows]
            rows = [r for r in rows if any(r)]
            if len(rows) < 2:
                continue
            ref, caption = _caption_near(blocks, rect, TAB_REF, above=True)
            ref = ref or f'TAB-P{pno:02d}-{j}'
            caption = caption or f'Tableau page {pno}'
            header, body = rows[0], rows[1:]
            lines = [' | '.join(header)] + [' | '.join(r) for r in body]
            el = {**_base(meta, pno), 'element_id': ref, 'type': 'tableau', 'caption': caption,
                  'content': caption + '\n' + '\n'.join(lines)}
            elements.append(el)
            for i, r in enumerate(body, 1):
                pairs = '; '.join(f'{h}: {v}' for h, v in zip(header, r) if v)
                units.append({'unit_id': f'{doc_id}/{ref}#r{i}', 'document_id': doc_id, 'element_id': ref,
                              'text': f'[{title}] {caption} - {pairs}'})
        # ---------------------------------------------------------- figures
        seen = set()
        for j, info in enumerate(page.get_images(full=True), 1):
            xref = info[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                raw = pdf.extract_image(xref)
                im = Image.open(io.BytesIO(raw['image'])).convert('RGB')
            except Exception:
                continue
            if im.width < 120 or im.height < 85:
                continue
            rects = page.get_image_rects(xref)
            rect = rects[0] if rects else pymupdf.Rect(0, 0, 0, 0)
            ref, caption = _caption_near(blocks, rect, FIG_REF, above=False)
            if ref is None:  # légende parfois au-dessus (manuel d'origine)
                ref, caption = _caption_near(blocks, rect, FIG_REF, above=True)
            ref = ref or f'IMG-P{pno:02d}-{j}'
            caption = caption or f'Figure page {pno}'
            img_path = image_dir / f'{doc_id}_{ref}.png'
            im.save(img_path)
            elements.append({**_base(meta, pno), 'element_id': ref, 'type': 'figure', 'caption': caption,
                             'content': caption + ' (image originale disponible)', 'image_path': str(img_path)})
            units.append({'unit_id': f'{doc_id}/{ref}', 'document_id': doc_id, 'element_id': ref,
                          'text': f'[{title}] {caption}'})
        # ---------------------------------------------------------- texte (hors tableaux et légendes)
        paras = []
        for r, text in blocks:
            if any(r.intersects(x) and (r & x).get_area() > 0.5 * r.get_area() for x in excluded):
                continue
            first = _clean(text)
            if TAB_REF.match(first):
                continue
            paras.append(first)
        chunks, cur = [], ''
        for p in paras:
            if cur and len(cur) + len(p) > CHUNK_CHARS:
                chunks.append(cur); cur = ''
            cur = f'{cur} {p}'.strip()
        if cur:
            chunks.append(cur)
        for k, chunk in enumerate(chunks, 1):
            if len(chunk) < 40:
                continue
            ref = f'TXT-P{pno:02d}-{k}'
            elements.append({**_base(meta, pno), 'element_id': ref, 'type': 'texte', 'content': chunk})
            units.append({'unit_id': f'{doc_id}/{ref}', 'document_id': doc_id, 'element_id': ref,
                          'text': f'[{title}] {chunk}'})
    pdf.close()
    return elements, units


def parse_corpus(image_dir: Path) -> tuple[list[dict], list[dict]]:
    image_dir.mkdir(parents=True, exist_ok=True)
    elements, units = [], []
    for meta in load_metadata().values():
        e, u = parse_document(meta, image_dir)
        elements += e; units += u
    # identifiant global unique d'élément
    for e in elements:
        e['key'] = f"{e['document_id']}/{e['element_id']}"
    return elements, units
