"""Couche de stockage : dossier local et client S3/MinIO (simulé en mémoire, sans service réseau)."""
import io
import os
import sys
import types

import pytest

from maintidoc import config, storage


def test_local_storage_reads_corpus_and_blocks_traversal():
    st = storage.LocalStorage()
    names = st.list()
    assert 'metadata.json' in names and sum(n.endswith('.pdf') for n in names) == 30
    assert st.read('metadata.json').startswith(b'[')
    with pytest.raises(ValueError):
        st.read('../README.md')


class FakeMinio:
    """Imite le sous-ensemble de l'API minio.Minio utilisé par S3Storage."""
    buckets: dict[str, dict[str, bytes]] = {}

    def __init__(self, endpoint, access_key, secret_key, secure):
        self.endpoint = endpoint

    def bucket_exists(self, b):
        return b in self.buckets

    def make_bucket(self, b):
        self.buckets[b] = {}

    def put_object(self, b, name, data, length):
        self.buckets[b][name] = data.read(length)

    def list_objects(self, b):
        return [types.SimpleNamespace(object_name=n) for n in self.buckets[b]]

    def get_object(self, b, name):
        resp = io.BytesIO(self.buckets[b][name])
        resp.release_conn = lambda: None
        return resp


def test_s3_storage_seeds_bucket_then_serves_documents(monkeypatch):
    monkeypatch.setitem(sys.modules, 'minio', types.SimpleNamespace(Minio=FakeMinio))
    FakeMinio.buckets = {}
    for k, v in {'MAINTIDOC_S3_ENDPOINT': 'minio:9000', 'MAINTIDOC_S3_ACCESS_KEY': 'a',
                 'MAINTIDOC_S3_SECRET_KEY': 'b', 'MAINTIDOC_S3_BUCKET': 'test-corpus'}.items():
        monkeypatch.setenv(k, v)
    storage.get_storage.cache_clear()
    try:
        st = storage.get_storage()
        assert st.kind == 's3'
        assert len(st.list()) == len(storage.LocalStorage().list())  # amorçage depuis le corpus local
        pdf = next(n for n in st.list() if n.endswith('.pdf'))
        assert st.read(pdf) == (config.CORPUS_DIR / pdf).read_bytes()
        # second démarrage : le bucket n'est pas réamorcé
        n_before = len(FakeMinio.buckets['test-corpus'])
        storage.S3Storage()
        assert len(FakeMinio.buckets['test-corpus']) == n_before
    finally:
        storage.get_storage.cache_clear()


@pytest.mark.skipif(not os.getenv('MAINTIDOC_S3_TEST_ENDPOINT'), reason='serveur MinIO de test non configuré')
def test_real_minio_seeds_and_serves_identical_documents(monkeypatch):
    """Intégration avec un vrai serveur MinIO (MAINTIDOC_S3_TEST_ENDPOINT, _ACCESS_KEY, _SECRET_KEY)."""
    import hashlib
    import uuid

    import pymupdf
    monkeypatch.setenv('MAINTIDOC_S3_ENDPOINT', os.environ['MAINTIDOC_S3_TEST_ENDPOINT'])
    monkeypatch.setenv('MAINTIDOC_S3_ACCESS_KEY', os.environ['MAINTIDOC_S3_TEST_ACCESS_KEY'])
    monkeypatch.setenv('MAINTIDOC_S3_SECRET_KEY', os.environ['MAINTIDOC_S3_TEST_SECRET_KEY'])
    monkeypatch.setenv('MAINTIDOC_S3_BUCKET', f'test-{uuid.uuid4().hex[:8]}')
    st = storage.S3Storage()
    try:
        local = storage.LocalStorage()
        assert st.list() == local.list()
        for name in st.list():
            assert hashlib.sha256(st.read(name)).digest() == hashlib.sha256(local.read(name)).digest()
        pdf = next(n for n in st.list() if n.endswith('.pdf'))
        assert pymupdf.open(stream=st.read(pdf), filetype='pdf').page_count >= 1
        assert storage.S3Storage().list() == st.list()  # redémarrage : pas de ré-amorçage en double
    finally:
        for name in st.list():
            st.client.remove_object(st.bucket, name)
        st.client.remove_bucket(st.bucket)
