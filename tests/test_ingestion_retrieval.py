from maintidoc.retrieval import detect_models, tokenize


def test_elements_have_metadata(retriever):
    els = retriever.elements
    assert len(els) > 60
    for e in els.values():
        for k in ('document_id', 'version', 'page', 'element_id', 'groupes_acces', 'modeles', 'type'):
            assert k in e
    types = {e['type'] for e in els.values()}
    assert types == {'texte', 'tableau', 'figure'}


def test_table_captions_and_cells(retriever):
    tab = retriever.elements['UPS-MAN-030/TAB-02']
    assert tab['type'] == 'tableau'
    assert 'SURCHARGE' in tab['content'] and 'Orange fixe' in tab['content']  # espaces conservés


def test_figures_saved(retriever):
    from pathlib import Path
    figs = [e for e in retriever.elements.values() if e['type'] == 'figure']
    assert figs and all(Path(e['image_path']).exists() for e in figs)
    assert 'UPS-MAN-030/FIG-01' in retriever.elements


def test_headers_not_indexed(retriever):
    for e in retriever.elements.values():
        if e['type'] == 'texte':
            assert '| Révision' not in e['content']


def test_scanned_documents_read_by_ocr(retriever):
    ocr_els = {k: e for k, e in retriever.elements.items() if e.get('ocr')}
    assert {e['document_id'] for e in ocr_els.values()} == {'SAN-SCAN-017', 'UPS-SCAN-038'}
    assert any('4521' in e['content'] for e in ocr_els.values())  # information présente uniquement dans le scan
    hits = retriever.search("Quel est le poste de l'astreinte stockage ?", ['tech_san'], top_k=3)
    assert hits[0].element['document_id'] == 'SAN-SCAN-017'


def test_figure_text_read_by_ocr(retriever):
    assert 'SURCHARGE' in retriever.elements['UPS-MAN-030/FIG-01']['content']


def test_ocr_normalization():
    from maintidoc.ocr import normalize
    assert normalize('UPS-X9OO D1O ON BATT') == 'UPS-X900 D10 ON BATT'


def test_tokenize_keeps_codes():
    toks = tokenize('Que signifie le code F-05 sur l\'UPS-X900 ?')
    assert 'f-05' in toks and 'ups-x900' in toks and 'le' not in toks


def test_detect_models():
    assert detect_models('voyant du SAN LAB 3000 et ups-x500') == ['SAN-LAB 3000', 'UPS-X500']


def test_access_filter_before_retrieval(retriever):
    hits = retriever.search('voyant orange batterie onduleur câblage PDU', ['tech_san'], top_k=10)
    docs = {h.element['document_id'] for h in hits}
    assert docs and all(retriever.metadata[d]['groupes_acces'].count('tech_san') for d in docs)
    assert 'SAN-CAB-012' not in docs and not any(d.startswith('UPS') for d in docs)


def test_restricted_doc_visible_to_responsable(retriever):
    hits = retriever.search('affectation des ports FC et circuits PDU', ['responsables'], top_k=5)
    assert any(h.element['document_id'] == 'SAN-CAB-012' for h in hits)


def test_model_filter(retriever):
    hits = retriever.search('voyant orange', ['tech_san', 'tech_ups'], models=['UPS-X900'], top_k=5)
    assert hits and all('UPS-X900' in h.element['modeles'] for h in hits)


def test_code_query_finds_alarm_table(retriever):
    hits = retriever.search('Que signifie le code F-05 ?', ['tech_ups'], top_k=5)
    assert any(h.key in ('UPS-ERR-031/TAB-01', 'UPS-MAN-030/TAB-02') for h in hits[:3])
