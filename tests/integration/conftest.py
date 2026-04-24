"""Marker gate pour les tests d'intégration.

Verrouille ``pytest.mark.integration`` sur **tous** les tests collectés
sous ``tests/integration/``, même si un fichier oublie le
``pytestmark = pytest.mark.integration`` en tête. Défense en profondeur
contre un ajout accidentel qui tournerait sous ``make test`` et
consommerait des crédits Anthropic / Pappers (cf. review S03 A8 +
``docs/stories/S03-agent-core.md`` §"Stratégie de tests").

Implémentation : ``pytest_collection_modifyitems`` reçoit **tous** les
items collectés par la session (y compris ``tests/unit/``) — on filtre
donc sur le chemin de l'item pour ne marquer que ceux de ce dossier,
sinon on taggerait accidentellement les unit tests et ``make test`` ne
sélectionnerait plus rien.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INTEGRATION_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Applique le marker ``integration`` uniquement aux items de ce dossier."""
    for item in items:
        try:
            path = Path(str(item.path)).resolve()
        except (AttributeError, OSError):
            continue
        if _INTEGRATION_ROOT.resolve() in path.parents:
            item.add_marker("integration")
