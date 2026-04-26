"""Tests S09.6 (review P1-1) — scrubbing de PAPPERS_API_KEY dans
``scripts/prewarm_comptes_entreprise.get_balance``.

Avant le fix, ``r.raise_for_status()`` levait ``HTTPStatusError`` dont
le ``__str__`` inclut l'URL complète (``?api_token=<KEY>``). Toute
remontée d'exception au terminal / aux logs CI = fuite de la clé.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "prewarm_comptes_entreprise.py"


def _load_get_balance():
    """Importe `get_balance` du script standalone — pas de package."""
    spec = importlib.util.spec_from_file_location("_prewarm_for_unit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.get_balance


def test_get_balance_scrubs_key_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si Pappers répond 401/403/500, l'exception remontée ne doit PAS
    contenir la clé API."""
    secret_key = "fake-secret-key-12345-do-not-leak"
    monkeypatch.setenv("PAPPERS_API_KEY", secret_key)

    def _fake_get(url, timeout=15):
        # Simule une réponse Pappers 403.
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request)
        # Le response.url contient la clé — c'est ce qui leak via l'exc.
        raise httpx.HTTPStatusError(
            f"403 Forbidden for url {url}",
            request=request,
            response=response,
        )

    monkeypatch.setattr(httpx, "get", _fake_get)

    get_balance = _load_get_balance()
    with pytest.raises(RuntimeError) as exc_info:
        get_balance()

    # Le message d'erreur résultant ne doit JAMAIS contenir la clé.
    assert secret_key not in str(exc_info.value)
    # Mais doit contenir le code HTTP utile au debug.
    assert "403" in str(exc_info.value)


def test_get_balance_scrubs_key_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Variante transport : timeout / DNS fail → RuntimeError sans clé."""
    secret_key = "fake-secret-key-67890-do-not-leak"
    monkeypatch.setenv("PAPPERS_API_KEY", secret_key)

    def _fake_get(url, timeout=15):
        # ConnectError peut aussi inclure l'URL dans certaines versions httpx.
        raise httpx.ConnectTimeout(f"timeout connecting to {url}")

    monkeypatch.setattr(httpx, "get", _fake_get)

    get_balance = _load_get_balance()
    with pytest.raises(RuntimeError) as exc_info:
        get_balance()

    assert secret_key not in str(exc_info.value)
    # On garde au moins le type d'erreur.
    assert "ConnectTimeout" in str(exc_info.value)


# NB : on ne teste PAS le retour None sans clé : le module fait
# ``load_dotenv(ROOT / ".env")`` à l'import, ce qui repopule
# ``PAPPERS_API_KEY`` depuis le .env du disque même après ``delenv``.
# Le comportement "no key → None" est trivial (1 ligne) et déjà couvert
# par la lecture de code.
