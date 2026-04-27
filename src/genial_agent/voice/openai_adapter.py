"""Adapter OpenAI Chat Completions SSE → ``run_guarded_turn``.

Endpoint cible : ``POST /v1/chat/completions``. Format de body et de
réponse : OpenAI Chat Completions API streaming, tel que ElevenLabs
Eleven Agents l'attend pour un custom LLM (cf. story S10
§"Décisions phase 1" point 1).

Format de chunks SSE :

    data: {"id": "...", "object": "chat.completion.chunk",
           "created": 1234, "model": "...",
           "choices": [{"delta": {"content": "..."}, "index": 0}]}\\n\\n
    ...
    data: {"choices": [{"finish_reason": "stop", "index": 0, "delta": {}}]}\\n\\n
    data: [DONE]\\n\\n

Principes :

- L'agent reste **100 % inchangé** côté logique (tools MCP, vault,
  caps, routing Haiku/Sonnet). On ne fait que wrapper.
- Sur chaque event ``tool_use`` reçu, on émet un chunk de **narration**
  via ``narrate.narrate(tool_name)`` pour que ElevenLabs ait du texte
  à TTS-er pendant que Pappers répond (mitigation latence U3).
- Sur les events ``text``, on forward le ``content`` verbatim.
- Sur ``ClientDisconnect`` (interruption user, navigateur fermé) ou
  ``asyncio.CancelledError``, on ferme proprement le generator
  ``run_guarded_turn`` (libère ``state.lock`` — pattern S03 + S09.7).

Les ``tools`` natifs Eleven (``end_call``, ``language_detection``,
``transfer_to_agent``, etc.) sont **ignorés** : on ne les renvoie pas en
``tool_calls`` SSE et on ne les dispatch pas côté Anthropic.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from genial_agent.agent import ConversationState
from genial_agent.guardrails import run_guarded_turn
from genial_agent.observability.stats import incr as stats_incr
from genial_agent.voice.narrate import narrate
from genial_agent.voice.reformulator import (
    reformulate_for_voice_stream,
    strip_markdown_for_tts,
)
from genial_agent.voice.security import verify_eleven_request
from genial_agent.voice.voice_prompt import compose_voice_system_prompt

logger = structlog.get_logger(__name__)

# Eleven attend du SSE (text/event-stream). On ajoute les headers
# anti-buffering proxy (cf. doc nginx) — Railway / Cloudflare peuvent
# autrement coalescer des chunks et casser l'effet duplex.
_SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",  # nginx proxy off
    "Connection": "keep-alive",
}

# Default si ``messages[-1]`` n'est pas un user (cas pathologique).
_FALLBACK_USER_MSG = ""


def _convert_history_to_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Convertit l'historique OpenAI en historique Anthropic + extrait
    le dernier user message (à passer en ``user_message`` de
    ``run_guarded_turn``).

    Règles :

    - ``role=system`` → ignoré (on a notre system prompt côté agent
      via ``compose_voice_system_prompt()``).
    - ``role=user`` / ``role=assistant`` → passés tels quels au format
      Anthropic ``MessageParam``. Le ``content`` peut être string ou
      list of blocks ; on normalise en string pour cet adapter (le
      voice mode ne fait jamais de tool_use côté state — l'agent
      re-fait ses tool calls Pappers à chaque appel ElevenLabs, le
      cache MCP absorbe le coût).
    - Le **dernier** user message est extrait et retourné séparément :
      il sera passé à ``run_guarded_turn(user_message=...)`` qui
      l'appendra avec ``wrap_user_input`` (input gate + anti-injection).

    Returns:
        ``(history_for_state, last_user_text)``. ``history_for_state``
        est destiné à ``ConversationState.messages`` ; ``last_user_text``
        au paramètre ``user_message`` de ``run_guarded_turn``.
    """
    history: list[dict[str, Any]] = []
    last_user_text = _FALLBACK_USER_MSG

    # Trouver l'index du dernier message ``role=user``.
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content", "")
        if not isinstance(content, str):
            # ``content`` peut être une list of blocks côté OpenAI vision.
            # On stringify best-effort — voice mode est en texte pur.
            content = json.dumps(content, ensure_ascii=False)

        if role == "system":
            # Ignoré : notre system prompt voice-friendly est injecté
            # via ``compose_voice_system_prompt()`` au niveau pipeline.
            continue

        if last_user_idx is not None and i == last_user_idx:
            # Dernier user → passé à run_guarded_turn, pas dans state.
            last_user_text = content
            continue

        if role in ("user", "assistant"):
            history.append({"role": role, "content": content})

    return history, last_user_text


