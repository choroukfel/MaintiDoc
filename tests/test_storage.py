"""Couche de stockage : dossier local et client S3/MinIO (simulé en mémoire, sans service réseau)."""
import io
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
