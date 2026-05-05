"""Tests for the demo session JWT issuer.

The demo backend issues HS256-signed JWTs using DEMO_JWT_SECRET. Prod
auth.bsvibe.dev tokens are NOT accepted (different signing keys, different
verification path).
"""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest

from bsvibe_demo import DemoJWTError, decode_demo_jwt, mint_demo_jwt


class TestMintDemoJwt:
    def test_mint_returns_jwt_with_tenant_and_is_demo_claims(self) -> None:
        secret = "a" * 64
        tenant_id = uuid4()

        token = mint_demo_jwt(tenant_id, secret=secret, ttl_seconds=7200)

        decoded = jwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["tenant_id"] == str(tenant_id)
        assert decoded["is_demo"] is True
        assert "exp" in decoded
        assert "iat" in decoded

    def test_mint_exp_respects_ttl(self) -> None:
        secret = "b" * 64
        before = time.time()
        token = mint_demo_jwt(uuid4(), secret=secret, ttl_seconds=60)
        decoded = jwt.decode(token, secret, algorithms=["HS256"])
        assert before + 55 <= decoded["exp"] <= before + 65


class TestDecodeDemoJwt:
    def test_returns_tenant_uuid_and_metadata(self) -> None:
        secret = "f" * 64
        tid = uuid4()
        token = mint_demo_jwt(tid, secret=secret, ttl_seconds=60)
        claims = decode_demo_jwt(token, secret=secret)
        assert claims.tenant_id == tid
        assert claims.is_demo is True

    def test_rejects_wrong_secret(self) -> None:
        token = mint_demo_jwt(uuid4(), secret="c" * 64, ttl_seconds=60)
        with pytest.raises(DemoJWTError):
            decode_demo_jwt(token, secret="d" * 64)

    def test_rejects_expired_token(self) -> None:
        token = mint_demo_jwt(uuid4(), secret="g" * 64, ttl_seconds=-10)
        with pytest.raises(DemoJWTError):
            decode_demo_jwt(token, secret="g" * 64)

    def test_rejects_token_without_is_demo_claim(self) -> None:
        # Defense: a prod-shaped JWT (no is_demo) MUST be rejected even if
        # signature happens to verify under the demo secret.
        token = jwt.encode(
            {"tenant_id": str(uuid4()), "exp": time.time() + 60},
            "f" * 64,
            algorithm="HS256",
        )
        with pytest.raises(DemoJWTError):
            decode_demo_jwt(token, secret="f" * 64)
