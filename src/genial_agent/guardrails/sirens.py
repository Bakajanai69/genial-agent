"""Helpers SIREN : validation Luhn (INSEE) + extraction depuis texte libre.

Mutualisé entre ``output_validator`` (détection orphelins) et
``pipeline`` (agrégation des ``allowed_sirens`` depuis les tool_results).

Décision phase 1 S05 : filtrer par Luhn réduit drastiquement les faux
positifs (un nombre aléatoire de 9 chiffres a ~10 % de chances de
passer Luhn, contre 100 % pour un bête ``\\d{9}``). Cf. cahier §R11
fiabilité.
"""

from __future__ import annotations

import re

SIREN_LENGTH = 9
SIREN_RE = re.compile(r"\b(\d{9})\b")


def valid_siren(s: str) -> bool:
    """Vérifie la clef Luhn d'un SIREN (9 chiffres, formule INSEE).

    Algorithme :
    - De droite à gauche, on double un chiffre sur deux (positions
      paires en partant de la droite → index impairs en Python après
      ``reversed``).
    - Si le chiffre doublé > 9, on soustrait 9 (équivaut à sommer les
      chiffres de la multiplication).
    - Si la somme totale est divisible par 10 ⇒ SIREN valide.

    Exemples :
        >>> valid_siren("775670417")  # LVMH
        True
        >>> valid_siren("552032534")  # Accor
        True
        >>> valid_siren("999999999")
        False
        >>> valid_siren("12345678")
        False
    """
    if len(s) != SIREN_LENGTH or not s.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def extract_sirens(text: str, *, luhn_only: bool = True) -> set[str]:
    """Extrait les SIREN candidats d'un texte libre.

    Args:
        text: texte brut (réponse agent, content_preview de tool_result…).
        luhn_only: si ``True`` (défaut), ne retient que les séquences
            9-chiffres qui passent la clef Luhn — réduit les faux
            positifs sur des nombres non-SIREN (effectifs, prix, etc.).

    Returns:
        Set des SIREN uniques trouvés (chaînes 9-chiffres).
    """
    candidates = set(SIREN_RE.findall(text))
    if not luhn_only:
        return candidates
    return {s for s in candidates if valid_siren(s)}
