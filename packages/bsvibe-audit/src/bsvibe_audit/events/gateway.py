"""``gateway.*`` events emitted by BSGateway."""

from __future__ import annotations

from typing import ClassVar

from bsvibe_audit.events.base import AuditEventBase


class RouteConfigChanged(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "gateway.route.config_changed"


class ApiKeyIssued(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "gateway.api_key.issued"


class ApiKeyRevoked(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "gateway.api_key.revoked"


class ClassifierCacheHit(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "gateway.classifier.cache_hit"


class RateLimitViolated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "gateway.rate_limit.violated"


__all__ = [
    "RouteConfigChanged",
    "ApiKeyIssued",
    "ApiKeyRevoked",
    "ClassifierCacheHit",
    "RateLimitViolated",
]
