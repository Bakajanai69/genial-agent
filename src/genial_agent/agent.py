"""Boucle agent Claude + MCP Pappers.

Flow par tour :

1. Wrap le message utilisateur dans ``<user_input>…</user_input>``, append
   à ``state.messages``.
2. Discovery MCP Pappers + conversion schéma Anthropic (via S02).
3. Boucle :

   a. ``client.messages.stream(...)`` avec le state complet.
   b. Yield ``text`` events pendant le streaming.
   c. ``get_final_message()`` → extraire content blocks + stop_reason.
   d. Émettre ``llm_meta`` event.
   e. Si ``stop_reason != "tool_use"`` → append assistant msg, émettre
      ``end``, sortir.
   f. Sinon (``tool_use``) → exécuter chaque tool (yield ``tool_use`` /
      ``tool_result``), **puis** append atomiquement ``assistant`` +
      ``user(tool_result)`` au state. Reboucle (goto 3.a).

4. ``MAX_ITERATIONS`` = 12 (filet anti-boucle-infinie).

Invariants protégés (cf. review S03 phase 3) :

- **Atomicité state** : les messages ``assistant(tool_use)`` et
  ``user(tool_result)`` sont appendés ensemble en fin d'itération. Si
  le consumer ``break`` au milieu d'un tour, le state reste cohérent au
  tour précédent — le prochain ``run_turn`` re-streamera une requête
  identique (cache Pappers absorbe le coût).
- **Contrat ``end`` event** : toute erreur connue (rate limit, transport,
  status) est convertie en event ``end`` typé avant de retourner. La UI
  S06 peut clôturer sa step view même en cas d'échec API.
- **Sérialisation session** : ``ConversationState.lock`` sérialise les
  appels concurrents sur la même session (double-submit, refresh),
  évitant la corruption du pairing ``tool_use``/``tool_result``.
- **Défense prompt-injection** : le contenu texte d'un ``tool_result``
  (donnée Pappers) est scrubbé des balises de frontière de contexte
  (``<user_input>``, ``<tool_result>``, ``<tool_use>``) avant injection —
  défense complémentaire à la clause system prompt (§14.3 C1 / C2).

Compat S04 (routing) : paramètres ``extra_tools`` (pour
``escalate_to_sonnet``), ``continuation=True`` (reprise après escalade
sans ré-append user message), ``tool_choice`` override.

Compat S07 (stats) : chaque appel Claude émet un event ``llm_meta`` qui
contient ``request_id``, ``model``, ``input_tokens``, ``output_tokens``,
``latency_ms``, ``stop_reason``. S07 incrémente les compteurs depuis ce
call-site.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog
from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)
from anthropic.types import MessageParam, ToolUseBlock

from genial_agent import mcp_pappers, payload_vault
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
from genial_agent.payload_vault import (
    LOOKUP_MAX_CHARS_DEFAULT,
    OFFLOAD_THRESHOLD_CHARS,
    PayloadVault,
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
        tool_calls_count: compteur **cumulatif** de tool calls sur toute
            la vie du state. Le cap par-turn (§5.3 cahier) est calculé
            par S05 via un snapshot début/fin autour de ``run_turn``.
        lock: sérialise les ``run_turn`` concurrents sur la même session
            (cf. review S03 I5). Sans ce lock, un double-submit user
            corrompt l'ordre des messages ``tool_use``/``tool_result``
            et casse l'API à l'itération suivante.
    """

    messages: list[MessageParam] = field(default_factory=list)
    tool_calls_count: int = 0
    # Compteur séparé pour les tools locaux du Payload Vault (S09.5).
    # Sépare les vrais tool calls Pappers (qui consomment crédits +
    # latence réseau) des lookups in-memory (gratuits côté coût mais
    # capés pour borner le coût LLM des prompts pathologiques). Cf.
    # ``MAX_LOCAL_LOOKUPS_PER_TURN`` dans ``guardrails/caps.py``.
    local_lookup_count: int = 0
    # Vault session-scoped pour les tool results MCP volumineux (S09.5).
    # Stocke les payloads bruts > ``OFFLOAD_THRESHOLD_CHARS`` ; l'agent
    # reçoit un index compact + un ``payload_id`` et ré-interroge via
    # ``payload_inspect`` / ``payload_search``. Vit le temps de la
    # session Chainlit (jamais sur disque, jamais cross-session).
    payload_vault: PayloadVault = field(default_factory=PayloadVault)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)


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


