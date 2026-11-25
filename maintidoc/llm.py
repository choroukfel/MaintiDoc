"""Accès au modèle vision-langage (Ollama) avec sorties JSON contraintes et budget d'appels."""
from __future__ import annotations

import io
import time
from functools import lru_cache
from typing import Any

import yaml
from langchain_core.prompts import PromptTemplate
from PIL import Image
from pydantic import BaseModel

from . import config


class BudgetExceeded(RuntimeError):
    pass


class LLMUnavailable(ValueError):
    """Modèle injoignable ou délai dépassé : pas de nouvel essai (contrairement à une sortie JSON invalide)."""


@lru_cache
def load_prompt(name: str) -> dict:
    data = yaml.safe_load((config.PROMPTS_DIR / f'{name}.yaml').read_text(encoding='utf-8'))
    data['user_template'] = PromptTemplate.from_template(data['user'])
    return data


def image_to_bytes(im: Image.Image, max_side: int = config.MAX_IMAGE_SIDE) -> bytes:
    im = im.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format='JPEG', quality=85)
    return buf.getvalue()


class OllamaVLM:
    """Client Ollama. `structured()` renvoie un objet Pydantic validé ou lève ValueError."""

    def __init__(self, model: str = config.VLM_MODEL, host: str = config.OLLAMA_HOST):
        from ollama import Client
        self.model = model
        self.client = Client(host=host, timeout=config.LLM_TIMEOUT)

    def warm_up(self) -> None:
        """Charge le modèle en mémoire (au démarrage de l'API) pour que la première demande ne paie pas ce délai."""
        self.client.generate(model=self.model, prompt='', keep_alive='15m')

    def structured(self, prompt_name: str, variables: dict[str, Any], schema: type[BaseModel],
                   images: list[bytes] | None = None, num_predict: int = config.NUM_PREDICT) -> tuple[BaseModel, dict]:
        p = load_prompt(prompt_name)
        user = p['user_template'].format(**variables)
        msg: dict[str, Any] = {'role': 'user', 'content': user}
        if images:
            msg['images'] = images
        t = time.time()
        try:
            res = self.client.chat(model=self.model, messages=[{'role': 'system', 'content': p['system']}, msg],
                                   format=schema.model_json_schema(), keep_alive='15m',
                                   options={'temperature': 0, 'num_ctx': config.NUM_CTX, 'num_predict': num_predict})
        except Exception as ex:  # délai dépassé, serveur arrêté...
            raise LLMUnavailable(f'Modèle indisponible : {type(ex).__name__}') from ex
        stats = {'prompt': prompt_name, 'seconds': round(time.time() - t, 2),
                 'prompt_tokens': res.prompt_eval_count, 'output_tokens': res.eval_count}
        from .lc import parse_structured  # validation par PydanticOutputParser (LangChain)
        return parse_structured(res.message.content, schema), stats

    def available(self) -> bool:
        try:
            return any(m.model == self.model for m in self.client.list().models)
        except Exception:
            return False
