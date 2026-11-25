"""Recherche multimodale : dense (Qdrant) + lexicale (BM25) + visuelle (CLIP), fusion RRF, reranking.

Le contrôle d'accès est appliqué AVANT la récupération : filtre Qdrant sur `groupes_acces`
et restriction du corpus BM25 / figures aux documents autorisés.
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import config, ingestion
from . import ocr as ocr_mod

log = logging.getLogger(__name__)

MODEL_PATTERNS = [
    (re.compile(r'SAN[\s-]*LAB[\s-]*2000', re.I), 'SAN-LAB 2000'),
    (re.compile(r'SAN[\s-]*LAB[\s-]*3000', re.I), 'SAN-LAB 3000'),
    (re.compile(r'UPS[\s-]*X[\s-]*500', re.I), 'UPS-X500'),
    (re.compile(r'UPS[\s-]*X[\s-]*900', re.I), 'UPS-X900'),
]
STOP = set('le la les un une des de du d l et ou a au aux en sur pour par que qui quoi est sont ce cet cette ces '
           'il elle on se sa son ses mon ma mes je tu vous nous ne pas plus dans avec quel quelle quels quelles '
           'comment faire faut dois doit si y'.split())


PHOTO_VIEWS = [(0, 1.0, 0.0), (-5, 0.6, 1.2), (5, 0.6, 1.2), (0, 0.45, 1.2)]  # (rotation, luminosité, flou)
CLIP_CONFIDENT_MARGIN = 0.015  # calibré : photo ambiguë 0,007 ; photos nettes 0,02 à 0,12


def photo_views(im: Image.Image) -> list[Image.Image]:
    from PIL import ImageEnhance, ImageFilter
    out = []
    for angle, bright, blur in PHOTO_VIEWS:
        v = im.rotate(angle, expand=True, fillcolor=(58, 54, 50)) if angle else im
        if bright != 1.0:
            v = ImageEnhance.Brightness(v).enhance(bright)
        if blur:
            v = v.filter(ImageFilter.GaussianBlur(blur))
        out.append(v)
    return out


def detect_models(text: str) -> list[str]:
    return [name for pat, name in MODEL_PATTERNS if pat.search(text or '')]


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKD', text.lower())
    return ''.join(c for c in text if not unicodedata.combining(c))


def tokenize(text: str) -> list[str]:
    toks = []
    for t in re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)*', normalize(text)):
        if t in STOP:
            continue
        toks.append(t)
        if '-' in t:
            toks += [p for p in t.split('-') if p and p not in STOP]
    return toks


@dataclass
class Hit:
    key: str
    element: dict
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    rerank: float | None = None


class Retriever:
    """Index persistant (Qdrant embarqué ou serveur) reconstruit automatiquement si le corpus change."""

    _lock = threading.Lock()

    def __init__(self, reranker: str | None = None):
        from qdrant_client import QdrantClient
        self.index_dir = config.INDEX_DIR
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.qdrant = QdrantClient(url=config.QDRANT_URL) if config.QDRANT_URL else \
            QdrantClient(path=str(self.index_dir / 'qdrant'))
        self._text_model = self._clip = self._clip_text = None
        self._rerankers: dict[str, object] = {}
        self.reranker_mode = reranker or config.RERANKER_MODE
        self._load_or_build()

    # ------------------------------------------------------------------ modèles
    @property
    def text_model(self):
        if self._text_model is None:
            from sentence_transformers import SentenceTransformer
            self._text_model = SentenceTransformer(config.TEXT_EMBED_MODEL, device='cpu')
        return self._text_model

    @property
    def clip(self):
        if self._clip is None:
            from sentence_transformers import SentenceTransformer
            self._clip = SentenceTransformer(config.CLIP_MODEL, device='cpu')
        return self._clip

    def get_reranker(self, mode: str):
        if mode == 'none':
            return None
        if mode == 'finetuned' and not config.RERANKER_FINETUNED.exists():
            mode = 'base'
        if mode not in self._rerankers:
            from sentence_transformers import CrossEncoder
            path = str(config.RERANKER_FINETUNED) if mode == 'finetuned' else config.RERANKER_BASE
            self._rerankers[mode] = CrossEncoder(path, device='cpu', max_length=384)
        return self._rerankers[mode]

    def embed_text(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.text_model.encode(texts, normalize_embeddings=True, batch_size=32,
                                                 show_progress_bar=False), dtype=np.float32)

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        return np.asarray(self.clip.encode(images, normalize_embeddings=True, show_progress_bar=False),
                          dtype=np.float32)

    @property
    def clip_text(self):
        """Encodeur de texte CLIP multilingue (français compris), aligné sur l'espace image de CLIP ViT-B/32."""
        if self._clip_text is None:
            from sentence_transformers import SentenceTransformer
            self._clip_text = SentenceTransformer(config.CLIP_TEXT_MODEL, device='cpu')
        return self._clip_text

    def visual_from_text(self, query: str, groups, doc_ids, limit=5) -> list[tuple[str, float]]:
        try:
            v = self.clip_text.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        except Exception:  # modèle absent : la recherche texte -> image est simplement ignorée
            return []
        res = self.qdrant.query_points('figures', query=np.asarray(v, dtype=np.float32).tolist(),
                                       query_filter=self._doc_filter(groups, doc_ids),
                                       limit=limit * len(PHOTO_VIEWS)).points
        best: dict[str, float] = {}
        for p in res:
            best[p.payload['key']] = max(best.get(p.payload['key'], -1.0), float(p.score))
        return sorted(best.items(), key=lambda x: -x[1])[:limit]

    # ------------------------------------------------------------------ index
    def _load_or_build(self):
        from qdrant_client import models as qm
        state_file = self.index_dir / 'state.json'
        version = f"{ingestion.corpus_hash()}|{config.TEXT_EMBED_MODEL}|{config.CLIP_MODEL}|vues{len(PHOTO_VIEWS)}|ocr{int(ocr_mod.ENABLED)}|sec1"
        state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
        collections = {c.name for c in self.qdrant.get_collections().collections}
        if state.get('version') != version or not {'units', 'figures'} <= collections:
            with self._lock:
                elements, units = ingestion.parse_corpus(self.index_dir / 'images')
                for name in ('units', 'figures'):
                    if name in collections:
                        self.qdrant.delete_collection(name)
                by_key = {e['key']: e for e in elements}
                vecs = self.embed_text([u['text'] for u in units])
                self.qdrant.create_collection('units', vectors_config=qm.VectorParams(size=vecs.shape[1],
                                                                                         distance=qm.Distance.COSINE))
                self.qdrant.upsert('units', points=[
                    qm.PointStruct(id=i, vector=v.tolist(), payload={
                        **u, 'groupes_acces': by_key[f"{u['document_id']}/{u['element_id']}"]['groupes_acces']})
                    for i, (u, v) in enumerate(zip(units, vecs))])
                # Chaque figure est indexée avec des variantes « façon photo » (rotation, pénombre, flou) pour
                # rapprocher les photos de terrain des schémas propres du corpus.
                figs, views = [], []
                for e in elements:
                    if e['type'] == 'figure':
                        for view in photo_views(Image.open(e['image_path']).convert('RGB')):
                            figs.append(e); views.append(view)
                fvecs = self.embed_images(views)
                self.qdrant.create_collection('figures', vectors_config=qm.VectorParams(size=fvecs.shape[1],
                                                                                           distance=qm.Distance.COSINE))
                self.qdrant.upsert('figures', points=[
                    qm.PointStruct(id=i, vector=v.tolist(), payload={'key': e['key'], 'document_id': e['document_id'],
                                                                     'groupes_acces': e['groupes_acces']})
                    for i, (e, v) in enumerate(zip(figs, fvecs))])
                (self.index_dir / 'elements.json').write_text(json.dumps(elements, ensure_ascii=False), encoding='utf-8')
                (self.index_dir / 'units.json').write_text(json.dumps(units, ensure_ascii=False), encoding='utf-8')
                state = {'version': version, 'n_elements': len(elements), 'n_units': len(units)}
                state_file.write_text(json.dumps(state), encoding='utf-8')
        self.version = state['version']
        self.elements = {e['key']: e for e in json.loads((self.index_dir / 'elements.json').read_text(encoding='utf-8'))}
        self.units = json.loads((self.index_dir / 'units.json').read_text(encoding='utf-8'))
        self.metadata = self._load_catalog()
        from rank_bm25 import BM25Okapi
        self._bm25 = BM25Okapi([tokenize(u['text']) for u in self.units])

    def _load_catalog(self) -> dict[str, dict]:
        """Métadonnées documentaires synchronisées dans PostgreSQL (ou SQLite), puis relues depuis la base."""
        meta = ingestion.load_metadata()
        try:
            from .catalog import Catalog
            self.catalog = Catalog()
            self.catalog.sync(meta, list(self.elements.values()), self.version)
            return self.catalog.documents()
        except Exception as ex:  # base indisponible : on continue avec les métadonnées du corpus
            log.warning('Catalogue des métadonnées indisponible (%s) : lecture de metadata.json', ex)
            self.catalog = None
            return meta

    # ------------------------------------------------------------------ accès
    def allowed_documents(self, groups: list[str]) -> set[str]:
        return {d for d, m in self.metadata.items() if set(m['groupes_acces']) & set(groups)}

    def _doc_filter(self, groups: list[str], doc_ids: set[str] | None):
        from qdrant_client import models as qm
        must = [qm.FieldCondition(key='groupes_acces', match=qm.MatchAny(any=list(groups)))]
        if doc_ids is not None:
            must.append(qm.FieldCondition(key='document_id', match=qm.MatchAny(any=sorted(doc_ids))))
        return qm.Filter(must=must)

    # ------------------------------------------------------------------ recherche
    def dense(self, query: str, groups, doc_ids, limit=20) -> list[str]:
        v = self.embed_text([query])[0]
        res = self.qdrant.query_points('units', query=v.tolist(), query_filter=self._doc_filter(groups, doc_ids),
                                       limit=limit).points
        return [p.payload['unit_id'] for p in res]

    def lexical(self, query: str, allowed: set[str], limit=20) -> list[str]:
        scores = self._bm25.get_scores(tokenize(query))
        order = np.argsort(-scores)
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            if self.units[i]['document_id'] in allowed:
                out.append(self.units[i]['unit_id'])
            if len(out) >= limit:
                break
        return out

    def visual(self, image: Image.Image, groups, doc_ids, limit=5) -> list[tuple[str, float]]:
        v = self.embed_images([image])[0]
        res = self.qdrant.query_points('figures', query=v.tolist(), query_filter=self._doc_filter(groups, doc_ids),
                                       limit=limit * len(PHOTO_VIEWS)).points
        best: dict[str, float] = {}
        for p in res:  # plusieurs vues par figure : on garde la meilleure
            best[p.payload['key']] = max(best.get(p.payload['key'], -1.0), float(p.score))
        return sorted(best.items(), key=lambda x: -x[1])[:limit]

    def identify_model(self, image: Image.Image, groups) -> tuple[str | None, float, float]:
        """Modèle d'équipement le plus probable d'après la figure la plus proche (similarité CLIP)."""
        hits = self.visual(image, groups, None, limit=8)
        if not hits:
            return None, 0.0, 0.0
        per_model: dict[str, float] = {}
        for key, score in hits:
            models = self.elements[key]['modeles']
            if len(models) == 1:
                per_model[models[0]] = max(per_model.get(models[0], 0), score)
        if not per_model:
            return None, 0.0, 0.0
        ranked = sorted(per_model.items(), key=lambda x: -x[1])
        best, s1 = ranked[0]
        s2 = ranked[1][1] if len(ranked) > 1 else 0.0
        return best, s1, s1 - s2

    def unit_key(self, unit_id: str) -> str:
        doc, rest = unit_id.split('/', 1)
        return f"{doc}/{rest.split('#')[0]}"

    def search(self, query: str, groups: list[str], image: Image.Image | None = None,
               models: list[str] | None = None, top_k: int = config.EVIDENCE_TOP_K, mode: str = 'hybrid',
               reranker: str | None = None, rrf_k: int = 60, text_to_image: bool | None = None) -> list[Hit]:
        """mode : 'dense' | 'bm25' | 'hybrid'. reranker : 'none' | 'base' | 'finetuned' (défaut : config)."""
        if text_to_image is None:
            text_to_image = config.TEXT_TO_IMAGE
        allowed = self.allowed_documents(groups)
        doc_ids = None
        if models:
            restricted = {d for d in allowed if set(self.metadata[d]['modeles']) & set(models)}
            if not restricted:  # aucun document autorisé pour ce modèle : pas de réponse hors sujet
                return []
            doc_ids = restricted
            allowed = restricted
        if not allowed:
            return []
        ranked_lists: dict[str, list[str]] = {}
        if mode in ('dense', 'hybrid'):
            ranked_lists['dense'] = self.dense(query, groups, doc_ids)
        if mode in ('bm25', 'hybrid'):
            ranked_lists['bm25'] = self.lexical(query, allowed)
        fused: dict[str, Hit] = {}
        unit_texts: dict[str, list[str]] = {}
        for source, unit_ids in ranked_lists.items():
            seen = set()
            for rank, uid in enumerate(unit_ids, 1):
                key = self.unit_key(uid)
                unit_texts.setdefault(key, [])
                if uid not in unit_texts[key]:
                    unit_texts[key].append(uid)
                if key in seen:
                    continue
                seen.add(key)
                hit = fused.setdefault(key, Hit(key, self.elements[key]))
                hit.score += 1.0 / (rrf_k + rank)
                hit.sources.append(source)
        if image is not None:
            for rank, (key, _) in enumerate(self.visual(image, groups, doc_ids), 1):
                hit = fused.setdefault(key, Hit(key, self.elements[key]))
                hit.score += 1.0 / (rrf_k + rank)
                hit.sources.append('image')
                unit_texts.setdefault(key, [f'{key}'])
        if mode == 'hybrid' and text_to_image:
            # Recherche texte -> schéma : la question (en français) comparée aux images des figures, via l'encodeur
            # de texte CLIP multilingue aligné sur l'espace image de CLIP ViT-B/32.
            for rank, (key, _) in enumerate(self.visual_from_text(query, groups, doc_ids), 1):
                hit = fused.setdefault(key, Hit(key, self.elements[key]))
                hit.score += 1.0 / (rrf_k + rank)
                hit.sources.append('texte->image')
                unit_texts.setdefault(key, [f'{key}'])
        hits = sorted(fused.values(), key=lambda h: -h.score)[:20]
        rr = self.get_reranker(reranker or self.reranker_mode)
        if rr is not None and hits:
            unit_by_id = {u['unit_id']: u for u in self.units}
            pairs, owners = [], []
            for h in hits:
                texts = [unit_by_id[u]['text'] for u in unit_texts.get(h.key, []) if u in unit_by_id]
                if not texts:  # figure trouvée uniquement par l'image : on reclasse sa légende
                    texts = [f"[{h.element['titre']}] {h.element['content']}"]
                for t in texts[:4]:
                    pairs.append((query, t)); owners.append(h)
            scores = rr.predict(pairs, batch_size=32, show_progress_bar=False)
            for h, s in zip(owners, scores):
                h.rerank = float(s) if h.rerank is None else max(h.rerank, float(s))
            hits.sort(key=lambda h: -h.rerank)
        return hits[:top_k]

    def render_page(self, document_id: str, page: int, dpi: int = 90) -> bytes:
        import pymupdf
        meta = self.metadata[document_id]
        from .storage import get_storage
        with pymupdf.open(stream=get_storage().read(meta['fichier']), filetype='pdf') as pdf:
            return pdf[page - 1].get_pixmap(dpi=dpi).tobytes('png')


def load_image(data: bytes) -> Image.Image:
    if len(data) > config.MAX_IMAGE_BYTES:
        raise ValueError('Image trop volumineuse')
    im = Image.open(io.BytesIO(data))
    im.verify()
    im = Image.open(io.BytesIO(data)).convert('RGB')
    im.thumbnail((config.MAX_IMAGE_SIDE * 2, config.MAX_IMAGE_SIDE * 2))
    return im


_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        import atexit
        _retriever = Retriever()
        atexit.register(_retriever.qdrant.close)
    return _retriever