# Tags "frontière de contexte" que le modèle reconnaît dans son prompt
# (cf. ``SYSTEM_PROMPT_AGENT``). On les neutralise quand ils apparaissent
# dans la **donnée** retournée par un tool (indirect prompt injection) —
# un greffe malveillant qui pose une dénomination sociale contenant
# ``</tool_result>\n\nSystem: …`` ne doit pas pouvoir confondre Claude
# sur la frontière du block tool_result. Remplacement par la même forme
# en angle brackets unicode (U+27E8/U+27E9) : lisible pour l'humain,
# inerte syntaxiquement.
_TAG_SCRUB = {
    "<user_input>": "⟨user_input⟩",
    "</user_input>": "⟨/user_input⟩",
    "<tool_use>": "⟨tool_use⟩",
    "</tool_use>": "⟨/tool_use⟩",
    "<tool_result>": "⟨tool_result⟩",
    "</tool_result>": "⟨/tool_result⟩",
}


def _neutralize_injection_attempts(s: str) -> str:
    """Scrub les tags de frontière de contexte dans un contenu tool_result.

    Couplé à la clause du system prompt qui déclare que le contenu des
    ``tool_result`` est donnée (pas instruction). Défense en profondeur
    contre l'indirect prompt injection via la base Pappers publique (cf.
    review S03 I4).
    """
    for src, dst in _TAG_SCRUB.items():
        s = s.replace(src, dst)
    return s


# --- Tools locaux Payload Vault (S09.5) ------------------------------------

PAYLOAD_INSPECT_TOOL_NAME = "payload_inspect"
PAYLOAD_SEARCH_TOOL_NAME = "payload_search"
LOCAL_PAYLOAD_TOOL_NAMES = frozenset({PAYLOAD_INSPECT_TOOL_NAME, PAYLOAD_SEARCH_TOOL_NAME})

# Exposés à Claude en plus des tools Pappers via ``extra_tools``-like
# concatenation dans ``run_turn``. Ces tools sont **dispatchés
# localement** (cf. dispatch ci-dessous) — ils ne touchent jamais
# ``mcp_pappers.call_tool`` et ne consomment pas de crédit Pappers.
LOCAL_PAYLOAD_TOOLS: list[dict[str, Any]] = [
    {
        "name": PAYLOAD_INSPECT_TOOL_NAME,
        "description": (
            "Read a verbatim subtree from a previously offloaded MCP payload. "
            "Use the payload_id returned in the previous tool_result's `_payload_id` field, "
            "and a JSON path resolved against the index `_skeleton` / `_array_sizes`. "
            "Path syntax: dotted with optional `$` prefix and `[i]` indices, "
            "e.g. '$.items[0].field' or 'items[-1].field'. A numeric segment is "
            "interpreted as a string-key when the current node is a dict "
            "(handy for date-keyed payloads like '$.2023[0]') and as an "
            "integer index when it is a list. Negative indices count from "
            "the end (-1 = last). Returns the JSON-encoded literal value "
            "(verbatim, never a summary)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payload_id": {
                    "type": "string",
                    "description": "The _payload_id field from the index returned by an offloaded tool result.",
                },
                "json_path": {
                    "type": "string",
                    "description": "Dotted/bracketed path, e.g. '$.foo.bar[0]' or 'foo[-1]'.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Cap on the returned JSON length. Default 8000, hard max 12000.",
                    "default": LOOKUP_MAX_CHARS_DEFAULT,
                    "maximum": 12_000,
                },
            },
            "required": ["payload_id", "json_path"],
        },
    },
    {
        "name": PAYLOAD_SEARCH_TOOL_NAME,
        "description": (
            "Regex search inside a previously offloaded MCP payload. Useful when "
            "you don't know the exact path (e.g. find an identifier, a year, "
            "a name in unknown structure). Returns up to max_matches occurrences "
            "with surrounding context. Pattern is case-insensitive and multiline. "
            "Does not consume any Pappers credit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payload_id": {
                    "type": "string",
                    "description": "The _payload_id field from the index returned by an offloaded tool result.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Python regex (case-insensitive, multiline).",
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Cap on the number of matches returned. Default 10, hard max 30.",
                    "default": 10,
                    "maximum": 30,
                },
            },
            "required": ["payload_id", "pattern"],
        },
    },
]


