"""Fine-tuning ciblé du reranker multilingue (section 5 du sujet).

1. Génère des triplets (question, passage pertinent, négatif difficile) à partir des tableaux du corpus.
2. Sépare entraînement / validation / test PAR DOCUMENT (aucun document partagé).
3. Entraîne le cross-encoder (boucle PyTorch minimale, CPU) et sauvegarde data/models/reranker-maintidoc.
4. Mesure MRR@10 sur les questions de validation et de test (base vs adapté).
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from maintidoc import config  # noqa: E402
from maintidoc.retrieval import Retriever, normalize  # noqa: E402

TEST_DOCS = {'UPS-MAN-030', 'UPS-ERR-031', 'SAN-ERR-011'}
VAL_DOCS = {'UPS-PROC-021', 'SAN-PSU-005', 'UPS-BAT-033'}
EXCLUDE = {'NOTE-EXT-099'}  # document de test de sécurité, jamais utilisé pour l'entraînement
OUT_DATA = ROOT / 'eval' / 'data'
rng = random.Random(42)


def rows_of(element):
    lines = element['content'].split('\n')
    header = [h.strip() for h in lines[1].split(' | ')]
    return header, [[c.strip() for c in l.split(' | ')] for l in lines[2:]]


def questions_for(header, row, model):
    h = [normalize(x) for x in header]
    d = dict(zip(h, row))
    m = model if rng.random() < 0.6 else ''
    on = f' sur le {m}' if m else ''
    qs = []
    code = next((d[k] for k in d if k.startswith('code') and re.match(r'^[A-Z]+-\d+', d[k] or '')), None)
    voyant = next((d[k] for k in d if k in ('voyant', 'led', 'combinaison observee')), None)
    couleur = (next((d[k] for k in d if k.startswith('couleur')), '') or '').lower()
    etat = (d.get('etat', '') or '').lower()
    first = (row[0] or '').strip()
    # v2 : formulations variées (registre courant, fautes, sans modèle) pour éviter le surapprentissage des gabarits
    if code:
        qs += rng.sample([f'Que signifie le code {code}{on} ?', f'Alarme {code}{on} : quelle conduite tenir ?',
                          f'{code} signification', f"J'ai {code} qui s'affiche{on}, c'est quoi ?",
                          f'Que faire face à {code} ?', f'le {code} c grave ?', f"explication de l'alerte {code}{on}",
                          f'{code} : faut-il escalader ?'], 3)
    if voyant:
        qs += rng.sample([f'Que signifie le voyant {voyant} {couleur} {etat}{on} ?',
                          f'{m or "Équipement"} : voyant {voyant} {couleur}, que faire ?',
                          f'Pourquoi le voyant {voyant} est {couleur}{on} ?',
                          f"voyant {couleur} allumé sur {voyant}, c'est normal ?",
                          f"Le témoin {voyant} s'allume en {couleur}, quelle est la cause ?",
                          f'{voyant} {couleur} {etat} ça veut dire quoi'], 3)
    if not code and not voyant and first:
        qs += rng.sample([f'{first}{on} ?', f'Quelle est la valeur de « {first} »{on} ?',
                          f'Que dit la documentation{on} à propos de {first.lower()} ?',
                          f"Donne-moi l'information « {first.lower()} »{on}", f'{first.lower()} : quelle valeur{on} ?',
                          f'Je cherche {first.lower()}{on}'], 3)
    return [re.sub(r'\s+', ' ', q).strip() for q in qs]


def build_dataset(r: Retriever):
    units = {u['unit_id']: u for u in r.units}
    by_doc_units: dict[str, list[str]] = {}
    for u in r.units:
        by_doc_units.setdefault(u['document_id'], []).append(u['unit_id'])
    data = {'train': [], 'val': [], 'test': []}
    for key, el in r.elements.items():
        doc = el['document_id']
        if el['type'] != 'tableau' or doc in EXCLUDE:
            continue
        split = 'test' if doc in TEST_DOCS else 'val' if doc in VAL_DOCS else 'train'
        header, rows = rows_of(el)
        model = el['modeles'][0]
        for i, row in enumerate(rows, 1):
            pos_id = f"{doc}/{el['element_id']}#r{i}"
            if pos_id not in units:
                continue
            for q in questions_for(header, row, model):
                negs = []
                same_table = [f"{doc}/{el['element_id']}#r{j}" for j in range(1, len(rows) + 1) if j != i]
                same_table = [u for u in same_table if u in units]
                if same_table:
                    negs.append(rng.choice(same_table))
                # négatif difficile : meilleure unité lexicale d'un AUTRE document (souvent un autre modèle)
                allowed = {d for d in r.metadata if d != doc and d not in EXCLUDE}
                lex = [u for u in r.lexical(q, allowed, limit=5)]
                if lex:
                    negs.append(lex[0])
                for neg in negs:
                    data[split].append({'question': q, 'positive': units[pos_id]['text'], 'negative': units[neg]['text'],
                                        'pos_id': pos_id, 'neg_id': neg, 'cle': row[0]})
    return data


def verify_and_cap(data: dict, cap: int = 1500) -> tuple[dict, dict]:
    """Vérification automatique des triplets (le sujet demande des exemples « vérifiés ») puis plafond de `cap`.
    Règles : le positif contient l'élément demandé (première cellule de la ligne) ; le négatif ne le contient pas
    (sinon c'est un faux négatif) ; pas de triplet en double ; positif et négatif différents."""
    from maintidoc.retrieval import normalize
    stats = {'generes': sum(len(v) for v in data.values()), 'positif_sans_element': 0, 'negatif_ambigu': 0,
             'doublons': 0}
    out, seen = {k: [] for k in data}, set()
    for split, rows in data.items():
        for ex in rows:
            key = normalize(ex['cle'] or '')
            sig = (ex['question'], ex['pos_id'], ex['neg_id'])
            if sig in seen or ex['pos_id'] == ex['neg_id']:
                stats['doublons'] += 1
                continue
            if key and key not in normalize(ex['positive']):
                stats['positif_sans_element'] += 1
                continue
            neg = normalize(ex['negative'])
            if key and len(key) > 2 and f': {key};' in neg.replace(' - ', '; ') + ';':
                stats['negatif_ambigu'] += 1  # le négatif porte le même élément : faux négatif probable
                continue
            seen.add(sig)
            out[split].append(ex)
    total = sum(len(v) for v in out.values())
    if total > cap:  # plafond : on réduit l'entraînement, validation et test restent intacts
        keep = cap - len(out['val']) - len(out['test'])
        out['train'] = rng.sample(out['train'], max(0, keep))
    stats['retenus'] = {k: len(v) for k, v in out.items()}
    stats['total_retenu'] = sum(stats['retenus'].values())
    return out, stats


def mrr_at_10(model, r: Retriever, examples):
    """Classement de la bonne unité parmi les 20 candidats hybrides (sans reranker)."""
    scores = []
    seen = set()
    for ex in examples:
        if ex['question'] in seen:
            continue
        seen.add(ex['question'])
        allowed = set(r.metadata) - EXCLUDE
        cand = list(dict.fromkeys(r.lexical(ex['question'], allowed, 10) +
                                  r.dense(ex['question'], ['tech_san', 'tech_ups', 'responsables'], None, 10)))
        if ex['pos_id'] not in cand:
            scores.append(0.0); continue
        texts = [next(u['text'] for u in r.units if u['unit_id'] == c) for c in cand]
        s = model.predict([(ex['question'], t) for t in texts], show_progress_bar=False)
        rank = 1 + sum(1 for x in s if x > s[cand.index(ex['pos_id'])])
        scores.append(1.0 / rank if rank <= 10 else 0.0)
    return round(float(np.mean(scores)), 4), len(scores)


def train(base_model, train_rows, epochs=2, batch=16, lr=2e-5):
    model = base_model
    hf, tok = model.model, model.tokenizer
    pairs = []
    for ex in train_rows:
        pairs.append((ex['question'], ex['positive'], 1.0))
        pairs.append((ex['question'], ex['negative'], 0.0))
    pairs = list({(a, b): (a, b, c) for a, b, c in pairs}.values())
    opt = torch.optim.AdamW(hf.parameters(), lr=lr, weight_decay=0.01)
    steps = epochs * ((len(pairs) + batch - 1) // batch)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, (steps - s) / steps))
    lossf = torch.nn.BCEWithLogitsLoss()
    hf.train()
    log, step, t0 = [], 0, time.time()
    for ep in range(epochs):
        rng.shuffle(pairs)
        for k in range(0, len(pairs), batch):
            b = pairs[k:k + batch]
            enc = tok([x[0] for x in b], [x[1] for x in b], padding=True, truncation=True, max_length=256,
                      return_tensors='pt')
            logits = hf(**enc).logits.view(-1)
            loss = lossf(logits, torch.tensor([x[2] for x in b]))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(hf.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
            step += 1
            if step % 20 == 0:
                log.append({'step': step, 'loss': round(loss.item(), 4), 'sec': round(time.time() - t0)})
                print(log[-1], flush=True)
    hf.eval()
    return model, {'pairs': len(pairs), 'steps': steps, 'seconds': round(time.time() - t0), 'loss_log': log}


def main():
    from sentence_transformers import CrossEncoder
    torch.manual_seed(42)
    if sys.platform == 'win32':  # empêche la mise en veille pendant l'entraînement
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    r = Retriever(reranker='none')
    data, verification = verify_and_cap(build_dataset(r))
    print('vérification', verification, flush=True)
    OUT_DATA.mkdir(parents=True, exist_ok=True)
    for split, rows in data.items():
        with open(OUT_DATA / f'reranker_{split}.jsonl', 'w', encoding='utf-8') as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
    sizes = {k: len(v) for k, v in data.items()}
    print('triplets', sizes, flush=True)
    base = CrossEncoder(config.RERANKER_BASE, device='cpu', max_length=256)
    before = {'val': mrr_at_10(base, r, data['val']), 'test': mrr_at_10(base, r, data['test'])}
    print('base', before, flush=True)
    model, info = train(base, data['train'])
    after = {'val': mrr_at_10(model, r, data['val']), 'test': mrr_at_10(model, r, data['test'])}
    print('adapté', after, flush=True)
    config.RERANKER_FINETUNED.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(config.RERANKER_FINETUNED))
    report = {'triplets': sizes, 'verification': verification, 'split': {'test_docs': sorted(TEST_DOCS), 'val_docs': sorted(VAL_DOCS),
                                           'exclus': sorted(EXCLUDE)},
              'base_model': config.RERANKER_BASE, 'mrr10_base': before, 'mrr10_adapte': after, 'training': info}
    out = ROOT / 'eval' / 'results'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'finetuning.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    from maintidoc.tracking import log_run
    log_run('maintidoc-reranker', 'finetune-mmarco-mMiniLM', {'base': config.RERANKER_BASE, 'epochs': 2, 'lr': 2e-5,
            'batch': 16, **sizes}, {'mrr10_val_base': before['val'][0], 'mrr10_test_base': before['test'][0],
            'mrr10_val_ft': after['val'][0], 'mrr10_test_ft': after['test'][0]})


if __name__ == '__main__':
    main()
