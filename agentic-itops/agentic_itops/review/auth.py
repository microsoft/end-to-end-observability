"""Signature-verified Entra authentication; no platform principal/header bypass."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import UUID

import jwt

from .contracts import ReviewError


ROLES = frozenset({"Viewer", "Analyst", "Reviewer", "KnowledgeReviewer", "Operator"})


@dataclass(frozen=True)
class Principal:
    oid: str
    roles: frozenset[str]
    method: str = "bearer"


class EntraAuthenticator:
    """key_resolver(token) is injectable; default uses the fixed tenant JWKS URL.

    EasyAuth's ID token is accepted only with allow_easyauth=True and still
    undergoes exactly the same validation as an Authorization bearer token.
    """

    def __init__(self, tenant_id: str, audience: str, *, key_resolver: Callable | None = None,
                 allow_easyauth: bool = False):
        try:
            self.tenant_id = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("explicit Entra tenant UUID required") from exc
        if not isinstance(audience, str) or not audience.strip():
            raise ValueError("explicit Entra audience required")
        self.audience = audience
        self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        self.allow_easyauth = allow_easyauth
        self.jwks = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys",
            timeout=5,
        ) if key_resolver is None else None
        self.key_resolver = key_resolver or (lambda token: self.jwks.get_signing_key_from_jwt(token).key)

    def authenticate(self, request) -> Principal:
        authorization = request.headers.get("Authorization")
        method = "bearer"
        if authorization is not None:
            parts = authorization.split()
            if len(parts) != 2 or parts[0].casefold() != "bearer":
                raise ReviewError("authentication required", 401)
            token = parts[1]
        elif self.allow_easyauth:
            token = request.headers.get("X-MS-TOKEN-AAD-ID-TOKEN")
            method = "easyauth"
        else:
            token = None
        if not token or len(token) > 16384:
            raise ReviewError("authentication required", 401)
        try:
            if jwt.get_unverified_header(token).get("alg") != "RS256":
                raise ValueError("algorithm")
            claims = jwt.decode(
                token, self.key_resolver(token), algorithms=["RS256"],
                audience=self.audience, issuer=self.issuer, leeway=0,
                options={"require": ["exp", "nbf", "iat", "iss", "aud", "tid", "oid"],
                         "strict_aud": True},
            )
            if claims["tid"] != self.tenant_id:
                raise ValueError("tenant")
            oid = str(UUID(claims["oid"]))
            roles = claims.get("roles", [])
            if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                raise ValueError("roles")
        except Exception as exc:
            # Neither JWT contents nor dependency errors may leak into a response.
            raise ReviewError("invalid authentication", 401) from exc
        return Principal(oid, frozenset(roles) & ROLES, method)
