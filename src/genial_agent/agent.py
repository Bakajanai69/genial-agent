"""Boucle agent Claude + MCP Pappers.

Flow par tour :

1. Wrap le message utilisateur dans ``<user_input>…</user_input>``, append
   à ``state.messages``.
2. Discovery MCP Pappers + conversion schéma Anthropic (via S02).
3. Boucle :

   a. ``client.messages.stream(...)`` avec le state complet.
   b. Yield ``text`` events pendant le streaming.
   c. ``get_final_message()`` → extraire content blocks + stop_reason.
   d. Append assistant message au state (tel quel).
   e. Émettre ``llm_meta`` event.
   f. Si ``stop_reason == "tool_use"`` → pour chaque tool_use block :

      * Yield ``tool_use`` event.
      * ``mcp_pappers.call_tool(name, args)`` (cache + retry + single-flight S02).
      * Yield ``tool_result`` event.
      * Append tool_result au state.

      Puis reboucle (goto 3.a).
   g. Sinon (end_turn / max_tokens / refusal) → émettre ``end`` event, sortir.

4. ``MAX_ITERATIONS`` = 12 (filet anti-boucle-infinie).

Compat S04 (routing) : paramètres ``extra_tools`` (pour
``escalate_to_sonnet``) et ``continuation=True`` (pour reprendre après
escalade sans ré-append user message).

Compat S07 (stats) : chaque appel Claude émet un event ``llm_meta`` qui
contient ``request_id``, ``model``, ``input_tokens``, ``output_tokens``,
``latency_ms``, ``stop_reason``. S07 incrémente les compteurs depuis
ce call-site.

Anti-injection (§14.3 C1) : ``wrap_user_input`` encadre tout message
utilisateur avant append. Le system prompt déclare explicitement que le
contenu de ``<user_input>`` est donnée, pas instruction.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog
from anthropic import AsyncAnthropic, RateLimitError
from anthropic.types import MessageParam, ToolUseBlock

from genial_agent import mcp_pappers
from genial_agent.config import settings
from genial_agent.mcp_pappers import PappersError
from genial_agent.models import (
    DEFAULT_INFERENCE_GEO,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    MAX_ITERATIONS,
    ModelTier,
    model_id,
)
from genial_agent.prompts import SYSTEM_PROMPT_AGENT

logger = structlog.get_logger(__name__)


@dataclass
class ConversationState:
    """État de conversation par session.

    Instancié par session Chainlit en S06 (``cl.user_session.set("state",
    ConversationState())``). **Jamais de singleton global** : Chainlit
    gère la concurrence multi-session, un état partagé corromprait les
    conversations.

    Attributes:
        messages: historique brut au format Anthropic ``MessageParam``.
            Inclut les blocks ``tool_use`` et ``tool_result`` pour le
            pairing multi-tour.
        tool_calls_count: compteur cumulatif de tool calls pour ce
            state. Consommé par S05 (cap 5 / tour) et S07 (stats).
    """

    messages: list[MessageParam] = field(default_factory=list)
    tool_calls_count: int = 0


def wrap_user_input(text: str) -> str:
    """Encadre l'input utilisateur pour le distinguer des instructions
    système (cf. cahier §14.3 C1).

    Le system prompt déclare que tout ce qui est entre ces tags est
    donnée, pas directive. Appelé sur **tous** les messages user texte
    (pas sur les ``tool_result`` — évident, mais le review S03 vérifie
    qu'il n'existe aucun autre ``append({"role": "user", "content":
    <str>})`` dans le code).
    """
    return f"<user_input>\n{text}\n</user_input>"


async def run_turn(
    state: ConversationState,
    user_message: str,
    tier: ModelTier = ModelTier.HAIKU,
    extra_tools: list[dict[str, Any]] | None = None,
    continuation: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> AsyncIterator[dict[str, Any]]:
    """Exécute un tour agent. Yield des events structurés.

    Args:
        state: état de conversation mutable (messages, compteurs).
        user_message: texte brut de l'utilisateur (sera wrappé).
        tier: Haiku (default) ou Sonnet. Passé par S04 routing.
        extra_tools: tools additionnels (ex : ``escalate_to_sonnet`` S04).
        continuation: ``True`` si on reprend après escalade (ne pas
            ré-append user message, le state a déjà le contexte).
        max_tokens: cap tokens output par appel Claude.
        temperature: 0.2 par défaut (factuel).

    Yields:
        dict events, schéma :

        - ``{"type": "text", "content": str}`` — text deltas streaming.
        - ``{"type": "tool_use", "id": str, "name": str, "input": dict}``
        - ``{"type": "tool_result", "tool_use_id": str, "is_error": bool,
          "content_preview": str}``
        - ``{"type": "llm_meta", "model": str, "input_tokens": int,
          "output_tokens": int, "request_id": str | None,
          "latency_ms": int, "stop_reason": str}``
        - ``{"type": "end", "tool_calls_count": int, "reason": str}``
    """
    if not continuation:
        state.messages.append({"role": "user", "content": wrap_user_input(user_message)})

    # Discovery MCP + mapping schéma Anthropic (S02). Pas de duplication
    # du schéma converter : règle README "Décisions de cohérence".
    pappers_tools = await mcp_pappers.list_available_tools()
    tools_schema = mcp_pappers.to_anthropic_schema(pappers_tools)
    if extra_tools:
        tools_schema = tools_schema + extra_tools

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    # ``inference_geo`` n'est supporté que sur Sonnet à date (2026-04-24).
    # Haiku rejette avec ``BadRequestError 400 : "<id> does not support
    # inference_geo."`` — on ne passe donc le paramètre que pour Sonnet.
    # Le cahier §6.2 fige ``global`` pour la donnée côté API ; Haiku est
    # routé par Anthropic sans promesse géographique à date, cohérent.
    stream_kwargs: dict[str, Any] = {
        "model": model_id(tier),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": SYSTEM_PROMPT_AGENT,
        "messages": state.messages,
        "tools": tools_schema,
        "tool_choice": {"type": "auto"},
    }
    if tier == ModelTier.SONNET:
        stream_kwargs["inference_geo"] = DEFAULT_INFERENCE_GEO

    for _iteration in range(MAX_ITERATIONS):
        started = time.monotonic()
        try:
            async with client.messages.stream(**stream_kwargs) as stream:
                async for text_delta in stream.text_stream:
                    yield {"type": "text", "content": text_delta}
                final = await stream.get_final_message()
                # ``stream.request_id`` lit les headers httpx ; None si
                # la réponse n'a pas encore été reçue (cas dégénéré).
                try:
                    request_id = stream.request_id
                except Exception:  # noqa: BLE001
                    request_id = None
        except RateLimitError:
            # Le SDK Anthropic retry déjà 2 fois en interne. Au-delà, on
            # remonte un event "end" explicite pour que S06 affiche un
            # message cadré sans casser l'UI.
            logger.warning("agent_rate_limited")
            yield {
                "type": "end",
                "tool_calls_count": state.tool_calls_count,
                "reason": "rate_limited",
            }
            return

        latency_ms = int((time.monotonic() - started) * 1000)
        yield {
            "type": "llm_meta",
            "model": final.model,
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
            "request_id": request_id,
            "latency_ms": latency_ms,
            "stop_reason": final.stop_reason or "end_turn",
        }

        # Append assistant message au state — content blocks tels quels
        # (inclut les tool_use blocks, obligatoire pour le pairing avec
        # tool_result au prochain message user). ``exclude_none=True``
        # évite de pousser des champs nullables du SDK 2026 (ex.
        # ``caller=None`` sur ``ToolUseBlock``) qui pourraient être
        # rejetés à l'entrée API lors du prochain tour.
        state.messages.append(
            {
                "role": "assistant",
                "content": [b.model_dump(exclude_none=True) for b in final.content],
            }
        )

        if final.stop_reason != "tool_use":
            yield {
                "type": "end",
                "tool_calls_count": state.tool_calls_count,
                "reason": final.stop_reason or "end_turn",
            }
            return

        # Exécuter chaque tool_use, append tool_result en premier dans
        # le prochain message user (contrainte API : tool_result blocks
        # FIRST in content array, pas de texte mixé — sinon HTTP 400).
        tool_result_blocks: list[dict[str, Any]] = []
        for block in final.content:
            if not isinstance(block, ToolUseBlock):
                continue
            state.tool_calls_count += 1
            yield {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
            try:
                result = await mcp_pappers.call_tool(block.name, block.input)
                content_str = _stringify_tool_result(result)
                is_error = False
            except PappersError as exc:
                logger.info(
                    "agent_pappers_business_error",
                    tool_name=block.name,
                    error_type=type(exc).__name__,
                )
                content_str = f"Erreur Pappers : {exc}"
                is_error = True
            except Exception as exc:  # noqa: BLE001 — informer Claude, pas remonter
                logger.warning(
                    "agent_tool_call_failed",
                    tool_name=block.name,
                    error_type=type(exc).__name__,
                )
                content_str = f"Erreur technique : {type(exc).__name__}"
                is_error = True

            yield {
                "type": "tool_result",
                "tool_use_id": block.id,
                "is_error": is_error,
                "content_preview": content_str[:200],
            }
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content_str,
                    "is_error": is_error,
                }
            )

        # Append le message user (tool_results en 1er, pas de texte mixé).
        state.messages.append({"role": "user", "content": tool_result_blocks})

    # MAX_ITERATIONS atteint : filet anti-boucle-infinie.
    logger.warning(
        "agent_max_iterations_hit",
        tool_calls_count=state.tool_calls_count,
    )
    yield {
        "type": "end",
        "tool_calls_count": state.tool_calls_count,
        "reason": "max_iterations",
    }


# Borne de sécurité sur la taille du tool_result passé à Claude. Pappers
# peut renvoyer ~200k tokens sur une ``recherche-dirigeants`` d'un
# Bernard Arnault — au-delà, on explose le context window Haiku (200k)
# avant même le prochain tour. 16_000 caractères ≈ 4–5k tokens : marge
# confortable pour enchaîner 4-5 tool calls dans une conversation
# multi-turn sans tripper ``prompt is too long``.
_TOOL_RESULT_MAX_CHARS = 16_000


def _stringify_tool_result(result: dict[str, Any]) -> str:
    """Extrait un texte compact du ``CallToolResult`` sérialisé par S02.

    S02 retourne ``result.model_dump(mode="json")`` — dict avec
    ``content: [{"type": "text", "text": "..."}]`` en général. On sort
    le premier bloc texte (cohérent avec ``mcp_pappers._first_text_block``)
    ou on tombe sur un ``json.dumps`` du payload complet. Résultat
    tronqué à ``_TOOL_RESULT_MAX_CHARS`` caractères dans tous les cas
    pour éviter de saturer le context window Claude sur les retours
    Pappers volumineux (ex : ``recherche-dirigeants`` avec 30+ mandats).
    """

    raw: str
    content = result.get("content")
    raw = json.dumps(result, ensure_ascii=False)
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype is not None and btype != "text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                raw = text
                break

    if len(raw) <= _TOOL_RESULT_MAX_CHARS:
        return raw
    marker = "\n…[tronqué : réponse Pappers dépasse la borne agent]"
    return raw[: _TOOL_RESULT_MAX_CHARS - len(marker)] + marker
