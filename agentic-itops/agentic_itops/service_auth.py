"""App-only Entra verification for private HTTP Function hosts."""
from __future__ import annotations

from uuid import UUID

import jwt

from .consumer.contracts import ContractError, strict_json
from .review.contracts import ReviewError


class ServiceAuthenticator:
    def __init__(self, tenant_id, audience, callers, *, key_resolver=None):
        self.tenant_id = str(UUID(tenant_id))
        if not isinstance(audience, str) or not audience.strip():
            raise ContractError("service audience must be explicit")
        self.audience = audience
        self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        if not isinstance(callers, list) or not callers:
            raise ContractError("explicit service-principal allowlist required")
        self.callers = {}
        for caller in callers:
            if not isinstance(caller, dict) or set(caller) != {"oid", "client_id", "roles"}:
                raise ContractError("invalid service-principal allowlist contract")
            roles = caller["roles"]
            if (not isinstance(roles, list) or not roles or len(roles) != len(set(roles))
                    or not set(roles).issubset({"Worker.Invoke", "Tools.Invoke", "Publisher.Invoke", "Results.Project"})):
                raise ContractError("service roles must be explicit known application roles")
            key = (str(UUID(caller["oid"])), str(UUID(caller["client_id"])))
            if key in self.callers or any(UUID(item).int == 0 for item in key):
                raise ContractError("service caller UUID must be unique and non-placeholder")
            self.callers[key] = frozenset(roles)
        jwks = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys", timeout=5)
        self.key_resolver = key_resolver or (lambda token: jwks.get_signing_key_from_jwt(token).key)

    def authenticate(self, headers, required_role):
        authorization = headers.get("Authorization", "")
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 16384:
            raise ReviewError("service authentication required", 401)
        token = parts[1]
        try:
            if jwt.get_unverified_header(token).get("alg") != "RS256":
                raise ValueError("algorithm")
            claims = jwt.decode(
                token, self.key_resolver(token), algorithms=["RS256"],
                audience=self.audience, issuer=self.issuer, leeway=0,
                options={"require": ["exp", "nbf", "iat", "iss", "aud", "tid", "oid", "azp", "idtyp", "roles"],
                         "strict_aud": True})
            if claims["tid"] != self.tenant_id or claims["idtyp"] != "app" or "scp" in claims:
                raise ValueError("app-only token required")
            key = (str(UUID(claims["oid"])), str(UUID(claims["azp"])))
            roles = claims["roles"]
            if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                raise ValueError("roles")
        except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError, TypeError, AttributeError) as exc:
            raise ReviewError("invalid service authentication", 401) from exc
        if required_role not in self.callers.get(key, ()) or required_role not in roles:
            raise ReviewError("service caller is not authorized", 403)
        return key[0]


def application_token_scope(value):
    if (not isinstance(value, str) or len(value) > 256 or not value.startswith("api://")
            or not value.endswith("/.default") or any(character.isspace() for character in value)
            or any(character in value for character in ("?", "#", "{", "}"))):
        raise ContractError("explicit application resource scope api://.../.default required")
    if not value[len("api://"):-len("/.default")]:
        raise ContractError("application token scope cannot have an empty resource")
    return value


def request_document(content, *, maximum=64_000):
    if not isinstance(content, bytes) or len(content) > maximum:
        raise ContractError("bounded JSON request body required")
    value = strict_json(content)
    if not isinstance(value, dict):
        raise ContractError("JSON object required")
    return value
