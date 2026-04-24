"""Validation et nettoyage des inputs utilisateur (cahier §14.3 C1).

Deux API complémentaires :

- ``check_input(text) -> str`` : **raising**, lève ``InputGateError``
  sur rejet. Usage direct : scripts, tests unit, callers qui veulent
  l'erreur remontée.
- ``evaluate_input(text) -> InputGateResult`` : **non-raising**,
  renvoie un dataclass. Usage pipeline : on yield un event
  ``input_rejected`` sans laisser une exception remonter dans l'async
  generator (cf. S03 invariant I1).

Tous les patterns sont appliqués sur le texte normalisé via
``text.normalize_fr`` (NFKD + lowercase + ascii-only). Les patterns
doivent donc être en ASCII minuscule — aucun ``re.IGNORECASE`` nécessaire.

Patterns 2026 figés — cf. story S05 phase 1 §"Patterns d'injection 2026".
Couverture : OWASP LLM01:2025 Direct Injection, Jailbreak, Role override,
Prompt leakage, Format breakers, Fake turns, Delimiter override, Agent
tool manipulation. Indirect injection via Pappers est gérée par S03
(``_neutralize_injection_attempts``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from genial_agent.guardrails.text import normalize_fr

MAX_INPUT_LENGTH = 2000

# ---------------------------------------------------------------------------
# Reason codes (enum stable, cohérent avec S04 ``reason_code`` pattern)
# ---------------------------------------------------------------------------

REASON_CODE_INPUT_EMPTY = "input_empty"
REASON_CODE_INPUT_TOO_LONG = "input_too_long"
REASON_CODE_INPUT_INJECTION = "input_injection"


# Patterns appliqués APRÈS ``normalize_fr`` (NFKD + lowercase + ascii-only).
# Aucun ``re.IGNORECASE`` — redondant sur texte normalisé.
#
# Structure — chaque pattern cible une famille OWASP LLM01:2025 :
#
# - Direct override : ``ignore``/``disregard``/``forget`` + qualifiers
#   (up to 4 words) + ``instructions``/``rules``/``prompts``/etc.
#   Le ``(?:\w+\s+){0,4}`` permet de capturer les variantes « ignore all
#   previous instructions », « ignore les precedentes instructions »,
#   « ignore your last two prompts », etc. en évitant la combinatoire.
# - Role override / jailbreak : DAN/STAN/… + variantes FR/EN.
# - System prompt leakage : reveal/show/print + your/the + system prompt.
# - Format breakers : ``<|im_*|>``, ``[INST]``, ``### System:``.
# - Fake turns : ``^system:`` (multiline), ``BEGIN SYSTEM PROMPT``.
# - Delimiter override : nos propres tags (``</user_input>``,
#   ``</tool_result>``) — un prompt qui les contient tente de faire
#   sortir Claude du frame user.
# - Agent tool manipulation : réorientation vers un tool hors-scope
#   (``send_email``, ``exec``, ``shell``, ``curl``…).
INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # --- Direct override: ignore/disregard + qualifiers + instructions/rules/prompts ---
    re.compile(
        r"\b(?:ignore[s]?|disregard)\s+"
        r"(?:\w+\s+){0,4}"
        r"(?:instructions?|rules?|prompts?|regles?)\b"
    ),
    # --- Forget variants (forget everything/all/above/previous…) ---
    re.compile(
        r"\bforget\s+"
        r"(?:\w+\s+){0,4}"
        r"(?:above|previous|instructions?|rules?|prompts?)\b"
    ),
    # --- Role override / jailbreak (DAN, STAN, etc.) ---
    re.compile(r"\bdan\s+mode\b"),
    re.compile(r"\b(stan|dude|dan)\s+(mode|prompt|jailbreak)\b"),
    re.compile(r"\byou\s+are\s+now\s+(a\s+)?(jailbreak|free|unrestricted|without\s+rules?)"),
    re.compile(
        r"\btu\s+es\s+maintenant\s+(un\s+)?"
        r"(chatbot\s+libre|sans\s+regles?|sans\s+filtre)"
    ),
    re.compile(
        r"\bpretend\s+(to\s+be|you\s+are)\s+"
        r"(?:an?\s+)?(?:ai\s+|assistant\s+|model\s+)?"
        r"without\s+(rules?|filters?|restrictions?)"
    ),
    # --- System prompt leakage (``show me the system prompt``, etc.) ---
    re.compile(
        r"\b(reveal|show|print|leak|expose|divulgue|divulge|revele)\s+"
        r"(?:me\s+)?"
        r"(?:your?|ton|tes|the)\s+"
        r"(system\s+prompt|instructions?|rules?|prompts?)\b"
    ),
    re.compile(r"\brepeat\s+(the|your)\s+(system\s+prompt|instructions|rules)\b"),
    # --- Special tokens / format breakers ---
    re.compile(r"<\|im_start\|>"),
    re.compile(r"<\|im_end\|>"),
    re.compile(r"<\|endoftext\|>"),
    re.compile(r"\[inst\]|\[/inst\]"),
    re.compile(r"###\s*(instruction|response|system)\s*:"),
    # --- Fake system/user turns ---
    re.compile(r"^\s*(system|assistant)\s*:", re.MULTILINE),
    re.compile(r"\bbegin\s+(new\s+)?system\s+prompt\b"),
    # --- Override delimiters we use ourselves (agent wraps user input) ---
    re.compile(r"</?user_input>"),
    re.compile(r"</?tool_result>"),
    # --- Tool redirection (OWASP Agent tool manipulation) ---
    re.compile(r"\buse\s+the\s+(send_email|send_message|exec|shell|fetch|curl|wget)\s+tool\b"),
]


class InputGateError(ValueError):
    """Levée par ``check_input`` sur rejet.

    ``args[0]`` = ``reason_code`` (enum stable, cf. ``REASON_CODE_INPUT_*``).
    ``args[1]`` = ``reason`` (détail humain, à afficher pas à matcher).
    """


@dataclass(frozen=True)
class InputGateResult:
    """Résultat non-raising de l'évaluation d'un input utilisateur.

    Attributes:
        ok: True si l'input passe les 3 checks (non-vide, length cap,
            pas d'injection détectée).
        reason_code: l'un des ``REASON_CODE_INPUT_*`` si ``ok=False``,
            sinon None.
        reason: détail humain (à afficher, **pas** à matcher
            programmatiquement).
        text: l'input d'origine si ``ok=True``, sinon chaîne vide.
    """

    ok: bool
    reason_code: str | None = None
    reason: str | None = None
    text: str = ""


def evaluate_input(text: str) -> InputGateResult:
    """Évalue l'input sans lever d'exception. Utilisé par le pipeline.

    Ordre des checks : empty → length → injection. Sur length cap,
    ne lance pas la recherche d'injection (inutile et coûteux sur
    un message de 10k chars).
    """
    if not text or not text.strip():
        return InputGateResult(
            ok=False,
            reason_code=REASON_CODE_INPUT_EMPTY,
            reason="empty input",
        )
    if len(text) > MAX_INPUT_LENGTH:
        return InputGateResult(
            ok=False,
            reason_code=REASON_CODE_INPUT_TOO_LONG,
            reason=f"{len(text)} chars > cap {MAX_INPUT_LENGTH}",
        )
    normalized = normalize_fr(text)
    for pattern in INJECTION_PATTERNS:
        if pattern.search(normalized):
            return InputGateResult(
                ok=False,
                reason_code=REASON_CODE_INPUT_INJECTION,
                reason=f"pattern matched: {pattern.pattern[:60]}",
            )
    return InputGateResult(ok=True, text=text)


def check_input(text: str) -> str:
    """Valide l'input utilisateur. Lève ``InputGateError`` si rejeté.

    Args:
        text: l'input utilisateur brut.

    Returns:
        Le texte d'origine si valide.

    Raises:
        InputGateError: ``args[0]`` = reason_code stable, ``args[1]`` =
        reason humaine.
    """
    result = evaluate_input(text)
    if not result.ok:
        assert result.reason_code is not None
        raise InputGateError(result.reason_code, result.reason or "")
    return result.text
