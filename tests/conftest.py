"""Fixtures partagées — isolation des singletons S02.

Deux fixtures ``autouse`` garantissent qu'aucun test ne contamine l'état
global :

- ``_restore_settings`` : snapshot/restore du dataclass frozen
  ``mcp_pappers.settings``. Les tests qui veulent manipuler la clé API
  utilisent ``monkeypatch.setattr(mcp_pappers, "settings", replace(...))``.
- ``_fresh_cache`` : remplace le singleton ``mcp_pappers.cache`` par un
  ``ToolCache`` neuf avant chaque test, et réinitialise la mémoïsation
  de ``_is_degraded`` (cf. review S02 C4).

Combinées, elles adressent les blockers B1 (pollution ``settings``) et
R2 (fuite d'état cache) identifiés en revue S02.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def _restore_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Réinstalle le ``settings`` d'origine à la fin de chaque test.

    ``mcp_pappers.settings`` est un ``@dataclass(frozen=True)`` — on ne
    peut pas le muter directement. Les tests doivent passer par
    ``monkeypatch.setattr(mcp_pappers, "settings",
    dataclasses.replace(original, PAPPERS_API_KEY="..."))`` ; cette
    fixture assure simplement que rien de bricolé ne survit au test.
    """
    from genial_agent import mcp_pappers
    from genial_agent.config import settings as original

    # Monkeypatch réinstalle automatiquement la valeur originelle à la
    # fin du test via setattr — il suffit de lui en donner l'occasion.
    monkeypatch.setattr(mcp_pappers, "settings", replace(original))
    yield


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Swap ``mcp_pappers.cache`` with a brand-new ``ToolCache`` per test.

    Empêche la fuite d'état entre tests (cf. review S02 R2). Réinit
    aussi la mémoïsation ``_is_degraded`` (review C4) pour qu'un test
    qui force le mode dégradé ne le laisse pas collé pour le suivant.
    """
    from genial_agent import mcp_pappers
    from genial_agent.mcp_cache import ToolCache

    fresh = ToolCache()
    monkeypatch.setattr(mcp_pappers, "cache", fresh)
    mcp_pappers._reset_degraded_cache()
    yield
    mcp_pappers._reset_degraded_cache()
