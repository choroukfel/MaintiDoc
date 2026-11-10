"""Schémas Pydantic des sorties structurées."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    document_id: str
    page: int
    element_id: str


class Answer(BaseModel):
    """Sortie finale renvoyée à l'utilisateur (format de la section 7 du sujet)."""
    status: Literal['answered', 'needs_clarification', 'refused']
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    proposed_action: Literal['draft_ticket'] | None = None


class LLMAnswer(BaseModel):
    """Format demandé au modèle : il cite des clés courtes (E1, E2...) plus faciles à recopier pour un petit modèle."""
    status: Literal['answered', 'needs_clarification']
    answer: str = Field(max_length=1500)
    citations: list[str] = Field(default_factory=list, max_length=6)
    uncertainties: list[str] = Field(default_factory=list, max_length=5)
    clarification_question: str | None = None


class Indicator(BaseModel):
    label: str | None = None
    color: str


class VisualAnalysis(BaseModel):
    readable: bool
    equipment_model: str | None = None
    screen_text: str | None = None
    lit_indicators: list[Indicator] = Field(default_factory=list, max_length=8)
    notes: str | None = None


class TicketDraft(BaseModel):
    equipment_id: str
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=1500)
    priority: Literal['basse', 'moyenne', 'haute'] = 'moyenne'
    references: list[str] = Field(default_factory=list)


class LLMTicket(BaseModel):
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=1200)
    priority: Literal['basse', 'moyenne', 'haute']
