"""The sync client (_client.py) is generated from the async one; it must be what the async source gives today."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]


def test_generated_sync_client_is_up_to_date() -> None:
    spec = importlib.util.spec_from_file_location("unasync", PKG / "scripts" / "unasync.py")
    assert spec and spec.loader
    unasync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(unasync)
    expected = unasync.generate((PKG / "src" / "boxline" / "_async_client.py").read_text(encoding="utf-8"))
    current = (PKG / "src" / "boxline" / "_client.py").read_text(encoding="utf-8")
    assert current == expected, "src/boxline/_client.py is out of date: run python scripts/unasync.py"
    # Nothing async may survive the transformation.
    for word in ("await ", "async def", "AsyncClient", "aiter_lines", "asyncio"):
        assert word not in current, word
