"""Suivi des expériences avec MLflow (stockage local dans ./mlruns)."""
from __future__ import annotations

import os
from pathlib import Path

from . import config

os.environ.setdefault('MLFLOW_ALLOW_FILE_STORE', 'true')
os.environ.setdefault('MLFLOW_DISABLE_AGENT_HINT', '1')


def log_run(experiment: str, run_name: str, params: dict, metrics: dict, artifacts_dir: Path | None = None) -> bool:
    try:
        import mlflow
        mlflow.set_tracking_uri((config.ROOT / 'mlruns').as_uri())
        mlflow.set_experiment(experiment)
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params({k: str(v)[:250] for k, v in params.items()})
            mlflow.log_metrics({k.replace('@', '_at_').replace(' ', '_').replace('(', '').replace(')', ''): float(v)
                                for k, v in metrics.items() if v is not None})
            if artifacts_dir is not None:
                mlflow.log_artifacts(str(artifacts_dir), artifact_path='modele')  # poids + fiche du modèle
        return True
    except Exception as ex:  # le suivi ne doit jamais bloquer l'évaluation
        print('MLflow indisponible :', str(ex)[:200])
        return False
