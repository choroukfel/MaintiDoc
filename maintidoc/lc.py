"""Composants LangChain (section 6 du sujet : « LangChain fournit les composants de récupération, de prompting et
de validation des sorties »).

- MaintiDocRetriever : un BaseRetriever LangChain au-dessus de la recherche hybride (Qdrant + BM25 + CLIP + RRF +
  reranker), avec le contrôle d'accès appliqué avant la récupération. Il renvoie des Document LangChain.
- parse_structured : validation des sorties du modèle par PydanticOutputParser.
- Le prompting utilise PromptTemplate (maintidoc/llm.py).
"""
from __future__ import annotations

from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.retrievers import BaseRetriever
from pydantic import BaseModel, ConfigDict


class MaintiDocRetriever(BaseRetriever):
    """Recherche multimodale filtrée par les droits de l'utilisateur, exposée comme retriever LangChain."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    retriever: Any                       # maintidoc.retrieval.Retriever
    groups: list[str]                    # groupes d'accès de l'utilisateur (filtrage AVANT récupération)
    models: list[str] | None = None      # modèle d'équipement identifié (filtre)
    image: Any = None                    # photo de l'utilisateur (recherche visuelle CLIP)
    top_k: int = 4

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        hits = self.retriever.search(query, self.groups, image=self.image, models=self.models, top_k=self.top_k)
        return [Document(page_content=h.element['content'],
                         metadata={'key': h.key, 'document_id': h.element['document_id'], 'page': h.element['page'],
                                   'element_id': h.element['element_id'], 'type': h.element['type'],
                                   'rerank': h.rerank, 'rrf': h.score, 'sources': list(h.sources)})
                for h in hits]


def parse_structured(text: str, schema: type[BaseModel]) -> BaseModel:
    """Valide une sortie JSON du modèle avec le schéma Pydantic (PydanticOutputParser de LangChain)."""
    try:
        return PydanticOutputParser(pydantic_object=schema).parse(text)
    except OutputParserException as ex:
        raise ValueError(f'Sortie JSON invalide : {str(ex)[:200]}') from ex
