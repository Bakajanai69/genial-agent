"""Voice mode S10 — Eleven Agents (custom LLM SSE) + narration tool steps.

Sous-package isolé du pipeline brain (``agent.py``, ``routing.py``,
``guardrails/``, ``mcp_pappers.py``, ``payload_vault.py``) — voice est
une **couche I/O** branchée via ``mount_voice_routes()`` derrière le
flag ``settings.ENABLE_VOICE_MODE``. Si le flag est ``false``,
l'endpoint n'est même pas monté (route absente, surface d'attaque
nulle).

Modules :

- ``voice_prompt`` : suffixe voice-friendly injecté côté system prompt
  quand le custom LLM endpoint est appelé par ElevenLabs.
- ``narrate`` : mapping ≤ 6 entrées tool_name → phrase neutre FR
  émise pendant un tool_use pour donner du texte à TTS-er au widget.
- ``security`` : middleware Bearer timing-safe ``hmac.compare_digest``.
- ``openai_adapter`` : endpoint ``POST /v1/chat/completions`` SSE qui
  wrap ``run_guarded_turn``.
- ``mount`` : prepend de la route sur ``chainlit.server.app.router.routes``.
"""

from __future__ import annotations
