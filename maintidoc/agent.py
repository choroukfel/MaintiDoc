"""Workflow agentique LangGraph de MaintiDoc.

check_access -> analyze (texte + photo) -> retrieve -> mcp_lookup (si besoin) -> generate -> verify
  -> [draft_ticket -> human_approval (interrupt) -> create_ticket] -> finalize

Les appels d'outils sont décidés par le code du graphe (intention de l'utilisateur), jamais par le texte
des documents ou du modèle : une injection ne peut donc pas déclencher d'appel MCP.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import config, security
from .llm import BudgetExceeded, LLMUnavailable, image_to_bytes
from .retrieval import CLIP_CONFIDENT_MARGIN, Retriever, detect_models, load_image
from .schemas import Answer, Citation, LLMAnswer, LLMTicket, TicketDraft, VisualAnalysis
from .vision import describe as describe_leds
from .vision import detect_lit_leds

USERS = {u['user_id']: u for u in json.loads((config.MCP_DATA_DIR / 'utilisateurs.json').read_text(encoding='utf-8'))}
EQUIPMENT = {e['equipment_id']: e for e in
             json.loads((config.MCP_DATA_DIR / 'equipements.json').read_text(encoding='utf-8'))}

EQUIP_RE = re.compile(r'\b(SAN-LAB-(?:2000|3000)-\d{2}|UPS-X(?:500|900)-\d{2})\b', re.I)
HISTORY_RE = re.compile(r'historique|derni[eè]res?\s+interventions?|ant[ée]c[ée]dents?|d[ée]j[aà]\s+eu|'
                        r'tickets?\b.{0,25}\bouverts?|r[ée]currence|r[ée]current|consulte', re.I)
TICKET_RE = re.compile(r'(?:pr[ée]pare|cr[ée]e|ouvre|r[ée]dige|ouvrir|cr[ée]er|pr[ée]parer)\w*\s+(?:un\s+|le\s+)?ticket|'
                       r'ticket\s+si\s+n[ée]cessaire', re.I)
CODE_RE = re.compile(r'\b(?:AL|A|F|E)-\d{2,3}\b')
ANY_CODE_RE = re.compile(r'\b(?!INC-|TCK-)[A-Z]{1,3}-\d{2,3}\b')  # codes d'alarme / d'événement cités par l'utilisateur
LED_RE = re.compile(r'\b(voyants?|led)\b', re.I)
DOC_REF_RE = re.compile(r'\b(note|fournisseur|bulletin|manuel|document|proc[ée]dure|[A-Z]+-[A-Z]+-\d{3})\b', re.I)
# Indices visuels -> modèle (codes affichés et étiquettes de voyants propres à chaque modèle)
CODE_MODEL = [(re.compile(r'\bAL-0\d\b'), 'SAN-LAB 2000'), (re.compile(r'\b(?:AL-3\d|E-3\d\d)\b'), 'SAN-LAB 3000'),
              (re.compile(r'\bA-\d\d\b'), 'UPS-X500'), (re.compile(r'\bF-\d\d\b'), 'UPS-X900')]
LABEL_MODEL = {'SURCHARGE': 'UPS-X900', 'SECTEUR': 'UPS-X900', 'BATT': 'UPS-X900', 'ALARME': 'UPS-X900',
               'EN LIGNE': 'UPS-X500', 'BYPASS': 'UPS-X500', 'BATTERIE': 'UPS-X500', 'DEFAUT': 'UPS-X500',
               'DÉFAUT': 'UPS-X500', 'FAN': 'SAN-LAB 3000', 'TEMP': 'SAN-LAB 3000', 'SYS': 'SAN-LAB 2000',
               'OVERLOAD': 'UPS-X900', 'ON BATT': 'UPS-X500'}
# Seuils d'abstention calibrés sur les scénarios (score du meilleur passage après reranking)
RERANK_THRESHOLD = {'finetuned': -2.0, 'base': 0.0}


class State(TypedDict, total=False):
    request_id: str
    user: dict
    question: str
    image: bytes | None
    intent: dict
    model: str | None
    model_evidence: dict
    equipment_id: str | None
    visual: dict | None
    hits: list[dict]
    evidence: list[dict]
    flags: dict
    history: list | None
    open_tickets: list | None
    mcp_notes: list[str]
    llm_answer: dict | None
    result: dict
    ticket_draft: dict | None
    ticket: dict | None
    llm_calls: int
    mcp_calls: int
    started: float
    trace: list[dict]
    route: str


PLACEHOLDER_RE = re.compile(r'\bou null\b|^null$|^none$|^n/?a$|nom imprim|texte de l', re.I)


def clean_visual(va: VisualAnalysis) -> VisualAnalysis:
    """Écarte les valeurs recopiées du gabarit et les listes de voyants manifestement non observées."""
    def ok(v):
        return v if v and not PLACEHOLDER_RE.search(v.strip()) else None
    inds = [i for i in va.lit_indicators if not (i.label and PLACEHOLDER_RE.search(i.label))]
    for i in inds:  # étiquettes numériques ou génériques (« 100 », « ALLUMÉ ») : non informatives
        if i.label and (re.fullmatch(r'[\W\d_]*(voyant)?[\W\d_]*', i.label, re.I) or i.label.upper() in ('ALLUMÉ', 'ALLUME', 'ON')):
            i.label = None
    if len(inds) >= 4 and not any(i.label for i in inds):  # toutes les couleurs sans étiquette : énumération recopiée
        inds = []
    return VisualAnalysis(readable=va.readable, equipment_model=ok(va.equipment_model), screen_text=ok(va.screen_text),
                          lit_indicators=inds, notes=ok(va.notes))


def _answer(status, text, **kw) -> dict:
    return Answer(status=status, answer=text, **kw).model_dump()


class Agent:
    def __init__(self, retriever: Retriever, llm, mcp_call: Callable[[list[tuple[str, dict]]], list[dict]] | None = None):
        from . import mcp_client
        self.retriever = retriever
        self.llm = llm
        self.mcp_call = mcp_call or mcp_client.call_tools
        self.graph = self._build()

    # ------------------------------------------------------------------ utilitaires
    def _trace(self, s: State, node: str, **info) -> list[dict]:
        return [*s.get('trace', []), {'node': node, 't': round(time.time() - s['started'], 2), **info}]

    def _llm(self, s: State, prompt: str, variables: dict, schema, images=None, num_predict=config.NUM_PREDICT):
        if s.get('llm_calls', 0) >= config.MAX_LLM_CALLS:
            raise BudgetExceeded('budget d\'appels au modèle atteint')
        if time.time() - s['started'] > config.MAX_SECONDS:
            raise BudgetExceeded('budget de temps atteint')
        s['llm_calls'] = s.get('llm_calls', 0) + 1
        return self.llm.structured(prompt, variables, schema, images=images, num_predict=num_predict)

    def _mcp(self, s: State, calls: list[tuple[str, dict]]) -> list[dict]:
        if s.get('mcp_calls', 0) + len(calls) > config.MAX_MCP_CALLS:
            raise BudgetExceeded('budget d\'appels MCP atteint')
        s['mcp_calls'] = s.get('mcp_calls', 0) + len(calls)
        return self.mcp_call(calls)

    # ------------------------------------------------------------------ noeuds
    def check_access(self, s: State) -> dict:
        user, q = s['user'], s['question']
        if not user or not user.get('groupes'):
            return {'result': _answer('refused', 'Accès refusé : utilisateur inconnu ou sans droits.'),
                    'route': 'end', 'trace': self._trace(s, 'check_access', refused='user')}
        if len(q) > config.MAX_QUESTION_CHARS:
            return {'result': _answer('refused', 'Question trop longue.'), 'route': 'end',
                    'trace': self._trace(s, 'check_access', refused='length')}
        inj = security.detect_injection(q)
        if inj:
            return {'result': _answer('refused', "Je ne peux pas modifier mes règles, contourner l'approbation humaine "
                                                 "ni transmettre des données à l'extérieur. Reformulez votre question "
                                                 "documentaire.", uncertainties=['Tentative d\'instruction détectée dans la demande.']),
                    'route': 'end', 'flags': {'injection_question': True},
                    'trace': self._trace(s, 'check_access', refused='injection')}
        risky = security.risky_request(q)
        if risky:
            return {'result': _answer('refused', f"Demande hors périmètre : MaintiDoc est un assistant documentaire et ne "
                                                 f"guide pas l'action « {risky} ». Contactez un technicien habilité et "
                                                 f"suivez la procédure validée du site."),
                    'route': 'end', 'trace': self._trace(s, 'check_access', refused='risky')}
        return {'route': 'ok', 'trace': self._trace(s, 'check_access')}

    def analyze(self, s: State) -> dict:
        q = s['question']
        intent = {'history': bool(HISTORY_RE.search(q)), 'ticket': bool(TICKET_RE.search(q))}
        votes: dict[str, float] = {}
        evidence: dict[str, Any] = {}

        def vote(model, w, why):
            if model:
                votes[model] = votes.get(model, 0) + w
                evidence.setdefault(model, []).append(why)

        m = EQUIP_RE.search(q)
        equipment_id = m.group(1).upper() if m else None
        if equipment_id and equipment_id in EQUIPMENT:
            vote(EQUIPMENT[equipment_id]['modele'], 10, 'identifiant équipement')
        for model in detect_models(q):
            vote(model, 10, 'cité dans la question')
        visual = None
        trace_info: dict[str, Any] = {}
        if s.get('image'):
            img = load_image(s['image'])
            clip_model, sim, margin = self.retriever.identify_model(img, s['user']['groupes'])
            trace_info['clip'] = {'model': clip_model, 'sim': round(sim, 3), 'margin': round(margin, 3)}
            vote(clip_model, 3 if margin >= CLIP_CONFIDENT_MARGIN else 1, f'similarité visuelle (marge {margin:.2f})')
            leds = detect_lit_leds(img)
            visual = {'readable': None, 'equipment_model': None, 'screen_text': None, 'lit_indicators': [],
                      'notes': None, 'detected_leds': leds}
            trace_info['leds'] = leds
            try:
                va, stats = self._llm(s, config.PROMPT_VERSIONS['visual'], {'question': q}, VisualAnalysis,
                                      images=[image_to_bytes(img)], num_predict=350)
                va = clean_visual(va)
                visual = {**va.model_dump(), 'detected_leds': leds}
                trace_info['vlm'] = stats
                trace_info['visual'] = visual
                text = ' '.join([va.equipment_model or '', va.screen_text or ''] +
                                [i.label or '' for i in va.lit_indicators])
                for model in detect_models(text):
                    vote(model, 3, 'nom lu sur la photo')
                for pat, model in CODE_MODEL:
                    if pat.search(va.screen_text or ''):
                        vote(model, 3, 'code affiché sur l\'écran')
                for ind in va.lit_indicators:
                    lab = (ind.label or '').upper().strip()
                    if lab in LABEL_MODEL:
                        vote(LABEL_MODEL[lab], 2, f'voyant « {lab} »')
            except (ValueError, BudgetExceeded) as ex:
                trace_info['vlm_error'] = str(ex)[:120]
        model = None
        if votes:
            ranked = sorted(votes.items(), key=lambda x: -x[1])
            if ranked[0][1] >= 3 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
                model = ranked[0][0]
        if equipment_id is None and model and (intent['history'] or intent['ticket']):
            candidates = [e for e, d in EQUIPMENT.items() if d['modele'] == model]
            if len(candidates) == 1:
                equipment_id = candidates[0]
        if equipment_id and equipment_id in EQUIPMENT and \
                EQUIPMENT[equipment_id]['groupe_acces'] not in s['user']['groupes']:
            return {'intent': intent, 'equipment_id': equipment_id, 'model': model, 'route': 'end',
                    'llm_calls': s.get('llm_calls', 0), 'trace': self._trace(s, 'analyze', refused='equipment'),
                    'result': _answer('refused', f"Accès refusé : votre profil n'est pas autorisé sur l'équipement "
                                                 f"{equipment_id}. Adressez-vous à un responsable si nécessaire.",
                                      uncertainties=[f'Accès à {equipment_id} refusé.'])}
        if (model is None and not s.get('image') and LED_RE.search(q) and not CODE_RE.search(q)
                and not DOC_REF_RE.search(q)):
            return {'intent': intent, 'model': None, 'equipment_id': equipment_id, 'route': 'end',
                    'llm_calls': s.get('llm_calls', 0), 'trace': self._trace(s, 'analyze', clarify='model'),
                    'result': _answer('needs_clarification',
                                      "La signification d'un voyant dépend du modèle d'équipement : un même voyant orange "
                                      "correspond par exemple à une alarme disque sur le SAN-LAB 2000, à un ventilateur sur "
                                      "le SAN-LAB 3000 ou à la batterie sur l'UPS-X500.",
                                      uncertainties=["Modèle d'équipement non précisé."],
                                      clarification_question="Sur quel équipement se trouve ce voyant (modèle ou identifiant, "
                                                             "par exemple SAN-LAB-2000-01) et quel code est affiché ? "
                                                             "Une photo du panneau peut aussi être jointe.")}
        out = {'intent': intent, 'model': model, 'model_evidence': evidence.get(model, []) if model else [],
               'equipment_id': equipment_id, 'visual': visual, 'llm_calls': s.get('llm_calls', 0),
               'trace': self._trace(s, 'analyze', model=model, votes=votes, **trace_info), 'route': 'retrieve'}
        if s.get('image') and model is None:
            out['result'] = _answer(
                'needs_clarification',
                "La photo ne permet pas d'identifier l'équipement avec certitude ; je ne peux pas interpréter ce voyant "
                "sans risque d'erreur.",
                uncertainties=['Modèle d\'équipement non identifié sur la photo.'],
                clarification_question="Pouvez-vous indiquer le modèle (plaque signalétique) et le code affiché à l'écran, "
                                       "ou envoyer une photo plus nette du panneau complet ?")
            out['route'] = 'end'
        return out

    def retrieve(self, s: State) -> dict:
        parts = [s['question']]
        if s.get('model'):
            parts.append(s['model'])
        v = s.get('visual')
        if v:
            if v.get('screen_text'):
                parts.append(v['screen_text'])
            parts += [f"voyant {i.get('label') or ''} {i['color']}" for i in v.get('lit_indicators', [])]
            parts += [f'voyant {c}' for c in (v.get('detected_leds') or {}) if c != 'vert']
        query = ' '.join(parts)
        img = load_image(s['image']) if s.get('image') else None
        hits = self.retriever.search(query, s['user']['groupes'], image=img,
                                     models=[s['model']] if s.get('model') else None)
        mode = self.retriever.reranker_mode
        threshold = RERANK_THRESHOLD.get(mode)
        if s.get('image') and s.get('model'):
            # Photo + modèle identifié : la pertinence vient du filtre modèle et de l'image ; le seuil, calibré
            # sur des questions textuelles précises, rejetterait à tort les questions vagues (« que faut-il faire ? »).
            threshold = None
        best = hits[0].rerank if hits and hits[0].rerank is not None else None
        hit_dicts = [{'key': h.key, 'rerank': h.rerank, 'rrf': round(h.score, 4), 'sources': h.sources} for h in hits]
        trace = self._trace(s, 'retrieve', hits=[(h['key'], round(h['rerank'] or 0, 2)) for h in hit_dicts])
        if not hits or (threshold is not None and best is not None and best < threshold):
            return {'hits': hit_dicts, 'trace': trace, 'route': 'end', 'result': _answer(
                'needs_clarification',
                "Je n'ai trouvé aucune source autorisée suffisamment pertinente pour répondre à cette question.",
                uncertainties=['Aucune source pertinente accessible avec vos droits.'],
                clarification_question="Pouvez-vous préciser l'équipement concerné (modèle, code d'alarme) ? "
                                       "Si la question porte sur un document auquel vous n'avez pas accès, adressez-vous "
                                       "à un responsable.")}
        # Code cité dans la question mais absent de toute la documentation autorisée : abstention sans appel au modèle
        allowed = self.retriever.allowed_documents(s['user']['groupes'])
        corpus_text = ' '.join(e['content'] for e in self.retriever.elements.values() if e['document_id'] in allowed)
        q_text = s['question']
        unknown = [c for c in ANY_CODE_RE.findall(q_text)
                   if not EQUIP_RE.search(c) and not re.search(rf'(?<![\w-]){re.escape(c)}(?![\w-])', corpus_text)]
        if unknown and q_text:
            return {'hits': hit_dicts, 'trace': self._trace(s, 'retrieve', unknown_codes=unknown), 'route': 'end',
                    'result': _answer('needs_clarification',
                                      f"Le code {', '.join(unknown)} ne figure dans aucun document auquel vous avez accès ; "
                                      f"je ne peux pas en donner la signification.",
                                      uncertainties=[f"Code absent de la documentation autorisée : {', '.join(unknown)}."],
                                      clarification_question="Pouvez-vous vérifier le code affiché (photo de l'écran) et "
                                                             "le modèle exact de l'équipement ?")}
        # Évidences : contenus traités comme données non fiables, injections neutralisées
        evidence, flags = [], dict(s.get('flags') or {})
        docs = set()
        for i, h in enumerate(hits, 1):
            el = self.retriever.elements[h.key]
            content, suspect = security.sanitize(el['content'][:config.EVIDENCE_MAX_CHARS])
            if suspect:
                flags['injection_evidence'] = True
            evidence.append({'eid': f'E{i}', 'key': h.key, 'document_id': el['document_id'], 'page': el['page'],
                             'element_id': el['element_id'], 'type': el['type'], 'titre': el['titre'],
                             'confiance': el['niveau_confiance'], 'content': content, 'suspect': suspect})
            docs.add(el['document_id'])
        contradictions = []
        for d in docs:
            for old in self.retriever.metadata[d].get('modifie', []):
                contradictions.append((d, old))  # même si l'ancien document n'est pas dans les preuves
        if contradictions:
            flags['contradiction'] = contradictions
        if any(e['confiance'] != 'valide' for e in evidence):
            flags['unverified_source'] = True
        route = 'mcp' if s.get('equipment_id') and (s['intent']['history'] or s['intent']['ticket']) else 'generate'
        return {'hits': hit_dicts, 'evidence': evidence, 'flags': flags, 'trace': trace, 'route': route}

    def mcp_lookup(self, s: State) -> dict:
        uid, eq = s['user']['user_id'], s['equipment_id']
        notes, history, tickets = [], None, None
        try:
            res = self._mcp(s, [('get_equipment_history', {'user_id': uid, 'equipment_id': eq, 'limit': 5}),
                                ('get_open_tickets', {'user_id': uid, 'equipment_id': eq})])
            for name, r in zip(('history', 'tickets'), res):
                if not r.get('ok'):
                    notes.append(f"Accès MCP refusé ({name}) : {r.get('message')}")
                    continue
                data = r['data']
                for item in data:  # les réponses d'outils sont aussi des données non fiables
                    for k, v in list(item.items()):
                        if isinstance(v, str):
                            item[k], suspect = security.sanitize(v)
                            if suspect:
                                s.setdefault('flags', {})['injection_mcp'] = True
                if name == 'history':
                    history = data
                else:
                    tickets = data
        except BudgetExceeded as ex:
            notes.append(str(ex))
        except Exception as ex:  # serveur MCP indisponible
            notes.append(f'Service de maintenance indisponible : {type(ex).__name__}')
        return {'history': history, 'open_tickets': tickets, 'mcp_notes': notes, 'mcp_calls': s.get('mcp_calls', 0),
                'flags': s.get('flags', {}), 'trace': self._trace(s, 'mcp_lookup', calls=s.get('mcp_calls', 0),
                                                                    denied=len(notes)), 'route': 'generate'}

    def generate(self, s: State) -> dict:
        ev_text = '\n\n'.join(
            f"[{e['eid']}] {e['document_id']} « {e['titre']} » | page {e['page']} | {e['element_id']} | {e['type']}"
            f"{' | SOURCE NON VÉRIFIÉE' if e['confiance'] != 'valide' else ''}\n{e['content']}" for e in s['evidence'])
        ctx = []
        if s.get('model'):
            ctx.append(f"Modèle d'équipement identifié : {s['model']} ({', '.join(s.get('model_evidence', []))}).")
        v = s.get('visual')
        if v:
            inds = ', '.join(f"{i.get('label') or '?'} ({i['color']})" for i in v.get('lit_indicators', [])) or 'aucun'
            ctx.append(f"Observation de la photo (lecture automatique, peut être imprécise) : voyants lus : {inds} ; "
                       f"écran : {v.get('screen_text') or 'illisible'}.")
            ctx.append(f"Analyse d'image (détection des couleurs) : {describe_leds(v.get('detected_leds') or {})}. "
                       f"Explique en priorité le voyant d'alerte (non vert).")
            # Alignement image -> texte : lignes de tableaux des preuves qui mentionnent la couleur d'alerte détectée
            alerts = [c for c in (v.get('detected_leds') or {}) if c != 'vert'] or \
                     (['vert'] if v.get('detected_leds') else [])
            rows = []
            for e in s['evidence']:
                if e['type'] != 'tableau':
                    continue
                for line in e['content'].split('\n')[2:]:
                    if any(re.search(rf'\b{c}\b', line, re.I) for c in alerts):
                        rows.append(f"[{e['eid']}] {line}")
            if rows:
                ctx.append(f"Lignes des preuves correspondant à la couleur détectée ({', '.join(alerts)}) :\n"
                           + '\n'.join(rows[:8]) + "\nUne photo ne permet pas de distinguer un voyant fixe d'un "
                           "voyant clignotant : si plusieurs lignes correspondent, cite-les et précise l'information à vérifier.")
        if s.get('history') is not None:
            h = '; '.join(f"{i['id']} {i['date']} {i['code']} {i['probleme']}" for i in s['history']) or 'aucune'
            ctx.append(f"Historique MCP de {s['equipment_id']} (données non fiables) : {h}.")
        if s.get('open_tickets') is not None:
            t = '; '.join(f"{i['id']} {i['titre']} ({i['statut']})" for i in s['open_tickets']) or 'aucun'
            ctx.append(f'Tickets ouverts : {t}.')
        for new, old in (s.get('flags') or {}).get('contradiction', []):
            ctx.append(f'ATTENTION : {new} modifie {old} ; les valeurs peuvent diverger, signale-le.')
        variables = {'question': s['question'], 'context': '\n'.join(ctx), 'evidence': ev_text}
        llm_answer, stats, err = None, [], None
        for attempt in range(2):
            try:
                a, st = self._llm(s, config.PROMPT_VERSIONS['answer'], variables, LLMAnswer)
                llm_answer = a.model_dump(); stats.append(st)
                break
            except LLMUnavailable as ex:
                err = str(ex)[:150]; break
            except ValueError as ex:
                err = str(ex)[:150]
            except BudgetExceeded as ex:
                err = str(ex); break
        return {'llm_answer': llm_answer, 'llm_calls': s.get('llm_calls', 0),
                'trace': self._trace(s, 'generate', stats=stats, error=err), 'route': 'verify'}

    def verify(self, s: State) -> dict:
        la = s.get('llm_answer')
        flags = s.get('flags') or {}
        if la is None:
            return {'result': _answer('needs_clarification', "La réponse n'a pas pu être générée de façon fiable.",
                                      uncertainties=['Échec ou budget dépassé lors de la génération.']),
                    'route': 'end', 'trace': self._trace(s, 'verify', ok=False)}
        by_eid = {e['eid']: e for e in s['evidence']}
        uncertainties = list(dict.fromkeys(u.strip() for u in (la.get('uncertainties') or []) if u and u.strip()))
        cited, invalid = [], []
        for c in la.get('citations') or []:
            eid = str(c).strip().strip('[]').upper()
            if eid in by_eid:
                if by_eid[eid] not in cited:
                    cited.append(by_eid[eid])
            else:
                invalid.append(str(c))
        if invalid:
            uncertainties.append(f"Références invalides retirées : {', '.join(invalid)}.")
        answer_text = la['answer']
        # Filtre de sortie : aucune adresse ni instruction d'exfiltration ne sort
        answer_text, leaked = security.sanitize(answer_text)
        if leaked:
            uncertainties.append('Contenu suspect retiré de la réponse.')
        # Contrôle de soutien : les codes cités dans la réponse doivent figurer dans les preuves citées
        status = la['status']
        if status == 'answered' and not cited:
            ans_tokens = set(CODE_RE.findall(answer_text)) | {w for w in re.findall(r'\w{5,}', answer_text.lower())}
            best, best_score = None, 0.0
            for e in s['evidence']:
                etoks = set(CODE_RE.findall(e['content'])) | {w for w in re.findall(r'\w{5,}', e['content'].lower())}
                score = len(ans_tokens & etoks) / max(1, len(ans_tokens))
                if score > best_score:
                    best, best_score = e, score
            if best is not None and best_score >= 0.3:
                cited = [best]
                uncertainties.append('Citation attribuée automatiquement par recouvrement avec la source (non fournie par le modèle).')
            else:
                status = 'needs_clarification'
                uncertainties.append('Réponse sans source vérifiable : à confirmer.')
        cited_text = ' '.join(e['content'] for e in cited) + ' ' + s['question']
        unsupported = sorted({c for c in CODE_RE.findall(answer_text) if c not in cited_text})
        if unsupported:
            uncertainties.append(f"Codes non trouvés dans les sources citées : {', '.join(unsupported)}.")
        for new, old in flags.get('contradiction', []):
            uncertainties.append(f'Le document {new} modifie {old} : en cas de divergence, demander l\'arbitrage du '
                                 f'responsable maintenance.')
        if flags.get('injection_evidence') or flags.get('injection_mcp'):
            uncertainties.append('Une source contenait des instructions suspectes : elles ont été ignorées.')
        if any(e['confiance'] != 'valide' for e in cited):
            uncertainties.append('Une source citée est non vérifiée (document externe).')
        uncertainties += s.get('mcp_notes') or []
        clar = (la.get('clarification_question') or '').strip() or None
        intent = s.get('intent', {})
        if (intent.get('history') or intent.get('ticket')) and not s.get('equipment_id'):
            accessible = [e for e, d in EQUIPMENT.items() if d['groupe_acces'] in s['user']['groupes']
                          and (not s.get('model') or d['modele'] == s['model'])]
            clar = f"Quel équipement est concerné ? ({', '.join(accessible)})"
            uncertainties.append('Équipement non précisé : historique non consulté.')
        proposed, duplicate = None, None
        if intent.get('ticket') and s.get('equipment_id') and status == 'answered' and s.get('open_tickets') is not None:
            # Sujet du ticket : codes de la demande et de la photo ; la réponse seulement à défaut (elle peut citer
            # d'autres alarmes de l'historique, ce qui ferait croire à tort à un doublon).
            codes = set(CODE_RE.findall(s['question'] + ' ' + json.dumps(s.get('visual') or {}))) or \
                set(CODE_RE.findall(answer_text))
            for t in s['open_tickets']:
                if codes & set(CODE_RE.findall(t['titre'])):
                    duplicate = t
            if duplicate:
                answer_text += f" Un ticket ouvert couvre déjà ce sujet : {duplicate['id']} ({duplicate['titre']}) ; aucun nouveau ticket n'est proposé."
            else:
                proposed = 'draft_ticket'
        result = Answer(status=status, answer=answer_text,
                        citations=[Citation(document_id=e['document_id'], page=e['page'], element_id=e['element_id'])
                                   for e in cited],
                        uncertainties=list(dict.fromkeys(uncertainties)),
                        clarification_question=clar if (status != 'answered' or clar) else None,
                        proposed_action=proposed).model_dump()
        return {'result': result, 'route': 'draft' if proposed else 'end',
                'trace': self._trace(s, 'verify', cited=len(cited), invalid=len(invalid), duplicate=bool(duplicate))}

    def draft_ticket(self, s: State) -> dict:
        user, res = s['user'], dict(s['result'])
        if not user.get('peut_preparer_ticket'):
            res['proposed_action'] = None
            res['uncertainties'] = [*res['uncertainties'], 'Votre profil ne permet pas de préparer un ticket.']
            return {'result': res, 'route': 'end', 'trace': self._trace(s, 'draft_ticket', allowed=False)}
        refs = [f"{c['document_id']} p.{c['page']} {c['element_id']}" for c in res['citations']]
        history = '; '.join(f"{i['id']} {i['date']} {i['probleme']}" for i in (s.get('history') or [])[:4]) or 'aucun'
        try:
            t, _ = self._llm(s, config.PROMPT_VERSIONS['ticket'], {'equipment_id': s['equipment_id'], 'question': s['question'],
                                                    'answer': res['answer'], 'references': ', '.join(refs),
                                                    'history': history}, LLMTicket, num_predict=300)
            title, desc, prio = t.title, t.description, t.priority
            source = 'llm'
        except (ValueError, BudgetExceeded):
            title = f"Alerte à analyser sur {s['equipment_id']}"
            desc = f"{res['answer']}\nRéférences : {', '.join(refs)}\nHistorique : {history}"
            prio, source = 'moyenne', 'template'
        title, _ = security.sanitize(title)
        desc, _ = security.sanitize(desc)
        draft = TicketDraft(equipment_id=s['equipment_id'], title=title[:120], description=desc[:1500], priority=prio,
                            references=refs).model_dump()
        return {'ticket_draft': draft, 'llm_calls': s.get('llm_calls', 0), 'route': 'approval',
                'trace': self._trace(s, 'draft_ticket', source=source)}

    def human_approval(self, s: State) -> dict:
        decision = interrupt({'type': 'ticket_approval', 'request_id': s['request_id'], 'draft': s['ticket_draft']})
        if not decision.get('approved'):
            return {'ticket': {'status': 'rejected', 'by': decision.get('approver_id')}, 'route': 'end',
                    'trace': self._trace(s, 'human_approval', approved=False)}
        return {'ticket_draft': decision['draft'], 'ticket': {'status': 'approved', 'by': decision['approver_id'],
                                                              'token': decision['approval_token'],
                                                              'idempotency_key': decision['idempotency_key']},
                'route': 'create', 'trace': self._trace(s, 'human_approval', approved=True)}

    def create_ticket(self, s: State) -> dict:
        d, t = s['ticket_draft'], s['ticket']
        try:
            [r] = self._mcp(s, [('create_ticket', {
                'user_id': s['user']['user_id'], 'approver_id': t['by'], 'equipment_id': d['equipment_id'],
                'title': d['title'], 'description': d['description'], 'priority': d['priority'],
                'approval_token': t['token'], 'idempotency_key': t['idempotency_key']})])
        except BudgetExceeded as ex:
            r = {'ok': False, 'error': 'budget', 'message': str(ex)}
        ticket = {'status': 'created' if r.get('ok') else 'failed', 'by': t['by'], **(r.get('data') or {}),
                  'message': r.get('message')}
        return {'ticket': ticket, 'mcp_calls': s.get('mcp_calls', 0), 'route': 'end',
                'trace': self._trace(s, 'create_ticket', ok=r.get('ok'))}

    def finalize(self, s: State) -> dict:
        security.log_event({'request_id': s['request_id'], 'user_id': s['user'].get('user_id'),
                            'status': s.get('result', {}).get('status'), 'llm_calls': s.get('llm_calls', 0),
                            'mcp_calls': s.get('mcp_calls', 0), 'seconds': round(time.time() - s['started'], 1),
                            'ticket': (s.get('ticket') or {}).get('status'), 'flags': sorted((s.get('flags') or {}).keys()),
                            'versions': self.versions()})
        return {'trace': self._trace(s, 'finalize')}

    # ------------------------------------------------------------------ graphe
    def _build(self):
        g = StateGraph(State)
        for name in ('check_access', 'analyze', 'retrieve', 'mcp_lookup', 'generate', 'verify', 'draft_ticket',
                     'human_approval', 'create_ticket', 'finalize'):
            g.add_node(name, getattr(self, name))
        route = lambda s: s.get('route', 'end')  # noqa: E731
        g.add_edge(START, 'check_access')
        g.add_conditional_edges('check_access', route, {'ok': 'analyze', 'end': 'finalize'})
        g.add_conditional_edges('analyze', route, {'retrieve': 'retrieve', 'end': 'finalize'})
        g.add_conditional_edges('retrieve', route, {'mcp': 'mcp_lookup', 'generate': 'generate', 'end': 'finalize'})
        g.add_edge('mcp_lookup', 'generate')
        g.add_edge('generate', 'verify')
        g.add_conditional_edges('verify', route, {'draft': 'draft_ticket', 'end': 'finalize'})
        g.add_conditional_edges('draft_ticket', route, {'approval': 'human_approval', 'end': 'finalize'})
        g.add_conditional_edges('human_approval', route, {'create': 'create_ticket', 'end': 'finalize'})
        g.add_edge('create_ticket', 'finalize')
        g.add_edge('finalize', END)
        return g.compile(checkpointer=MemorySaver())

    def versions(self) -> dict:
        return {'model': getattr(self.llm, 'model', 'fake'), 'prompts': config.PROMPT_VERSIONS,
                'reranker': self.retriever.reranker_mode, 'index': self.retriever.version.split('|')[0]}

    # ------------------------------------------------------------------ API publique
    def _output(self, thread_id: str) -> dict:
        snap = self.graph.get_state({'configurable': {'thread_id': thread_id}})
        s = snap.values
        pending = [i.value for t in snap.tasks for i in (t.interrupts or [])]
        return {'thread_id': thread_id, 'result': s.get('result'), 'ticket_draft': s.get('ticket_draft'),
                'ticket': s.get('ticket'), 'awaiting_approval': bool(pending), 'model': s.get('model'),
                'equipment_id': s.get('equipment_id'), 'visual': s.get('visual'), 'history': s.get('history'),
                'open_tickets': s.get('open_tickets'), 'evidence': [{k: e[k] for k in ('eid', 'document_id', 'page',
                'element_id', 'type', 'titre', 'suspect')} for e in s.get('evidence', [])],
                'llm_calls': s.get('llm_calls', 0), 'mcp_calls': s.get('mcp_calls', 0), 'trace': s.get('trace', []),
                'flags': {k: (v if not isinstance(v, list) else [list(x) for x in v]) for k, v in (s.get('flags') or {}).items()},
                'versions': self.versions()}

    def ask(self, user_id: str, question: str, image: bytes | None = None) -> dict:
        thread_id = uuid.uuid4().hex
        state: State = {'request_id': thread_id, 'user': USERS.get(user_id) or {}, 'question': question.strip(),
                        'image': image, 'llm_calls': 0, 'mcp_calls': 0, 'started': time.time(), 'trace': [],
                        'flags': {}}
        self.graph.invoke(state, {'configurable': {'thread_id': thread_id}})
        return self._output(thread_id)

    def decide(self, thread_id: str, approver_id: str, approved: bool, draft: dict | None = None) -> dict:
        """Décision humaine sur un brouillon. L'approbation est liée à l'approbateur et au contenu exact."""
        cfg = {'configurable': {'thread_id': thread_id}}
        snap = self.graph.get_state(cfg)
        if not any(t.interrupts for t in snap.tasks):
            raise ValueError('Aucun ticket en attente d\'approbation pour cette demande.')
        approver = USERS.get(approver_id)
        current = snap.values['ticket_draft']
        final = TicketDraft(**(draft or current)).model_dump()
        if final['equipment_id'] != current['equipment_id']:
            raise ValueError('L\'équipement du ticket ne peut pas être modifié.')
        decision: dict[str, Any] = {'approved': False, 'approver_id': approver_id}
        if approved:
            if not approver or not approver.get('peut_approuver_ticket'):
                raise PermissionError('Ce profil ne peut pas approuver de ticket.')
            chash = security.content_hash(final['equipment_id'], final['title'], final['description'], final['priority'])
            idem = security.idempotency_key(thread_id, final['equipment_id'], chash)
            decision = {'approved': True, 'approver_id': approver_id, 'draft': final, 'idempotency_key': idem,
                        'approval_token': security.approval_token(approver_id, final['equipment_id'], chash, idem)}
        self.graph.invoke(Command(resume=decision), cfg)
        return self._output(thread_id)
