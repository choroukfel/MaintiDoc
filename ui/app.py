"""Interface Streamlit de MaintiDoc (cliente de l'API FastAPI).

Lancement : streamlit run ui/app.py   (l'API doit tourner sur MAINTIDOC_API, par défaut http://127.0.0.1:8000)
"""
from __future__ import annotations

import base64
import os

import httpx
import streamlit as st

API = os.getenv('MAINTIDOC_API', 'http://127.0.0.1:8000')
USERS = {'u-tech-a': 'Tech-A (technicien SAN)', 'u-tech-b': 'Tech-B (technicien onduleurs)',
         'u-resp-1': 'Resp-1 (responsable)', 'u-stagiaire': 'Stagiaire-1 (lecteur)'}
STATUS = {'answered': ('Réponse sourcée', 'success'), 'needs_clarification': ('Clarification nécessaire', 'warning'),
          'refused': ('Demande refusée', 'error')}

st.set_page_config(page_title='MaintiDoc', page_icon='🛠️', layout='wide')
st.title('MaintiDoc · assistant documentaire de maintenance')
st.caption('Aide documentaire sur équipements fictifs. Ne pilote aucun équipement et ne remplace pas un technicien habilité.')


def api(method: str, path: str, token: str | None = None, **kw):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    return httpx.request(method, f'{API}{path}', headers=headers, timeout=900, **kw)


def login(uid: str) -> str:
    r = api('POST', '/auth/token', json={'user_id': uid})
    r.raise_for_status()
    return r.json()['access_token']


with st.sidebar:
    st.header('Session')
    uid = st.selectbox('Utilisateur (fictif)', list(USERS), format_func=USERS.get)
    if st.session_state.get('uid') != uid:
        try:
            st.session_state.update(uid=uid, token=login(uid), last=None)
        except Exception as ex:
            st.error(f'API inaccessible ({API}) : {ex}')
            st.stop()
    approver = st.selectbox('Approbateur des tickets', list(USERS), index=2, format_func=USERS.get)
    with st.expander('Documents autorisés'):
        for d in api('GET', '/documents', st.session_state['token']).json():
            st.write(f"**{d['document_id']}** — {d['titre']}")

question = st.text_area('Question', 'Voici une photo du panneau du SAN-LAB-2000-01. Que signifie ce voyant orange ? '
                                    'Consulte les dernières interventions et prépare un ticket si nécessaire.', height=90)
photo = st.file_uploader('Photographie du panneau (facultatif)', type=['jpg', 'jpeg', 'png'])
if photo:
    st.image(photo, width=360)

if st.button('Envoyer', type='primary'):
    payload = {'question': question}
    if photo:
        payload['image_base64'] = base64.b64encode(photo.getvalue()).decode()
    with st.spinner('Analyse en cours (inférence locale, plusieurs minutes sur CPU)…'):
        r = api('POST', '/ask', st.session_state['token'], json=payload)
    if r.status_code == 429:
        st.warning(f"{r.json().get('detail')} Réessayez dans {r.headers.get('Retry-After', '?')} s.")
    elif r.status_code != 200:
        st.error(f'Erreur {r.status_code} : {r.text}')
    else:
        st.session_state['last'] = r.json()

out = st.session_state.get('last')
if out:
    res = out['result'] or {}
    label, kind = STATUS.get(res.get('status'), ('?', 'info'))
    getattr(st, kind)(label)
    st.markdown(res.get('answer', ''))
    if res.get('clarification_question'):
        st.info(f"❓ {res['clarification_question']}")
    for u in res.get('uncertainties', []):
        st.caption(f'⚠️ {u}')
    if res.get('citations'):
        st.subheader('Sources citées')
        cols = st.columns(min(3, len(res['citations'])))
        for col, c in zip(cols * 3, res['citations']):
            with col:
                st.write(f"**{c['document_id']}** · page {c['page']} · {c['element_id']}")
                img = api('GET', f"/documents/{c['document_id']}/pages/{c['page']}", st.session_state['token'])
                if img.status_code == 200:
                    st.image(img.content, width='stretch')
                else:
                    st.warning(f'Page non affichable ({img.status_code})')
    if out.get('history') is not None:
        with st.expander(f"Historique MCP — {out['equipment_id']}"):
            st.table(out['history'])
            st.write('Tickets ouverts :', out.get('open_tickets'))
    if out.get('awaiting_approval') and out.get('ticket_draft'):
        st.subheader('Brouillon de ticket — approbation requise')
        d = out['ticket_draft']
        title = st.text_input('Titre', d['title'])
        desc = st.text_area('Description', d['description'], height=150)
        prio = st.selectbox('Priorité', ['basse', 'moyenne', 'haute'], index=['basse', 'moyenne', 'haute'].index(d['priority']))
        c1, c2 = st.columns(2)
        edited = {**d, 'title': title, 'description': desc, 'priority': prio}
        for col, ok, text in ((c1, True, '✅ Approuver et créer'), (c2, False, '❌ Rejeter')):
            if col.button(text):
                tok = login(approver)
                r = api('POST', f"/tickets/{out['thread_id']}/decision", tok, json={'approved': ok, 'draft': edited})
                if r.status_code == 200:
                    st.session_state['last'] = r.json()
                    st.rerun()
                else:
                    st.error(f'{r.status_code} : {r.json().get("detail")}')
    if out.get('ticket'):
        t = out['ticket']
        (st.success if t.get('status') == 'created' else st.info)(f"Ticket : {t}")
    with st.expander('Trace technique (métadonnées)'):
        st.json({k: out[k] for k in ('model', 'equipment_id', 'visual', 'llm_calls', 'mcp_calls', 'flags', 'versions', 'trace')})
