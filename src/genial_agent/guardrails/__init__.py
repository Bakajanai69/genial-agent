"""Guardrails S05 — 6 couches de défense en profondeur.

Ré-exports publics pour les consumers (S06 UI, S07 observabilité). Les
modules internes (``text``, ``sirens``) restent importables directement
mais ne font pas partie de l'API de surface.

Les 6 couches (cahier §14.3) :

- **C1 Input gate** : ``check_input`` / ``evaluate_input`` — length cap,
  regex anti-injection 2026 sur texte normalisé NFKD.
- **C2 System prompt durci** : déjà en S03 (``SYSTEM_PROMPT_AGENT``).
- **C3 Safety native Claude** : embarquée dans les modèles 4.x.
- **C4 Execution caps** : ``caps.py`` (tool calls / wall-clock /
  tokens-per-session) + ``token_budget`` pour le suivi cumulatif.
- **C5 Validateur déterministe** : ``validate_response`` / ``degrade``
  — SIREN Luhn + bilan horodaté + advisory reframing.
- **C6 Haiku-critic async** : ``critique_async`` — non-bloquant, badge
  confiance (green / orange / red).

PII scrubbing (§14.4) : ``scrub`` + ``pii_scrub_processor`` structlog.

Entry point unique pour S06 : ``run_guarded_turn``.
"""

from __future__ import annotations

from genial_agent.guardrails.caps import (
    DAILY_PAPPERS_CREDITS_CAP,
    MAX_BRIEFS_PER_SESSION,
    MAX_TOKENS_PER_SESSION,
    MAX_TOOL_CALLS_PER_TURN,
    WALL_CLOCK_S,
)
from genial_agent.guardrails.critic import CriticResult, critique_async
from genial_agent.guardrails.input_gate import (
    REASON_CODE_INPUT_EMPTY,
    REASON_CODE_INPUT_INJECTION,
    REASON_CODE_INPUT_TOO_LONG,
    InputGateError,
    InputGateResult,
    check_input,
    evaluate_input,
)
from genial_agent.guardrails.output_validator import (
    REASON_CODE_ADVISORY_LANGUAGE,
    REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE,
    REASON_CODE_HALLUCINATION_ORPHAN_SIRENS,
    OutputValidationResult,
    degrade,
    validate_response,
)
from genial_agent.guardrails.pii import pii_scrub_processor, scrub
from genial_agent.guardrails.pipeline import run_guarded_turn
from genial_agent.guardrails.sirens import extract_sirens, valid_siren
from genial_agent.guardrails.token_budget import (
    REASON_CODE_CAP_TOKEN_BUDGET,
    TokenBudget,
    budget,
)

__all__ = [
    # Caps
    "DAILY_PAPPERS_CREDITS_CAP",
    "MAX_BRIEFS_PER_SESSION",
    "MAX_TOKENS_PER_SESSION",
    "MAX_TOOL_CALLS_PER_TURN",
    "WALL_CLOCK_S",
    # Critic
    "CriticResult",
    "critique_async",
    # Input gate
    "InputGateError",
    "InputGateResult",
    "REASON_CODE_INPUT_EMPTY",
    "REASON_CODE_INPUT_INJECTION",
    "REASON_CODE_INPUT_TOO_LONG",
    "check_input",
    "evaluate_input",
    # Output validator
    "OutputValidationResult",
    "REASON_CODE_ADVISORY_LANGUAGE",
    "REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE",
    "REASON_CODE_HALLUCINATION_ORPHAN_SIRENS",
    "degrade",
    "validate_response",
    # PII
    "pii_scrub_processor",
    "scrub",
    # Pipeline
    "run_guarded_turn",
    # Sirens
    "extract_sirens",
    "valid_siren",
    # Token budget
    "REASON_CODE_CAP_TOKEN_BUDGET",
    "TokenBudget",
    "budget",
]