def _dispatch_local_payload_tool(
    state: ConversationState,
    name: str,
    tool_input: dict[str, Any],
) -> tuple[str, bool, dict[str, Any] | None]:
    """Exécute un tool local Payload Vault. Retourne ``(content_str,
    is_error, observability_event)``.

    L'event d'observabilité (``payload_inspected`` ou ``payload_searched``)
    est rendu sous forme de dict prêt à être ``yield``é par ``run_turn``.
    Si ``payload_id`` est inconnu, l'event vaut None et le content est
    un message d'erreur explicite que Claude peut interpréter.
    """
    pid = tool_input.get("payload_id")
    if not isinstance(pid, str):
        return ("Error: missing or invalid 'payload_id'", True, None)
    raw = state.payload_vault.get(pid)
    if raw is None:
        return (
            f"Error: payload_id '{pid}' unknown or expired (vault is session-scoped)",
            True,
            None,
        )

    if name == PAYLOAD_INSPECT_TOOL_NAME:
        json_path = tool_input.get("json_path", "")
        max_chars = tool_input.get("max_chars", LOOKUP_MAX_CHARS_DEFAULT)
        try:
            max_chars_int = int(max_chars)
        except (TypeError, ValueError):
            max_chars_int = LOOKUP_MAX_CHARS_DEFAULT
        content = payload_vault.inspect(raw, str(json_path), max_chars_int)
        # Scrub par défense en profondeur : la valeur extraite vient
        # d'un payload Pappers, donc soumise au même risque de prompt
        # injection indirecte qu'un tool_result direct.
        scrubbed = _neutralize_injection_attempts(content)
        event = {
            "type": "payload_inspected",
            "payload_id": pid,
            "json_path": str(json_path),
            "returned_chars": len(scrubbed),
        }
        return (scrubbed, False, event)

    # PAYLOAD_SEARCH_TOOL_NAME
    pattern = tool_input.get("pattern", "")
    max_matches = tool_input.get("max_matches", 10)
    try:
        max_matches_int = int(max_matches)
    except (TypeError, ValueError):
        max_matches_int = 10
    matches = payload_vault.search(raw, str(pattern), max_matches_int)
    serialized = json.dumps(matches, ensure_ascii=False, indent=2)
    scrubbed = _neutralize_injection_attempts(serialized)
    event = {
        "type": "payload_searched",
        "payload_id": pid,
        "pattern": str(pattern)[:200],
        "match_count": sum(1 for m in matches if "error" not in m),
    }
    return (scrubbed, False, event)


