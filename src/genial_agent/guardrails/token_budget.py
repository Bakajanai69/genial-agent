"""Tracker de tokens consommés par session (cahier §14.3 C4).

Singleton module-level ``budget`` indexé par ``session_id``. Alimenté
par le pipeline (``guardrails/pipeline.py``) à chaque ``llm_meta`` event
yieldé par S03 via S04.

**Règle architecture** : ne **jamais** modifier ``agent.py`` / ``routing.py``
pour câbler le budget — le pipeline intercepte les events et met à jour
le budget. Décision phase 1 : garder S03 / S04 agnostiques du budget.

**Isolation tests** : ``tests/conftest.py`` expose une fixture
``_fresh_budget`` autouse qui swap le singleton pour éviter la fuite
d'état entre tests (même pattern que ``_fresh_cache`` de S02). Le
pipeline importe ``budget`` avec un ``from … import budget`` (bind
local) → la fixture patche **aussi** ``pipeline.budget`` pour que la
substitution soit visible dans le code testé.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from genial_agent.guardrails.caps import MAX_TOKENS_PER_SESSION

REASON_CODE_CAP_TOKEN_BUDGET = "cap_token_budget"  # noqa: S105 — enum reason_code, pas un secret


class TokenBudget:
    """Cap cumulatif par session, thread-safe via ``asyncio.Lock``.

    - ``add(session, in_tok, out_tok)`` : ajoute un delta (in+out).
    - ``exhausted(session)`` : True si la session a atteint le cap.
    - ``remaining(session)`` / ``used(session)`` : inspection.
    - ``reset(session)`` : explicit reset (utile quand Chainlit
      recycle une session, ou en post-traitement de démo).

    Pas de reset auto entre turns — le budget est cumulatif par session.
    """

    def __init__(self, cap: int = MAX_TOKENS_PER_SESSION) -> None:
        self._cap = cap
        self._used: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    @property
    def cap(self) -> int:
        return self._cap

    async def add(self, session_id: str, input_tokens: int, output_tokens: int) -> None:
        async with self._lock:
            self._used[session_id] += input_tokens + output_tokens

    async def used(self, session_id: str) -> int:
        async with self._lock:
            return self._used[session_id]

    async def remaining(self, session_id: str) -> int:
        async with self._lock:
            return max(0, self._cap - self._used[session_id])

    async def exhausted(self, session_id: str) -> bool:
        async with self._lock:
            return self._used[session_id] >= self._cap

    async def reset(self, session_id: str) -> None:
        async with self._lock:
            self._used.pop(session_id, None)


# Singleton module-level — swap en test via ``_fresh_budget`` autouse.
budget = TokenBudget()
