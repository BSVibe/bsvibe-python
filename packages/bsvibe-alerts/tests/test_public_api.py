"""Stable public API surface — what the four products import.

If a name moves from this list, every product import statement breaks
on next ``uv sync``. Treat any failure here as a wire-compat regression.
"""

from __future__ import annotations


def test_top_level_exports() -> None:
    import bsvibe_alerts

    expected = {
        "Alert",
        "AlertSeverity",
        "AlertSettings",
        "AlertRouter",
        "AlertClient",
        "AlertChannel",
        "StructlogChannel",
        "TelegramChannel",
        "SlackChannel",
        "__version__",
    }
    missing = expected - set(bsvibe_alerts.__all__)
    assert not missing, f"missing exports: {missing}"

    for name in expected - {"__version__"}:
        assert getattr(bsvibe_alerts, name) is not None


def test_version_string() -> None:
    import bsvibe_alerts

    assert bsvibe_alerts.__version__ == "0.1.0"
