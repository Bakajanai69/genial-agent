"""Routing Haiku → Sonnet : pré-routeur keyword + escalate tool + caps.

Architecture Option B' (cahier §5.3) — défense en profondeur 3 couches :

1. **Pré-routeur keyword** (0 LLM, < 1 ms) — regex à frontière de mot
   sur texte normalisé NFKD-lowercase. Détecte comparaison, dossier
   complet, évolution multi-années, pronoms interrogatifs multi-
   entités, multi-SIREN. Dispatch direct Sonnet.
2. **Auto-escalade Haiku** — tool ``escalate_to_sonnet`` injecté dans
   ``extra_tools``. Haiku décide lui-même quand il sature.
3. **Cap dur backend** — ``MAX_TOOL_CALLS_PER_TURN = 5`` et
   ``WALL_CLOCK_S = 15`` (cahier §5.3, §14.3 C4). Mesurés en delta sur
   ``state.tool_calls_count`` et ``time.monotonic()``. Si un cap est
   atteint en tier Haiku, S04 force une escalade (mode ``forced``). En
   tier Sonnet, S04 émet un event ``capped`` et arrête la boucle.

Implémentation wall-clock : itération manuelle avec
``asyncio.wait_for(gen.__anext__(), timeout=remaining)`` pour respecter
PEP 789 (pas de ``asyncio.timeout`` autour d'un ``yield`` dans un async
generator). Cf. S04 phase 1 §"Décision majeure".

**Contrat de "reason"** — pour que S06 (UI badge) et S07 (stats) puissent
agréger sans dépendre d'un substring match fragile, chaque event porteur
d'une cause (escalation, capped) expose deux champs disjoints :

- ``reason_code`` (str, enum stable) : ``"self"``,
  ``"cap_tool_calls_per_turn"`` ou ``"cap_wall_clock"``. **Utiliser ce
  champ pour toute logique code-to-code** (matching, agrégation stats).
- ``reason`` (str, détail humain) : ``"5/5 tool calls"``, ``"15.0s"``,
  ou le texte libre que Haiku a fourni via ``escalate_to_sonnet.input
  .reason``. Ce champ est **à afficher** en UI mais **pas à matcher**
  programmatiquement.

``routing_done`` réexpose ces deux champs (préfixés ``escalation_*``
ou ``capped_*``) pour qu'un consumer qui n'observe que l'event final
ait la cause disponible.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import AsyncIterator
from typing import Any

import structlog

from genial_agent.agent import ConversationState, run_turn

# Caps S04 — single source of truth dans ``guardrails/caps.py`` (S05).
# Cahier §5.3, §14.3 C4, README "Décisions de cohérence". Avant S05, un
# ``try/except ImportError`` fallback local vivait ici — retiré à la
# merge S05 (la story S05 fige ces constantes).
from genial_agent.guardrails.caps import MAX_TOOL_CALLS_PER_TURN, WALL_CLOCK_S
from genial_agent.models import ModelTier

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pré-routeur keyword
# ---------------------------------------------------------------------------


def _normalize_fr(text: str) -> str:
    """Strip diacritiques + lowercase pour match accent-insensible.

    Justifie d'écrire les patterns en ASCII minuscule : on travaille
    sur texte normalisé. Indispensable pour attraper "evolution"
    (sans accent) ou "EVOLUTION" (majuscules).
    """
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = nfkd.encode("ascii", "ignore").decode("ascii")
    return stripped.lower()


# Patterns en ASCII minuscule — appliqués APRÈS _normalize_fr.
# Ordre important : premier match gagne.
COMPLEX_PATTERNS: list[re.Pattern[str]] = [
    # Comparaison / versus (cahier §3 U3)
    re.compile(r"\b(compare|comparaison|compares|comparons|versus|vs\.?)\b"),
    # Due diligence / dossier complet (cahier §7 terminologie)
    re.compile(r"\b(dossier\s+complet|due\s+diligence|due\s+dil)\b"),
    # Évolution multi-années (cahier §3 U3)
    re.compile(r"\b(evolution|evolutions|sur\s+\d+\s+ans?)\b"),
    # Pronoms interrogatifs multi-entités
    re.compile(r"\b(lequel|laquelle|lesquels?|lesquelles?)\b"),
    # Similarité / concurrence
    re.compile(r"\b(similaires?\s+a|concurrents?\s+de)\b"),
]

# Multi-SIREN — 2 SIREN détectés = intention multi-entité.
SIREN_RE = re.compile(r"\b\d{9}\b")


def pick_initial_tier(user_message: str) -> ModelTier:
    """Pré-routeur keyword. Retourne Sonnet si complexe, sinon Haiku.

    Texte normalisé NFKD-lowercase avant match (accent-insensible).
    Multi-SIREN (≥ 2 SIREN 9-chiffres) = complexe.
    """
    normalized = _normalize_fr(user_message)
    for pattern in COMPLEX_PATTERNS:
        if pattern.search(normalized):
            return ModelTier.SONNET
    if len(SIREN_RE.findall(user_message)) >= 2:
        return ModelTier.SONNET
    return ModelTier.HAIKU


# ---------------------------------------------------------------------------
# Reason codes (enum stable pour matching code-to-code)
# ---------------------------------------------------------------------------

# Valeurs possibles du champ ``reason_code`` dans les events ``escalation``
# et ``capped``. Utiliser ces constantes depuis les consumers (S06/S07)
# plutôt qu'un substring match sur ``reason`` (humain, peut évoluer).
REASON_CODE_SELF = "self"
REASON_CODE_CAP_TOOL_CALLS = "cap_tool_calls_per_turn"
REASON_CODE_CAP_WALL_CLOCK = "cap_wall_clock"

# Cap de longueur sur le ``reason`` text libre fourni par Haiku via
# ``escalate_to_sonnet.input.reason``. Défense en profondeur contre
# l'indirect prompt injection : même si Haiku est convaincu de forger
# une raison longue / formatée, l'UI n'affiche qu'un extrait.
_SELF_REASON_MAX_CHARS = 200


# ---------------------------------------------------------------------------
# Tool méta escalate_to_sonnet
# ---------------------------------------------------------------------------

ESCALATE_TOOL_NAME = "escalate_to_sonnet"

ESCALATE_TOOL_SCHEMA: dict[str, Any] = {
    "name": ESCALATE_TOOL_NAME,
    "description": (
        "Signale que la requête utilisateur dépasse tes capacités "
        "actuelles et qu'un modèle plus puissant (Sonnet) doit "
        "prendre le relais. Appelle-le SEUL (pas d'autre tool_use "
        "dans la même réponse) SI ET SEULEMENT SI : (a) la question "
        "nécessite une comparaison multi-entités fine, (b) elle "
        "demande un raisonnement financier qui dépasse tes "
        "capacités, (c) tu sens qu'il te faudra 3+ tool calls "
        "supplémentaires que tu ne pourras pas synthétiser "
        "correctement. Fournis une raison courte (≤ 1 phrase). "
        "L'escalade conserve l'intégralité du contexte conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": ("Pourquoi tu estimes ne pas pouvoir conclure seul."),
            }
        },
        "required": ["reason"],
    },
}


# ---------------------------------------------------------------------------
# Boucle routée
# ---------------------------------------------------------------------------


async def run_routed_turn(
    state: ConversationState,
    user_message: str,
) -> AsyncIterator[dict[str, Any]]:
    """Exécute un tour agent avec routing Haiku → Sonnet.

    Flow :

    1. ``pick_initial_tier`` → Haiku (défaut) ou Sonnet (keyword complexe).
    2. Lancer ``run_turn`` avec ce tier. Si Haiku, injecter le tool
       local ``escalate_to_sonnet`` via ``extra_tools``.
    3. Itération manuelle ``wait_for(__anext__, timeout=remaining)`` pour
       respecter le wall-clock cap sans tomber dans l'antipattern PEP 789.
    4. Forwarder les events S03 tels quels + émettre nos events routing.
    5. Détecter escalade :

       - **self** : event ``tool_use`` avec ``name == "escalate_to_sonnet"``.
       - **forced (tool_calls)** : ``state.tool_calls_count - initial_count
         >= MAX_TOOL_CALLS_PER_TURN``.
       - **forced (wall_clock)** : ``remaining <= 0`` en haut de boucle,
         ou ``asyncio.TimeoutError`` levé par ``wait_for``.

    6. Sur escalade : ``break``, ``gen.aclose()`` (release
       ``state.lock``), relancer ``run_turn`` en tier Sonnet avec
       ``continuation=True``. L'invariant I2 de S03 garantit que
       ``state.messages`` est cohérent (pas d'orphan ``tool_use``).
    7. Émettre ``routing_done`` en toute fin (après ``end`` de S03).
    """
    initial_tier = pick_initial_tier(user_message)
    yield {
        "type": "routing_initial",
        "tier": initial_tier.value,
        "reason": "keyword" if initial_tier == ModelTier.SONNET else "default",
    }
    logger.info("routing_initial", tier=initial_tier.value)

    initial_count = state.tool_calls_count
    deadline = time.monotonic() + WALL_CLOCK_S
    escalated = False
    escalation_reason_code: str | None = None
    escalation_reason: str | None = None
    escalation_mode: str | None = None
    capped_in_sonnet = False  # set si cap atteint alors qu'on est déjà Sonnet
    capped_reason_code: str | None = None
    capped_reason: str | None = None

    def _per_turn() -> int:
        return state.tool_calls_count - initial_count

    def _hit_cap_tool_calls() -> bool:
        return _per_turn() >= MAX_TOOL_CALLS_PER_TURN

    def _emit_cap_hit(reason_code: str, reason_detail: str) -> dict[str, Any]:
        """Fabrique l'event à émettre quand un cap backend est atteint,
        selon le tier courant. Ne met **pas** à jour les flags d'état —
        c'est à l'appelant de le faire en regardant ``initial_tier``.

        Format unifié pour les deux events :
        - ``reason_code`` : enum stable à matcher (``==``) côté consumer.
        - ``reason`` : détail humain à afficher, **pas** à matcher.
        """
        if initial_tier == ModelTier.HAIKU:
            return {
                "type": "escalation",
                "reason_code": reason_code,
                "reason": reason_detail,
                "mode": "forced",
            }
        return {
            "type": "capped",
            "reason_code": reason_code,
            "reason": reason_detail,
            "count": _per_turn(),
        }

    # Haiku reçoit le tool escalate_to_sonnet en plus des Pappers.
    # Sonnet ne l'a pas (c'est déjà lui, rien à escalader).
    extra_tools = [ESCALATE_TOOL_SCHEMA] if initial_tier == ModelTier.HAIKU else []

    gen = run_turn(
        state,
        user_message,
        tier=initial_tier,
        extra_tools=extra_tools,
    )
    aiter = gen.__aiter__()
    try:
        while True:
            # --- Wall-clock cap (cahier §5.3) ---
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason_detail = f"{WALL_CLOCK_S}s"
                logger.warning("routing_cap_wall_clock", seconds=WALL_CLOCK_S)
                yield _emit_cap_hit(REASON_CODE_CAP_WALL_CLOCK, reason_detail)
                if initial_tier == ModelTier.HAIKU:
                    escalation_reason_code = REASON_CODE_CAP_WALL_CLOCK
                    escalation_reason = reason_detail
                    escalation_mode = "forced"
                    escalated = True
                else:
                    capped_reason_code = REASON_CODE_CAP_WALL_CLOCK
                    capped_reason = reason_detail
                    capped_in_sonnet = True
                break

            # --- Next event, bounded par remaining ---
            try:
                event = await asyncio.wait_for(aiter.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            except TimeoutError:
                # Un single await dans run_turn a dépassé `remaining`.
                # Même traitement que wall-clock cap hit (même reason_code).
                reason_detail = f"{WALL_CLOCK_S}s"
                logger.warning("routing_cap_wall_clock_wait_for", seconds=WALL_CLOCK_S)
                yield _emit_cap_hit(REASON_CODE_CAP_WALL_CLOCK, reason_detail)
                if initial_tier == ModelTier.HAIKU:
                    escalation_reason_code = REASON_CODE_CAP_WALL_CLOCK
                    escalation_reason = reason_detail
                    escalation_mode = "forced"
                    escalated = True
                else:
                    capped_reason_code = REASON_CODE_CAP_WALL_CLOCK
                    capped_reason = reason_detail
                    capped_in_sonnet = True
                break

            # --- Self-escalade : break AVANT que run_turn exécute le
            # "tool" escalate_to_sonnet via mcp_pappers (MCP ne le
            # connaît pas, ça partirait en PappersError / 404). ---
            if event.get("type") == "tool_use" and event.get("name") == ESCALATE_TOOL_NAME:
                raw_reason = event.get("input", {}).get("reason") or "unspecified"
                # Cap de longueur — défense contre une reason anormalement
                # longue (prompt injection indirecte ou bug Haiku).
                reason_detail = str(raw_reason)[:_SELF_REASON_MAX_CHARS]
                escalation_reason_code = REASON_CODE_SELF
                escalation_reason = reason_detail
                escalation_mode = "self"
                escalated = True
                logger.info("routing_escalate_self", reason_length=len(reason_detail))
                yield {
                    "type": "escalation",
                    "reason_code": REASON_CODE_SELF,
                    "reason": reason_detail,
                    "mode": "self",
                }
                break

            yield event

            # --- Cap tool calls par-turn ---
            if _hit_cap_tool_calls():
                per_turn = _per_turn()
                reason_detail = f"{per_turn}/{MAX_TOOL_CALLS_PER_TURN} tool calls"
                logger.warning("routing_cap_tool_calls", per_turn_count=per_turn)
                yield _emit_cap_hit(REASON_CODE_CAP_TOOL_CALLS, reason_detail)
                if initial_tier == ModelTier.HAIKU:
                    escalation_reason_code = REASON_CODE_CAP_TOOL_CALLS
                    escalation_reason = reason_detail
                    escalation_mode = "forced"
                    escalated = True
                else:
                    capped_reason_code = REASON_CODE_CAP_TOOL_CALLS
                    capped_reason = reason_detail
                    capped_in_sonnet = True
                break
    finally:
        # Essentiel : libère state.lock en forçant le cleanup du
        # generator S03 (async with state.lock: fin de bloc). Sans
        # aclose(), le lock peut rester détenu jusqu'au GC. aclose()
        # est aussi ce qui propage CancelledError dans S03 si on a
        # break après un wait_for timeout — S03 cleanup cohérent.
        await gen.aclose()

    if escalated:
        # L'invariant I2 de S03 garantit que state.messages est cohérent :
        # l'itération contenant l'escalade (ou sa détection) n'a pas été
        # appendée. Sonnet reprend sur un state propre avec continuation.
        #
        # Sonnet n'a PAS de wall-clock cap appliqué (pas de tier au-dessus
        # pour escalader ; la démo peut tolérer une réponse Sonnet longue).
        # Si besoin post-MVP : wrapper ce 2e async for dans la même
        # logique wait_for + event capped, sans relance.
        async for event in run_turn(
            state,
            "",
            tier=ModelTier.SONNET,
            continuation=True,
        ):
            yield event

    model_used = (
        ModelTier.SONNET.value
        if escalated or initial_tier == ModelTier.SONNET
        else ModelTier.HAIKU.value
    )
    tool_calls_per_turn = state.tool_calls_count - initial_count
    logger.info(
        "routing_done",
        model_used=model_used,
        escalated=escalated,
        escalation_mode=escalation_mode,
        escalation_reason_code=escalation_reason_code,
        capped=capped_in_sonnet,
        capped_reason_code=capped_reason_code,
        tool_calls_count=tool_calls_per_turn,
    )
    yield {
        "type": "routing_done",
        "model_used": model_used,
        "escalated": escalated,
        "escalation_mode": escalation_mode,
        "escalation_reason_code": escalation_reason_code,
        "escalation_reason": escalation_reason,
        "capped": capped_in_sonnet,
        "capped_reason_code": capped_reason_code,
        "capped_reason": capped_reason,
        "tool_calls_count": tool_calls_per_turn,
    }
