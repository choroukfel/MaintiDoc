"""Stockage des documents sources : dossier local (développement) ou MinIO / S3 (déploiement).

MinIO est activé par MAINTIDOC_S3_ENDPOINT (ex. « minio:9000 »), avec MAINTIDOC_S3_ACCESS_KEY,
MAINTIDOC_S3_SECRET_KEY et MAINTIDOC_S3_BUCKET. Au premier démarrage, le bucket vide est alimenté avec le
corpus local fourni dans l'image (amorçage).
"""
from __future__ import annotations

import io
import os
from functools import lru_cache

from . import config


class LocalStorage:
    kind = 'local'

    def list(self) -> list[str]:
        return sorted(p.name for p in config.CORPUS_DIR.iterdir() if p.is_file())

    def read(self, name: str) -> bytes:
        path = (config.CORPUS_DIR / name).resolve()
        if config.CORPUS_DIR.resolve() not in path.parents:  # pas de sortie du dossier corpus
            raise ValueError('chemin de document invalide')
        return path.read_bytes()


class S3Storage:
    kind = 's3'

    def __init__(self):
        from minio import Minio
        self.bucket = os.getenv('MAINTIDOC_S3_BUCKET', 'maintidoc-corpus')
        self.client = Minio(os.environ['MAINTIDOC_S3_ENDPOINT'], access_key=os.environ['MAINTIDOC_S3_ACCESS_KEY'],
                            secret_key=os.environ['MAINTIDOC_S3_SECRET_KEY'],
                            secure=os.getenv('MAINTIDOC_S3_SECURE', '0') == '1')
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        if not self.list() and config.CORPUS_DIR.exists():
            self.seed_from_local()

    def seed_from_local(self) -> int:
        n = 0
        for name in LocalStorage().list():
            data = (config.CORPUS_DIR / name).read_bytes()
            self.client.put_object(self.bucket, name, io.BytesIO(data), len(data))
            n += 1
        return n

    def list(self) -> list[str]:
        return sorted(o.object_name for o in self.client.list_objects(self.bucket))

    def read(self, name: str) -> bytes:
        resp = self.client.get_object(self.bucket, name)
        try:
            return resp.read()
        finally:
            resp.close(); resp.release_conn()


@lru_cache
def get_storage():
    return S3Storage() if os.getenv('MAINTIDOC_S3_ENDPOINT') else LocalStorage()
