"""Évaluation de MaintiDoc.

  python eval/run_eval.py retrieval        # métriques de recherche (Recall@5, nDCG@5, MRR@10), 5 configurations
  python eval/run_eval.py e2e [--limit N]  # 150 scénarios de bout en bout avec le vrai VLM (reprise possible)
  python eval/run_eval.py report           # synthèse -> eval/results/*.json + docs/rapport_evaluation.md
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from maintidoc import config, security  # noqa: E402
from maintidoc.retrieval import detect_models, load_image, normalize  # noqa: E402

RES = ROOT / 'eval' / 'results'
SCEN = json.loads((ROOT / 'eval' / 'scenarios.json').read_text(encoding='utf-8'))
PHOTOS = ROOT / 'eval' / 'photos'
USERS = {u['user_id']: u for u in json.loads((config.MCP_DATA_DIR / 'utilisateurs.json').read_text(encoding='utf-8'))}
TEST_DOCS = {'UPS-MAN-030', 'UPS-ERR-031', 'SAN-ERR-011'}
NEGATION = re.compile(r"\b(aucun|aucune|pas|ne figure|introuvable|n'existe|ne mentionne|ne contient|ne precise|"
                      r"non document|ne dispose|impossible)\b")


def resolve_gold(retriever, gold) -> set[str]:
    keys = set()
    for g in gold or []:
        if isinstance(g, str):
            if g in retriever.elements:
                keys.add(g)
        else:
            keys |= {k for k, e in retriever.elements.items()
                     if e['document_id'] == g['doc'] and g['contains'].lower() in e['content'].lower()}
    return keys


def metrics(ranked: list[str], gold: set[str]) -> dict:
    rec5 = len(set(ranked[:5]) & gold) / len(gold)
    dcg = sum(1 / math.log2(i + 2) for i, k in enumerate(ranked[:5]) if k in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(5, len(gold))))
    mrr = next((1 / (i + 1) for i, k in enumerate(ranked[:10]) if k in gold), 0.0)
    return {'recall@5': rec5, 'ndcg@5': dcg / idcg, 'mrr@10': mrr}


def avg(rows, key):
    return round(statistics.mean(r[key] for r in rows), 4) if rows else None


# ---------------------------------------------------------------------------------------------- recherche
def run_retrieval():
    from PIL import Image

    from maintidoc.retrieval import Retriever
    r = Retriever(reranker='none')
    configs = [('dense', 'dense', 'none'), ('bm25', 'bm25', 'none'), ('hybride RRF', 'hybrid', 'none'),
               ('hybride + reranker origine', 'hybrid', 'base'), ('hybride + reranker adapté', 'hybrid', 'finetuned')]
    per_cfg = defaultdict(list)
    image_cmp = defaultdict(list)
    top_scores = []
    for s in SCEN:
        e = s['expected']
        if s.get('category') == 'acces_validation' and e.get('action'):
            continue
        groups = USERS.get(s['user'], {}).get('groupes', [])
        gold = resolve_gold(r, e.get('gold'))
        models = detect_models(s['question']) or None
        img = Image.open(PHOTOS / s['photo']).convert('RGB') if s.get('photo') else None
        model_img = None
        if img is not None:
            m, sim, margin = r.identify_model(img, groups)
            model_img = [m] if m and margin >= 0.05 else None
        # score du meilleur passage (reranker adapté) : sert à calibrer le seuil d'abstention
        if s['category'] in ('texte', 'absent_contradictoire') and not s.get('photo'):
            hits = r.search(s['question'], groups, models=models, top_k=1, reranker='finetuned')
            top_scores.append({'id': s['id'], 'in_scope': e.get('status') != 'abstain',
                               'score': hits[0].rerank if hits else None})
        if not gold:
            continue
        for name, mode, rr in configs:
            hits = r.search(s['question'], groups, image=img, models=models or model_img, top_k=10, mode=mode, reranker=rr)
            m = metrics([h.key for h in hits], gold)
            per_cfg[name].append({**m, 'id': s['id'], 'cat': s['category'],
                                  'heldout': all(k.split('/')[0] in TEST_DOCS for k in gold)})
        if img is not None:
            for name, kw in (('texte seul', {}), ('multimodal (photo)', {'image': img, 'models': model_img})):
                hits = r.search(s['question'], groups, top_k=10, reranker='finetuned', **kw)
                image_cmp[name].append(metrics([h.key for h in hits], gold))
    table = {}
    for name, rows in per_cfg.items():
        held = [x for x in rows if x['heldout']]
        table[name] = {'n': len(rows), **{k: avg(rows, k) for k in ('recall@5', 'ndcg@5', 'mrr@10')},
                       'n_docs_test': len(held), **{f'{k} (docs test)': avg(held, k) for k in ('recall@5', 'ndcg@5', 'mrr@10')},
                       'par_categorie': {c: {k: avg([x for x in rows if x['cat'] == c], k)
                                             for k in ('recall@5', 'ndcg@5', 'mrr@10')}
                                         for c in sorted({x['cat'] for x in rows})}}
    img_table = {n: {'n': len(v), **{k: avg(v, k) for k in ('recall@5', 'ndcg@5', 'mrr@10')}} for n, v in image_cmp.items()}
    out = {'configurations': table, 'texte_vs_multimodal': img_table, 'scores_abstention': top_scores}
    RES.mkdir(parents=True, exist_ok=True)
    (RES / 'retrieval.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    for n, v in table.items():
        print(f"{n:30s} n={v['n']} R@5={v['recall@5']} nDCG@5={v['ndcg@5']} MRR@10={v['mrr@10']} | docs test : "
              f"MRR@10={v['mrr@10 (docs test)']} (n={v['n_docs_test']})")
    print(img_table)
    ins = sorted(x['score'] for x in top_scores if x['in_scope'] and x['score'] is not None)
    outs = sorted(x['score'] for x in top_scores if not x['in_scope'] and x['score'] is not None)
    print('scores in-scope min/med', ins[:3], statistics.median(ins), '| hors-scope', outs)
    from maintidoc.tracking import log_run
    for n, v in table.items():
        log_run('maintidoc-retrieval', n, {'config': n, 'embed': config.TEXT_EMBED_MODEL, 'index': r.version.split('|')[0]},
                {k: v[k] for k in ('recall@5', 'ndcg@5', 'mrr@10')})


# ---------------------------------------------------------------------------------------------- bout en bout
def _norm(t) -> str:
    return normalize(t if isinstance(t, str) else json.dumps(t, ensure_ascii=False))


def check_scenario(s, out, extra) -> list[tuple[str, bool]]:
    e, res = s['expected'], (out or {}).get('result') or {}
    ans = _norm(res.get('answer', ''))
    everything = _norm([res, out.get('ticket_draft') if out else None])
    checks = []
    st = res.get('status')
    if 'status' in e:
        if e['status'] == 'abstain':
            checks.append(('status', st != 'answered' or bool(NEGATION.search(ans))))
        else:
            checks.append(('status', st == e['status']))
    if e.get('model'):
        checks.append(('modele', out.get('model') == e['model']))
    for req in e.get('contains', []):
        checks.append((f'contient:{req}', bool(re.search(normalize(req), ans))))
    for bad in e.get('not_contains', []):
        checks.append((f'absent:{bad}', normalize(bad) not in everything))
    ev_docs = {x['document_id'] for x in out.get('evidence', [])} | {c['document_id'] for c in res.get('citations', [])}
    for d in e.get('forbidden_docs', []):
        checks.append((f'interdit:{d}', d not in ev_docs))
    if 'mcp_calls' in e:
        checks.append(('mcp_calls', out.get('mcp_calls') == e['mcp_calls']))
    if 'history' in e:
        checks.append(('historique', (out.get('history') is not None) == e['history']))
    if 'ticket' in e:
        t = (out.get('ticket') or {}).get('status')
        want = e['ticket']
        ok = {'none': not out.get('ticket_draft') and t is None, 'created': t == 'created', 'rejected': t == 'rejected',
              'awaiting': bool(out.get('awaiting_approval')), 'not_created': t != 'created'}[want]
        if extra.get('decision_error') is not None:
            ok = ok and extra['decision_error'] == bool(e.get('decision', {}).get('expect_error'))
        if extra.get('replay_ok') is not None:
            ok = ok and extra['replay_ok']
        checks.append((f'ticket:{want}', ok))
    if e.get('decision'):
        checks.append(('brouillon_propose', bool(extra.get('draft_proposed'))))
    for f in e.get('flags', []):
        checks.append((f'drapeau:{f}', f in (out.get('flags') or {})))
    if e.get('uncertainty'):
        checks.append(('incertitude', e['uncertainty'].lower() in ' '.join(res.get('uncertainties', [])).lower()))
    if e.get('clarification'):
        checks.append(('clarification', bool(res.get('clarification_question'))))
    keys = {f"{x['document_id']}/{x['element_id']}" for x in out.get('evidence', [])}
    if res.get('citations'):
        checks.append(('citations_valides', all(f"{c['document_id']}/{c['element_id']}" in keys for c in res['citations'])))
    return checks


def run_action(s, agent):
    from fastapi.testclient import TestClient

    from api import main
    from maintidoc import mcp_client
    e = s['expected']
    main.set_agent(agent)
    client = TestClient(main.app)
    if e['action'] == 'api_page':
        tok = client.post('/auth/token', json={'user_id': s['user']}).json()['access_token']
        code = client.get(f"/documents/{e['doc']}/pages/1", headers={'Authorization': f'Bearer {tok}'}).status_code
        return [('http', code == e['expect_status'])], {'http': code}
    if e['action'] == 'api_noauth':
        code = client.post('/ask', json={'question': 'Que signifie AL-01 ?'}).status_code
        return [('http', code == e['expect_status'])], {'http': code}
    eq, title, desc, prio = 'UPS-X500-02', 'Ticket test évaluation', 'Description approuvée par le responsable', 'basse'
    ch = security.content_hash(eq, title, desc, prio)
    idem = security.idempotency_key(s['id'], eq, ch)
    args = dict(user_id='u-tech-b', approver_id='u-resp-1', equipment_id=eq, title=title, description=desc,
                priority=prio, approval_token=security.approval_token('u-resp-1', eq, ch, idem), idempotency_key=idem)
    if e['action'] == 'mcp_tamper':
        [r] = mcp_client.call_tools([('create_ticket', {**args, 'description': desc + ' + ajout non approuvé'})])
        return [('refus_contenu_modifie', not r['ok'] and r['error'] == 'access_denied')], {'mcp': r}
    if e['action'] == 'mcp_replay':
        r1, r2 = mcp_client.call_tools([('create_ticket', args), ('create_ticket', args)])
        ok = r1['ok'] and r2['ok'] and r2['data']['duplicate'] and r1['data']['ticket_id'] == r2['data']['ticket_id']
        return [('idempotence', ok)], {'mcp': [r1, r2]}
    raise ValueError(e['action'])


def run_e2e(limit: int | None, ids: list[str] | None, runs_name: str = 'e2e_runs.jsonl'):
    from maintidoc.agent import Agent
    from maintidoc.llm import OllamaVLM
    from maintidoc.retrieval import get_retriever
    agent = Agent(get_retriever(), OllamaVLM())
    if sys.platform == 'win32':  # empêche la mise en veille pendant l'évaluation (rétabli à la fin du processus)
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    RES.mkdir(parents=True, exist_ok=True)
    runs_file = RES / runs_name
    done = {}
    if runs_file.exists():
        for line in runs_file.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            done[row['id']] = row
    # ordre entrelacé par catégorie : un arrêt anticipé laisse un échantillon équilibré
    by_cat = defaultdict(list)
    for s in SCEN:
        by_cat[s['category']].append(s)
    order = [x for grp in __import__('itertools').zip_longest(*by_cat.values()) for x in grp if x]
    if ids:
        order = [s for s in order if s['id'] in ids]
    todo = [s for s in order if s['id'] not in done][:limit]
    print(f'{len(done)} déjà faits, {len(todo)} à faire', flush=True)
    for s in todo:
        if config.MCP_DB.exists():
            config.MCP_DB.unlink()  # état MCP identique pour chaque scénario
        t0 = time.time()
        extra, out = {}, {}
        try:
            if s['expected'].get('action'):
                checks, extra = run_action(s, agent)
            else:
                img = (PHOTOS / s['photo']).read_bytes() if s.get('photo') else None
                out = agent.ask(s['user'], s['question'], img)
                d = s['expected'].get('decision')
                extra['draft_proposed'] = bool(out.get('awaiting_approval'))
                if d and out.get('awaiting_approval'):
                    draft = dict(out['ticket_draft'])
                    if d.get('edit_title'):
                        draft['title'] = d['edit_title']
                    try:
                        out = agent.decide(out['thread_id'], d['approver'], d['approved'], draft)
                        extra['decision_error'] = False
                    except PermissionError:
                        extra['decision_error'] = True
                    if d.get('replay'):
                        try:
                            agent.decide(out['thread_id'], d['approver'], True)
                            extra['replay_ok'] = False
                        except ValueError:
                            extra['replay_ok'] = True
                elif d:
                    extra['decision_error'] = None
                checks = check_scenario(s, out, extra)
        except Exception as ex:
            checks, extra = [('exception', False)], {'error': f'{type(ex).__name__}: {ex}'}
        row = {'id': s['id'], 'category': s['category'], 'seconds': round(time.time() - t0, 1),
               'passed': all(ok for _, ok in checks), 'checks': checks, 'extra': extra,
               'result': out.get('result'), 'model': out.get('model'), 'llm_calls': out.get('llm_calls'),
               'mcp_calls': out.get('mcp_calls'), 'ticket': out.get('ticket'), 'flags': out.get('flags'),
               'evidence': [f"{x['document_id']}/{x['element_id']}" for x in out.get('evidence', [])],
               'trace': out.get('trace')}
        with open(runs_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f"{s['id']:8s} {'OK ' if row['passed'] else 'KO '} {row['seconds']:6.1f}s "
              f"{[c for c, ok in checks if not ok]}", flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['retrieval', 'e2e', 'report'])
    ap.add_argument('--limit', type=int)
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--runs', default='e2e_runs.jsonl', help='fichier de résultats dans eval/results')
    a = ap.parse_args()
    if a.cmd == 'retrieval':
        run_retrieval()
    elif a.cmd == 'e2e':
        run_e2e(a.limit, a.ids, a.runs)
    else:
        from eval.report import build_report
        build_report()
