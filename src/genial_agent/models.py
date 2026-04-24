"""Constantes de modèles et types tier.

Model IDs pinnés après probe API 2026-04-24 et cross-check docs
officielles (claude.com/docs/models/overview) :

- Sonnet 4.6 : alias ``claude-sonnet-4-6``, 1M ctx / 64k max output,
  ``inference_geo=global`` par défaut.
- Haiku 4.5 : alias ``claude-haiku-4-5`` résout vers le snapshot
  ``claude-haiku-4-5-20251001``. On pin directement le snapshot daté
  pour reproductibilité entre runs.

Valeurs par défaut agent (``DEFAULT_MAX_TOKENS``,
``DEFAULT_TEMPERATURE``, ``DEFAULT_INFERENCE_GEO``) partagées par S03
(``run_turn``) et consommables par S04 (routing) sans ré-hardcode.
"""

from __future__ import annotations

from enum import StrEnum

MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"

# Defaults agent
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.2
DEFAULT_INFERENCE_GEO = "global"

# Filet anti-boucle-infinie. Le cap produit (5 tool calls, cahier §5.3)
# est consommé par S05 — au-delà S05 coupe. Ici on borne à 12 pour
# éviter une boucle pathologique si S05 est absent ou bypassé.
MAX_ITERATIONS = 12


class ModelTier(StrEnum):
    HAIKU = "haiku"
    SONNET = "sonnet"


def model_id(tier: ModelTier) -> str:
    """Résout un ``ModelTier`` vers l'ID modèle Anthropic à passer au SDK.

    Unique point de résolution (cf. review S03 check-list) : aucun
    consommateur (``agent.py``, ``routing.py`` S04…) ne doit dupliquer
    la correspondance.
    """
    return {ModelTier.HAIKU: MODEL_HAIKU, ModelTier.SONNET: MODEL_SONNET}[tier]
