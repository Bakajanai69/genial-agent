"""Normalisation FR — mutualisé entre ``routing.py`` (S04) et ``input_gate.py`` (S05).

``normalize_fr`` strip les diacritiques (NFKD) puis met en minuscule,
pour un matching regex accent-insensible sans ``re.IGNORECASE``.

**Limite connue (héritée S04 phase 1)** : les ligatures qui n'ont pas de
décomposition NFKD canonique en ASCII sont **perdues** plutôt que
transcrites (ex : ``ß`` → drop, ``œ`` reste dans la sortie unicode mais
son ``c`` suivi du digraphe ``oe`` fusionné n'est pas reconstruit).
Acceptable : aucun pattern d'injection OWASP LLM01 ou d'advisory
financier n'en dépend.

S04 garde pour l'instant son alias privé ``_normalize_fr`` pour zéro
risque de régression (5 lignes dupliquées) — dédup prévue en S09 polish.
"""

from __future__ import annotations

import unicodedata


def normalize_fr(text: str) -> str:
    """Strip diacritiques + lowercase pour un match accent-insensible.

    Exemples :
        >>> normalize_fr("Évolution")
        'evolution'
        >>> normalize_fr("Société Générale")
        'societe generale'
    """
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = nfkd.encode("ascii", "ignore").decode("ascii")
    return stripped.lower()
