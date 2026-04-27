"""Bearer token middleware pour ``/v1/chat/completions``.

ElevenLabs envoie ``Authorization: Bearer <secret>`` (configuré côté
Workspace Secrets ElevenLabs + custom LLM agent settings). On le compare
en **timing-safe** via ``hmac.compare_digest`` au token attendu côté
``settings.ELEVEN_AGENT_SHARED_TOKEN``.

Sécurité :

- **Aucun log de la valeur reçue**. On loggue uniquement
  ``auth_attempt_rejected`` avec un hash tronqué ``sha256(token)[:8]``
  pour traçabilité (audit), jamais le token lui-même.
- **Pas d'IP allowlist** : ElevenLabs ne publie pas de plage stable.
- **Défense en profondeur** : si ``settings.ENABLE_VOICE_MODE`` est
  ``false``, l'endpoint n'est même pas monté côté ``mount.py`` — la
  surface d'attaque est nulle même en cas de fuite du secret.
"""

from __future__ import annotations

import hashlib
import hmac

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse

from genial_agent.config import settings

logger = structlog.get_logger(__name__)

# Réponse 401 standard. Pas de WWW-Authenticate header (clients
# automated only — pas de browser challenge à déclencher). Body neutre
# pour ne pas leaker d'info au scanner.
_UNAUTHORIZED_BODY = {"error": {"message": "unauthorized", "type": "auth_error"}}


def _hash_prefix(token: str) -> str:
    """Hash tronqué pour audit, jamais réversible vers la valeur reçue."""
    return hashlib.sha256(token.encode("utf-8", errors="replace")).hexdigest()[:8]


def verify_eleven_request(request: Request) -> JSONResponse | None:
    """Valide ``Authorization: Bearer <token>`` en timing-safe.

    Returns:
        ``None`` si le token est valide → l'appelant peut continuer.
        ``JSONResponse(401)`` si absent ou bidon → l'appelant doit
        retourner cette réponse au client.

    Le pattern "return-Response-or-None" est volontaire : il évite de
    lever une exception dans un async generator SSE (qui complique la
    gestion ``try/finally`` côté ``openai_adapter``).
    """
    expected = settings.ELEVEN_AGENT_SHARED_TOKEN
    auth_header = request.headers.get("authorization", "") or request.headers.get(
        "Authorization", ""
    )

    if not expected:
        # Configuration manquante côté serveur — refuse tout par défaut
        # (jamais d'open endpoint). On loggue en ``error`` car c'est une
        # mauvaise config, pas une attaque.
        logger.error("voice_auth_no_shared_token_configured")
        return JSONResponse(_UNAUTHORIZED_BODY, status_code=401)

    if not auth_header.startswith("Bearer "):
        logger.info("voice_auth_attempt_rejected", reason="missing_or_malformed_header")
        return JSONResponse(_UNAUTHORIZED_BODY, status_code=401)

    received_token = auth_header[len("Bearer ") :].strip()
    if not received_token:
        logger.info("voice_auth_attempt_rejected", reason="empty_token")
        return JSONResponse(_UNAUTHORIZED_BODY, status_code=401)

    # Comparaison timing-safe — empêche les timing attacks qui pourraient
    # déduire le secret octet par octet via la latence de comparaison.
    if not hmac.compare_digest(
        received_token.encode("utf-8"),
        expected.encode("utf-8"),
    ):
        # Hash tronqué pour audit — jamais la valeur reçue.
        logger.warning(
            "voice_auth_attempt_rejected",
            reason="token_mismatch",
            received_hash_prefix=_hash_prefix(received_token),
        )
        return JSONResponse(_UNAUTHORIZED_BODY, status_code=401)

    return None
