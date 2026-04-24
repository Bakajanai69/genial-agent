"""Haiku-critic : vérification async non-bloquante de la réponse (cahier §14.3 C6).

Implémentation **raw JSON + parser robuste** (cf. S05 phase 1 §"Critic
async — format de sortie"). Fail-safe : toute erreur (parse, timeout,
API, réseau) tombe sur ``confidence=0.0, color="orange",
issues=["critic_error"]`` — la UI affiche un badge orange, la réponse
reste visible.

**Non-bloquant** : le pipeline spawn le critic via
``asyncio.create_task`` post-``end`` event, puis ``wait_for(10 s)`` avec
fallback orange sur timeout. Cf. ``guardrails/pipeline.py``.

**Next step post-MVP** : migration vers ``tool_use strict=True`` quand
on bump le SDK Anthropic vers ≥ 0.115 (GA structured outputs,
~99.5 % schéma-conforme). Code change : ~10 lignes ici. Documenté en
S09 README.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from anthropic import APIError, AsyncAnthropic

from genial_agent.config import settings
from genial_agent.models import MODEL_HAIKU

logger = structlog.get_logger(__name__)

CRITIC_MAX_TOKENS = 512
# Borne défensive sur la taille du prompt user envoyé au critic : une
# réponse agent anormalement longue (saturation du context window) ne
# doit pas faire exploser le call critic (coût + latence).
CRITIC_RESPONSE_MAX_CHARS = 4000

CRITIC_PROMPT = """Tu es un vérificateur qualité pour des réponses d'agent
sur des entreprises françaises. Tu reçois la question de l'utilisateur
et la réponse de l'agent. Tu renvoies UNIQUEMENT un JSON (pas de
préambule, pas de ```json, pas de commentaires) de cette forme exacte :

{
  "scope_ok": true,
  "hallucination_risk": "low",
  "advisory_language": false,
  "confidence": 0.9,
  "issues": []
}

Critères :
- scope_ok (bool) : la réponse concerne bien une entreprise française ?
  False si Apple, Tesla, etc. False aussi si sujet hors entreprise.
- hallucination_risk ("low"|"medium"|"high") : chiffres / dirigeants /
  SIREN cités sans indication de source ? "high" si pas d'horodatage
  de bilan sur des chiffres ; "low" si tout est sourcé.
- advisory_language (bool) : ton prescriptif ("je te conseille",
  "tu devrais acheter", "bon placement") ?
- confidence (float 0.0-1.0) : ton évaluation globale. 1.0 = parfait.
- issues (list[str]) : problèmes concrets en 1-3 mots chacun. [] si rien.

Réponds UNIQUEMENT le JSON, rien d'autre."""


@dataclass(frozen=True)
class CriticResult:
    """Résultat du critic. Immuable, sérialisable via ``to_event()``.

    Les seuils de couleur sont figés phase 1 S05 :

    - ``green`` : ``confidence >= 0.85`` ET ``issues == []``.
    - ``red`` : ``confidence < 0.6`` OU ``scope_ok is False`` OU
      ``hallucination_risk == "high"``.
    - ``orange`` : entre les deux (inclut les parse / timeout errors
      via le ``_FALLBACK`` interne).
    """

    scope_ok: bool
    hallucination_risk: str
    advisory_language: bool
    confidence: float
    issues: list[str]

    @property
    def color(self) -> str:
        """Badge UI (cf. cahier §16.2 C6).

        **Erreurs infra** (``critic_error``, ``critic_timeout``) forcent
        orange : on ne sait pas, on ne doit pas induire l'utilisateur en
        erreur avec un rouge basé sur un ``confidence=0.0`` artificiel.
        Cf. S05 phase 1 §"Critic async — format de sortie".
        """
        if any(i in ("critic_error", "critic_timeout") for i in self.issues):
            return "orange"
        if not self.scope_ok or self.hallucination_risk == "high" or self.confidence < 0.6:
            return "red"
        if self.confidence >= 0.85 and not self.issues:
            return "green"
        return "orange"

    def to_event(self) -> dict[str, Any]:
        """Sérialise pour l'event ``critic_result`` du pipeline."""
        return {
            "color": self.color,
            "confidence": self.confidence,
            "scope_ok": self.scope_ok,
            "hallucination_risk": self.hallucination_risk,
            "advisory_language": self.advisory_language,
            "issues": list(self.issues),
        }


# Fallback orange : error path (parse / API / réseau). Utilisé par
# ``critique_async`` en interne ET par le pipeline en cas de timeout
# wall-clock 10 s.
_FALLBACK = CriticResult(
    scope_ok=True,
    hallucination_risk="low",
    advisory_language=False,
    confidence=0.0,
    issues=["critic_error"],
)


def _parse_critic_json(raw: str) -> CriticResult:
    """Parser robuste : isole le JSON même si Claude ajoute un préambule.

    Claude 4.x respecte l'instruction « UNIQUEMENT le JSON » ~99 % du
    temps, mais ~1 % des réponses ajoutent ``Voici le JSON:`` ou
    wrappent dans des backticks. On cherche la première ``{`` et la
    dernière ``}`` puis on parse.

    Raises:
        ValueError: si aucune accolade trouvée.
        json.JSONDecodeError: si le candidat n'est pas du JSON valide.
    """
    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace == -1 or last_brace <= first_brace:
        raise ValueError("no JSON object found")
    candidate = raw[first_brace : last_brace + 1]
    data = json.loads(candidate)
    return CriticResult(
        scope_ok=bool(data.get("scope_ok", True)),
        hallucination_risk=str(data.get("hallucination_risk", "low")).lower(),
        advisory_language=bool(data.get("advisory_language", False)),
        confidence=float(data.get("confidence", 0.0)),
        issues=list(data.get("issues", [])),
    )


async def critique_async(question: str, response: str) -> CriticResult:
    """Exécute le critic Haiku et retourne un ``CriticResult``.

    **Ne lève jamais d'exception** : toute erreur (parse, API, réseau)
    tombe sur ``_FALLBACK`` (orange). Log WARNING pour diagnostic.

    Args:
        question: question utilisateur brute (pas besoin de wrap
            ``<user_input>`` — le critic n'appelle pas de tool et n'a
            pas le system prompt agent).
        response: réponse finale agent (texte concaténé). Tronquée à
            ``CRITIC_RESPONSE_MAX_CHARS`` avant envoi au modèle.

    Returns:
        ``CriticResult`` — toujours, même en cas d'erreur.
    """
    trimmed = response[:CRITIC_RESPONSE_MAX_CHARS]
    try:
        async with AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY) as client:
            msg = await client.messages.create(
                model=MODEL_HAIKU,
                max_tokens=CRITIC_MAX_TOKENS,
                temperature=0.0,  # déterministe pour la vérif
                system=CRITIC_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"QUESTION:\n{question}\n\nRÉPONSE:\n{trimmed}",
                    }
                ],
            )
    except APIError as exc:
        logger.warning("critic_api_error", error_type=type(exc).__name__)
        return _FALLBACK

    raw_text = ""
    if msg.content:
        block = msg.content[0]
        raw_text = getattr(block, "text", "") or ""

    try:
        return _parse_critic_json(raw_text)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("critic_parse_failed", error_type=type(exc).__name__)
        return _FALLBACK