async def run_turn(
    state: ConversationState,
    user_message: str,
    tier: ModelTier = ModelTier.HAIKU,
    extra_tools: list[dict[str, Any]] | None = None,
    continuation: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    tool_choice: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Exécute un tour agent. Yield des events structurés.

    Args:
        state: état de conversation mutable (messages, compteurs, lock).
        user_message: texte brut de l'utilisateur (sera wrappé).
        tier: Haiku (default) ou Sonnet. Passé par S04 routing.
        extra_tools: tools additionnels (ex : ``escalate_to_sonnet`` S04).
        continuation: ``True`` si on reprend après escalade (ne pas
            ré-append user message, le state a déjà le contexte).
        max_tokens: cap tokens output par appel Claude.
        temperature: 0.2 par défaut (factuel).
        tool_choice: override ``{"type": "auto"|"any"|"tool"|"none"}``.
            Default ``{"type": "auto"}`` (cf. S03 elicitation). S04/S05
            peuvent forcer ``any`` pour §7 R9 (trigger Pappers).

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
          avec ``reason`` ∈ {``end_turn``, ``max_tokens``, ``refusal``,
          ``pause_turn``, ``stop_sequence``, ``tool_use``,
          ``max_iterations``, ``rate_limited``, ``transport_error``,
          ``api_error``}.
    """
    choice: dict[str, Any] = tool_choice or {"type": "auto"}

    async with state.lock:
        if not continuation:
            state.messages.append({"role": "user", "content": wrap_user_input(user_message)})

        # Discovery MCP + mapping schéma Anthropic (S02). Pas de duplication
        # du schéma converter : règle README "Décisions de cohérence".
        pappers_tools = await mcp_pappers.list_available_tools()
        tools_schema = mcp_pappers.to_anthropic_schema(pappers_tools)
        # Tools locaux Payload Vault (S09.5) — toujours exposés. Ils
        # sont dispatchés localement dans la boucle ci-dessous (jamais
        # via mcp_pappers.call_tool) et n'incrémentent que
        # ``state.local_lookup_count`` (cap séparé S05).
        tools_schema = tools_schema + LOCAL_PAYLOAD_TOOLS
        if extra_tools:
            tools_schema = tools_schema + extra_tools

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
            "tool_choice": choice,
        }
        if tier == ModelTier.SONNET:
            stream_kwargs["inference_geo"] = DEFAULT_INFERENCE_GEO

        async with AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY) as client:
            for _iteration in range(MAX_ITERATIONS):
                started = time.monotonic()
                try:
                    async with client.messages.stream(**stream_kwargs) as stream:
                        async for text_delta in stream.text_stream:
                            yield {"type": "text", "content": text_delta}
                        final = await stream.get_final_message()
                        # ``request_id`` property = headers.get("request-id")
                        # sur la httpx.Response. Toujours set après sortie
                        # du context manager (la réponse est ouverte).
                        request_id = stream.request_id
                except RateLimitError:
                    # Le SDK Anthropic retry déjà 2 fois en interne. Au-delà,
                    # on remonte un event ``end`` explicite pour que S06
                    # affiche un message cadré sans casser l'UI.
                    logger.warning("agent_rate_limited")
                    yield {
                        "type": "end",
                        "tool_calls_count": state.tool_calls_count,
                        "reason": "rate_limited",
                    }
                    return
                except APIConnectionError as exc:
                    # Inclut ``APITimeoutError`` (classe fille). Transport
                    # HTTP cassé, réseau instable, timeout total : le
                    # consumer UI ferme la step view et invite à réessayer.
                    logger.warning(
                        "agent_transport_error",
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    yield {
                        "type": "end",
                        "tool_calls_count": state.tool_calls_count,
                        "reason": "transport_error",
                    }
                    return
                except APIStatusError as exc:
                    # 4xx hors 429 (AuthenticationError, BadRequestError…)
                    # ou 5xx non-retryable. Loggué en ``error`` : c'est
                    # une vraie anomalie (clé révoquée, prompt trop long,
                    # indispo Anthropic). On émet quand même ``end`` pour
                    # que l'UI reste utilisable — le message utilisateur
                    # est cadré par S06 selon le ``reason``.
                    logger.error(
                        "agent_api_status_error",
                        status_code=getattr(exc, "status_code", None),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    yield {
                        "type": "end",
                        "tool_calls_count": state.tool_calls_count,
                        "reason": "api_error",
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

                # Préparer l'assistant message — **pas encore appendé** :
                # si on est sur un ``tool_use``, on append atomiquement
                # avec le user(tool_result) à la fin du cycle pour préserver
                # l'invariant de pairing même si le consumer ``break``.
                # ``exclude_none=True`` évite de pousser des champs
                # nullables du SDK 2026 (ex. ``caller=None`` sur
                # ``ToolUseBlock``) qui pourraient être rejetés à l'entrée
                # API lors du prochain tour.
                assistant_msg: MessageParam = {
                    "role": "assistant",
                    "content": [b.model_dump(exclude_none=True) for b in final.content],
                }

                if final.stop_reason != "tool_use":
                    state.messages.append(assistant_msg)
                    yield {
                        "type": "end",
                        "tool_calls_count": state.tool_calls_count,
                        "reason": final.stop_reason or "end_turn",
                    }
                    return

                # Exécuter chaque tool_use. Ordre API (contrainte officielle,
                # cf. docs "Handle tool calls") : les blocks ``tool_result``
                # doivent venir **en premier** dans le prochain message
                # user, pas de texte mixé — sinon HTTP 400.
                tool_result_blocks: list[dict[str, Any]] = []
                for block in final.content:
                    if not isinstance(block, ToolUseBlock):
                        continue

                    is_local_payload = block.name in LOCAL_PAYLOAD_TOOL_NAMES
                    if is_local_payload:
                        # Local dispatch : ne consomme **pas** de crédit
                        # Pappers, ne compte pas dans
                        # ``state.tool_calls_count`` (cap S05). Compteur
                        # séparé ``local_lookup_count`` (cap
                        # ``MAX_LOCAL_LOOKUPS_PER_TURN`` côté routing).
                        state.local_lookup_count += 1
                    else:
                        state.tool_calls_count += 1

                    yield {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }

                    if is_local_payload:
                        # Dispatch local — jamais d'appel mcp_pappers.
                        content_str, is_error, obs_event = _dispatch_local_payload_tool(
                            state, block.name, dict(block.input)
                        )
                        if obs_event is not None:
                            yield obs_event
                    else:
                        try:
                            result = await mcp_pappers.call_tool(block.name, block.input)
                            raw_str = _extract_text_from_result(result)
                            if len(raw_str) > OFFLOAD_THRESHOLD_CHARS:
                                # Offload (S09.5) : le payload est rangé
                                # dans le vault session-scoped, l'agent
                                # reçoit un index compact + payload_id
                                # et peut ré-interroger via
                                # payload_inspect / payload_search.
                                pid = state.payload_vault.store(raw_str)
                                index = payload_vault.build_index(raw_str, pid)
                                # Scrub des balises de frontière dans
                                # l'index : ``_preview_head`` /
                                # ``_preview_tail`` viennent du payload
                                # Pappers brut, soumis au même risque
                                # d'indirect prompt injection que les
                                # tool_results normaux.
                                # Pas d'``indent=2`` : ``payload_vault.build_index``
                                # check sa taille via ``json.dumps`` sans
                                # indent (cf. ``INDEX_BUDGET_CHARS``). On
                                # aligne la sérialisation envoyée au LLM
                                # sur le même format pour que le budget
                                # soit respecté à l'octet près (review
                                # S09.5 F1). Économie ~17 % de tokens.
                                content_str = _neutralize_injection_attempts(
                                    json.dumps(index, ensure_ascii=False)
                                )
                                yield {
                                    "type": "payload_offloaded",
                                    "payload_id": pid,
                                    "tool_name": block.name,
                                    "size_chars": len(raw_str),
                                }
                            else:
                                # Petit payload : pass-through avec scrub
                                # anti-injection. Filet de sécurité
                                # ``_truncate_tool_result`` en cas de
                                # payload juste sous le seuil mais
                                # malicieusement gros.
                                content_str = _neutralize_injection_attempts(
                                    _truncate_tool_result(raw_str)
                                )
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
                                exc_info=True,
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

                # Append **atomique** : soit les deux messages sont dans le
                # state, soit aucun. Si le consumer ``break`` entre deux
                # yields plus haut, ``state.messages`` reste cohérent avec
                # le tour précédent — le prochain ``run_turn`` re-streamera
                # la même requête (cache Pappers absorbe les tool calls
                # redondants, ~0 crédit).
                state.messages.append(assistant_msg)
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


# Borne de sécurité sur la taille du tool_result passé à Claude.
#
# Depuis S09.5, **le chemin nominal pour les payloads volumineux** est
# le Payload Vault (offload conditionnel à ``OFFLOAD_THRESHOLD_CHARS =
# 12 000``, cf. ``payload_vault.py``). ``_truncate_tool_result`` reste
# branché comme **filet de sécurité** sur les payloads juste sous le
# seuil d'offload (12 K - 24 K) pour borner le worst-case si un futur
# tool MCP ne passe pas par l'offload (par ex. parce que l'extraction
# texte échoue ou qu'on désactive le vault).
#
# Borne **doublée** de 16 000 → 24 000 chars : avec l'offload qui prend
# le relais au-dessus de 12 K, on a marge confortable et on évite de
# couper artificiellement un payload de 13-23 K qui aurait pu passer
# tel quel au LLM.
_TOOL_RESULT_MAX_CHARS = 24_000

# Fenêtre de recherche pour couper sur un délimiteur propre (newline,
# virgule, espace) plutôt qu'au milieu d'un token. 512 chars = 0.2 % de la
# borne, négligeable en perte d'info mais évite les JSON tronqués
# n'importe où qui égarent Claude.
_TOOL_RESULT_CLEAN_CUT_WINDOW = 512


def _truncate_tool_result(raw: str) -> str:
    """Tronque à ``_TOOL_RESULT_MAX_CHARS`` en cherchant un délimiteur
    propre (``\\n``, ``,``, espace) dans la fenêtre de fin pour éviter
    de couper un JSON au milieu d'une clé (sinon Claude peut halluciner
    en "recomplétant" la structure — cf. review S03 A6).

    **Filet de sécurité S09.5** : le chemin nominal pour > 12 K passe
    par le Payload Vault (cf. ``payload_vault.py`` + dispatch dans
    ``run_turn``). Cette fonction ne devrait quasi jamais être hit en
    prod — elle reste comme garantie qu'aucun payload ne dépasse jamais
    24 K dans l'historique conversation, peu importe le bug en amont.
    """
    if len(raw) <= _TOOL_RESULT_MAX_CHARS:
        return raw
    marker = "\n…[tronqué : réponse Pappers dépasse la borne agent]"
    cutoff = _TOOL_RESULT_MAX_CHARS - len(marker)
    window_start = max(0, cutoff - _TOOL_RESULT_CLEAN_CUT_WINDOW)
    window = raw[window_start:cutoff]
    # Préférence : dernier newline > dernière virgule > dernier espace.
    for sep in ("\n", ",", " "):
        idx = window.rfind(sep)
        if idx > 0:
            cutoff = window_start + idx
            break
    return raw[:cutoff] + marker


def _extract_text_from_result(result: dict[str, Any]) -> str:
    """Extrait le texte brut du ``CallToolResult`` sérialisé par S02
    **sans** truncation. La décision offload-ou-pass-through est prise
    en aval (cf. ``run_turn`` S09.5).

    S02 retourne ``result.model_dump(mode="json")`` — dict avec
    ``content: [{"type": "text", "text": "..."}]`` en général. On sort
    le premier bloc texte ou on fallback sur ``json.dumps`` du payload
    complet.
    """
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype is not None and btype != "text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                return text
    return json.dumps(result, ensure_ascii=False)


def _stringify_tool_result(result: dict[str, Any]) -> str:
    """Compat S03 : extraction + truncation immédiate à la borne S03.

    Conservé pour les call-sites historiques (tests S03) qui veulent
    encore le combo extract + truncate. Le chemin nominal S09.5 dans
    ``run_turn`` utilise ``_extract_text_from_result`` puis décide de
    l'offload — il n'appelle plus cette fonction.
    """
    return _truncate_tool_result(_extract_text_from_result(result))
