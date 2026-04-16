from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


pytest.importorskip("mcp")
pytest.importorskip("playwright.async_api")

from browser_puppet import server
from browser_puppet.server import mcp


def test_create_context_exposes_direct_input_schema() -> None:
    tools = asyncio.run(mcp.list_tools())
    create_context = next(tool for tool in tools if tool.name == "create_context")

    assert "browser" in create_context.inputSchema["properties"]
    assert "profile" in create_context.inputSchema["properties"]
    assert "args" in create_context.inputSchema["properties"]
    assert "kwargs" in create_context.inputSchema["properties"]
    assert create_context.inputSchema["required"] == ["browser"]


@pytest.mark.asyncio
async def test_create_context_accepts_direct_and_wrapped_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    create_context = AsyncMock(return_value={"context_id": "ctx-1"})
    monkeypatch.setattr(server.APP, "create_context", create_context)

    direct_result = await server._create_context(browser="chromium", profile={"locale": "en-US"})
    wrapped_result = await server._create_context(args=[], kwargs={"browser": "chromium", "profile": {"locale": "en-US"}})

    assert direct_result == {"context_id": "ctx-1"}
    assert wrapped_result == {"context_id": "ctx-1"}
    assert create_context.await_args_list[0].args == ("chromium", {"locale": "en-US"})
    assert create_context.await_args_list[1].args == ("chromium", {"locale": "en-US"})


@pytest.mark.asyncio
async def test_tool_validation_accepts_wrapped_and_flattened_create_context(monkeypatch: pytest.MonkeyPatch) -> None:
    create_context = AsyncMock(return_value={"context_id": "ctx-1"})
    monkeypatch.setattr(server.APP, "create_context", create_context)

    tool = server.mcp._tool_manager.get_tool("create_context")
    assert tool is not None

    wrapped_result = await tool.run(
        {
            "args": [],
            "kwargs": {
                "browser": "chromium",
                "profile": {"locale": "en-US"},
            },
        }
    )
    flattened_result = await tool.run(
        {
            "args": [],
            "kwargs": {
                "allow_local_network": True,
                "ignore_https_errors": True,
            },
        }
    )

    assert wrapped_result == {"context_id": "ctx-1"}
    assert flattened_result == {"context_id": "ctx-1"}
    assert create_context.await_args_list[0].args == ("chromium", {"locale": "en-US"})
    assert create_context.await_args_list[1].args == (
        "chromium",
        {"allow_local_network": True, "ignore_https_errors": True},
    )


@pytest.mark.asyncio
async def test_tool_validation_accepts_wrapped_open_page(monkeypatch: pytest.MonkeyPatch) -> None:
    open_page = AsyncMock(return_value={"page_id": "page-1"})
    monkeypatch.setattr(server.APP, "open_page", open_page)

    tool = server.mcp._tool_manager.get_tool("open_page")
    assert tool is not None

    result = await tool.run(
        {
            "args": [],
            "kwargs": {
                "context_id": "context-8941e8a4ae56",
                "url": "http://10.0.2.15:5000/math/",
            },
        }
    )

    assert result == {"page_id": "page-1"}
    assert open_page.await_args_list[0].args == ("context-8941e8a4ae56", "http://10.0.2.15:5000/math/", "load", 30000)


@pytest.mark.asyncio
async def test_create_context_passes_explicit_headless_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBrowser:
        version = "145.0.0.0"

        async def new_context(self, **kwargs):
            class FakeContext:
                async def add_init_script(self, script):
                    return None

                def on(self, event, handler):
                    return None

            return FakeContext()

    ensure_browser = AsyncMock(return_value=FakeBrowser())
    refresh_routes = AsyncMock(return_value=None)
    monkeypatch.setattr(server.APP, "ensure_browser", ensure_browser)
    monkeypatch.setattr(server.APP, "_refresh_context_routes", refresh_routes)

    result = await server.APP.create_context("chromium", {"headless": True})

    assert result["context_id"].startswith("context-")
    assert ensure_browser.await_args.kwargs == {"headless": True}