def _sse_chunk(
    content: str,
    *,
    chunk_id: str,
    model_label: str,
    role: str | None = None,
    finish_reason: str | None = None,
) -> str:
    """Sérialise un chunk SSE OpenAI Chat Completions.

    ``role`` n'est posé que sur le premier chunk (convention OpenAI :
    ``delta.role = "assistant"`` puis chunks suivants n'ont que
    ``delta.content``).
    """
    delta: dict[str, Any] = {}
    if role is not None:
        delta["role"] = role
    if content:
        delta["content"] = content

    payload: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_label,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


async def _stream_chat_completion(
    body: dict[str, Any],
    request: Request,
) -> AsyncIterator[str]:
    """Pipeline voice mode 2-passes vers ElevenLabs (custom LLM SSE).

    **Pass 1 (main LLM)** : ``run_guarded_turn`` est exécuté avec le
    voice system prompt comme override. On **buffer** les events
    ``text`` au lieu de les yielder en SSE direct. La narration tool
    steps (``Je consulte les comptes…``) est en revanche émise LIVE
    pendant les ``tool_use`` events pour ne pas avoir de silence
    pendant les appels Pappers.

    **Pass 2 (reformulateur Haiku)** : à la fin du main turn (event
    ``routing_done``), on prend le buffer et on le donne à
    ``reformulate_for_voice_stream`` (Haiku) qui génère une version
    voice-friendly (no Markdown, no SIREN, chiffres arrondis, narratif
    fluide ~80-100 mots). On stream les chunks reformulés en SSE.

    En sortie : narration LIVE (pendant Pass 1) + texte reformulé
    (Pass 2). Le main LLM peut produire du texte structuré (Markdown,
    SIREN) sans casser l'expérience vocale — le reformulateur le
    nettoie systématiquement avant TTS.

    **Order [DONE] avant aclose()** (S10 hotfix C) : on yield le chunk
    final + ``[DONE]`` AVANT le cleanup ``turn_gen.aclose()``. Le
    cleanup attend le critic_async (10 s timeout), ce qui ajoutait
    ~1 s de silence avant que le widget Eleven libère le TTS. Avec
    cette inversion, ``[DONE]`` arrive immédiatement après le dernier
    chunk reformulateur.

    **Strip Markdown safety net** (D) : chaque chunk reformulateur
    passe par ``strip_markdown_for_tts`` avant d'être envoyé en SSE,
    pour éliminer un éventuel ``**``, ``##`` ou emoji que le
    reformulateur aurait laissé passer malgré la consigne.

    Capture ``asyncio.CancelledError`` (déconnexion client / interruption
    user côté ElevenLabs) → libère le generator ``run_guarded_turn``
    proprement (gen.aclose() libère ``state.lock`` — pattern S03 +
    S09.7 invariant I5).
    """
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    # Le model label retourné dans les chunks n'est pas critique côté
    # Eleven (il sert de log) ; on rend explicite que c'est notre
    # custom LLM brain.
    model_label = body.get("model") or "genial-agent-claude"

    raw_messages = body.get("messages") or []
    history, last_user_text = _convert_history_to_anthropic(raw_messages)

    # session_id pour le token budget : si Eleven envoie un user_id
    # stable (champ ``user`` standard OpenAI), on l'utilise ; sinon un
    # UUID éphémère (chaque appel = nouveau budget).
    session_id = str(body.get("user") or f"voice-{uuid.uuid4().hex}")

    # State éphémère par appel — cohérent avec la philosophie
    # "session voice = échange éphémère côté UI" (story §"Compatibilité
    # Chainlit data layer"). L'historique conversationnel est
    # reconstruit depuis les messages OpenAI à chaque tour ; les tool
    # calls Pappers sont re-faits (cache MCP absorbe le coût).
    state = ConversationState()
    state.messages = list(history)

    # Stats : voice session démarrée + appel custom LLM compté.
    stats_incr(voice_sessions_total=1, voice_custom_llm_calls=1)

    voice_system_prompt = compose_voice_system_prompt()

    logger.info(
        "voice_chat_completion_started",
        session_id=session_id,
        history_messages=len(history),
        last_user_chars=len(last_user_text),
        model_label=model_label,
    )

    # Premier chunk : pose ``delta.role = "assistant"`` (convention OpenAI).
    yield _sse_chunk("", chunk_id=chunk_id, model_label=model_label, role="assistant")

    turn_gen = run_guarded_turn(
        state,
        last_user_text,
        session_id,
        system_prompt_override=voice_system_prompt,
    )
    cancelled = False
    rejected = False
    main_text_buffer: list[str] = []
    narration_chars = 0
    reformulated_chars = 0
    done_yielded = False

    try:
        # ---------------------------------------------------------- #
        # Pass 1 : main LLM + narration tool steps LIVE
        # ---------------------------------------------------------- #
        try:
            async for event in turn_gen:
                # Surveille l'état du client : si Eleven a fermé la
                # connexion (interruption user, fin de session vocale,
                # navigateur fermé), on stoppe proprement.
                if await request.is_disconnected():
                    cancelled = True
                    logger.info("voice_chat_completion_client_disconnected", session_id=session_id)
                    break

                etype = event.get("type")

                if etype == "text":
                    content = event.get("content") or ""
                    if content:
                        # Buffer pour Pass 2, ne yield PAS direct.
                        main_text_buffer.append(content)

                elif etype == "tool_use":
                    # Narration LIVE (mitigation latence U3) — émise
                    # dans le SSE pendant que Pappers répond, donne du
                    # texte à TTS-er au widget pour ne pas avoir de
                    # silence. Phrase neutre, pas de hardcode entité.
                    tool_name = event.get("name") or ""
                    phrase = narrate(tool_name) + " "
                    narration_chars += len(phrase)
                    stats_incr(voice_narration_chunks_emitted=1)
                    yield _sse_chunk(
                        phrase,
                        chunk_id=chunk_id,
                        model_label=model_label,
                    )

                elif etype == "input_rejected":
                    # Input refusé par C1 input gate. Court-circuit :
                    # pas de reformulateur (rien à reformuler), on
                    # yield direct un message court.
                    phrase = "Je ne peux pas traiter cette demande. Reformule s'il te plaît."
                    narration_chars += len(phrase)
                    rejected = True
                    yield _sse_chunk(
                        phrase,
                        chunk_id=chunk_id,
                        model_label=model_label,
                    )
                    break

                elif etype == "capped":
                    # Cap atteint en cours de turn. Annonce brève à
                    # l'oral (le user pourra dire "continue").
                    phrase = " (Pause sur le cap.) "
                    narration_chars += len(phrase)
                    yield _sse_chunk(
                        phrase,
                        chunk_id=chunk_id,
                        model_label=model_label,
                    )

                # Les autres events (llm_meta, tool_result, routing_*,
                # critic_*, validator_degraded, payload_*) sont ignorés
                # côté SSE — ils sont visibles via les compteurs /stats.

        except asyncio.CancelledError:
            cancelled = True
            logger.info("voice_chat_completion_cancelled", session_id=session_id)
        except Exception:  # noqa: BLE001 — on logue + clôture propre
            logger.exception("voice_chat_completion_main_error", session_id=session_id)

        # ---------------------------------------------------------- #
        # Pass 2 : reformulateur Haiku → SSE chunks voice-friendly
        # ---------------------------------------------------------- #
        if not cancelled and not rejected and main_text_buffer:
            full_main_text = "".join(main_text_buffer)
            logger.info(
                "voice_reformulator_start",
                session_id=session_id,
                main_chars=len(full_main_text),
            )
            try:
                async for delta in reformulate_for_voice_stream(last_user_text, full_main_text):
                    # Vérifie disconnect aussi pendant la reformulation
                    # (l'utilisateur peut interrompre pendant la voix).
                    if await request.is_disconnected():
                        cancelled = True
                        logger.info(
                            "voice_reformulator_client_disconnected",
                            session_id=session_id,
                        )
                        break
                    clean = strip_markdown_for_tts(delta)
                    if clean:
                        reformulated_chars += len(clean)
                        yield _sse_chunk(
                            clean,
                            chunk_id=chunk_id,
                            model_label=model_label,
                        )
            except Exception:  # noqa: BLE001 — fallback sur main text strippé
                logger.exception(
                    "voice_reformulator_failed_fallback_to_main",
                    session_id=session_id,
                )
                # Fallback : yield le main text strippé Markdown (mieux
                # que rien). L'utilisateur entendra une version moins
                # voice-friendly, mais aura quand même du contenu.
                fallback = strip_markdown_for_tts(full_main_text)
                if fallback:
                    reformulated_chars += len(fallback)
                    yield _sse_chunk(
                        fallback,
                        chunk_id=chunk_id,
                        model_label=model_label,
                    )

        # ---------------------------------------------------------- #
        # Final SSE chunks AVANT aclose() (hotfix C : pause finale 1.2s)
        # ---------------------------------------------------------- #
        # Le aclose() qui suit dans le finally peut traîner ~1 s sur le
        # critic_async du pipeline. En émettant [DONE] AVANT, le widget
        # Eleven libère le TTS immédiatement après le dernier chunk de
        # contenu — pas de silence final perceptible.
        yield _sse_chunk(
            "",
            chunk_id=chunk_id,
            model_label=model_label,
            finish_reason="stop",
        )
        yield _sse_done()
        done_yielded = True

    finally:
        # Yield [DONE] aussi en finally au cas où une exception nous
        # aurait empêché de l'émettre dans le bloc try (idempotent côté
        # client : un 2e [DONE] est ignoré).
        if not done_yielded:
            try:
                yield _sse_done()
            except Exception as final_exc:  # noqa: BLE001 — client parti, OK
                logger.debug(
                    "voice_chat_completion_done_dropped",
                    session_id=session_id,
                    error_type=type(final_exc).__name__,
                )

        # PEP 789 + S03 invariant I5 : aclose() force le cleanup du
        # ``async with state.lock`` dans agent.run_turn même si on
        # break/raise au milieu. Best-effort APRÈS [DONE] (cf. hotfix C).
        try:
            await turn_gen.aclose()
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.warning(
                "voice_chat_completion_aclose_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )

        if cancelled:
            stats_incr(voice_cancelled_total=1)
        total_tts_chars = narration_chars + reformulated_chars
        if total_tts_chars > 0:
            stats_incr(voice_chars_tts=total_tts_chars)

        logger.info(
            "voice_chat_completion_done",
            session_id=session_id,
            cancelled=cancelled,
            rejected=rejected,
            main_text_chars=sum(len(c) for c in main_text_buffer),
            narration_chars=narration_chars,
            reformulated_chars=reformulated_chars,
        )


async def chat_completions(request: Request) -> StreamingResponse | JSONResponse:
    """Handler Starlette pour ``POST /v1/chat/completions``.

    1. Vérifie ``Authorization: Bearer <ELEVEN_AGENT_SHARED_TOKEN>``
       via ``verify_eleven_request`` (timing-safe ``hmac.compare_digest``).
    2. Parse le body JSON (OpenAI Chat Completions schema, simplifié).
    3. Lance ``_stream_chat_completion`` en SSE.
    """
    auth_response = verify_eleven_request(request)
    if auth_response is not None:
        return auth_response

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — body invalide → 400
        logger.warning("voice_chat_completion_bad_body")
        return JSONResponse(
            {"error": {"message": "invalid json body", "type": "invalid_request_error"}},
            status_code=400,
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"error": {"message": "body must be a json object", "type": "invalid_request_error"}},
            status_code=400,
        )

    # ``stream`` doit être ``true`` côté Eleven (custom LLM = SSE).
    # On n'expose **pas** de mode non-streaming pour cet endpoint :
    # la simplicité est un signal "j'évite les chemins non testés".
    if not body.get("stream", False):
        return JSONResponse(
            {"error": {"message": "stream=true required", "type": "invalid_request_error"}},
            status_code=400,
        )

    return StreamingResponse(
        _stream_chat_completion(body, request),
        headers=_SSE_HEADERS,
        media_type="text/event-stream",
    )
