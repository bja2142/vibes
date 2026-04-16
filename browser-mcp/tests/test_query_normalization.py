from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from browser_puppet import server
from browser_puppet.server import (
    BrowserPuppetApp,
    lift_fields_into_nested_payload,
    normalize_click_and_wait_payload,
    normalize_fill_and_click_payload,
    normalize_mcp_tool_payload,
    normalize_fill_form_payload,
    normalize_wait_for_payload,
)


def test_normalize_target_query_wraps_raw_string_as_selector() -> None:
    app = BrowserPuppetApp()

    assert app._normalize_target_query("#btnStart") == {"selector": "#btnStart"}


def test_normalize_target_query_preserves_mapping() -> None:
    app = BrowserPuppetApp()

    assert app._normalize_target_query({"role": "button", "text": "Start"}) == {"role": "button", "text": "Start"}


def test_normalize_wait_for_payload_wraps_loose_target_fields() -> None:
    normalized = normalize_wait_for_payload(
        {
            "page_id": "page-1",
            "text": "Stopped peer0.org2.example.com",
            "timeout_ms": 10000,
        }
    )

    assert normalized == {
        "page_id": "page-1",
        "state": "visible",
        "target": {
            "text": "Stopped peer0.org2.example.com",
            "timeout_ms": 10000,
        },
    }


def test_normalize_wait_for_payload_infers_url_state_from_pattern() -> None:
    normalized = normalize_wait_for_payload({"page_id": "page-1", "pattern": "*/dashboard*"})

    assert normalized == {
        "page_id": "page-1",
        "state": "url",
        "target": {"pattern": "*/dashboard*"},
    }


def test_lift_fields_into_nested_payload_wraps_string_target() -> None:
    normalized = lift_fields_into_nested_payload(
        {"page_id": "page-1", "target": "#start", "text": "Start"},
        field_name="target",
        candidate_keys=("text",),
        hoist_keys=("page_id",),
    )

    assert normalized == {
        "page_id": "page-1",
        "target": {
            "selector": "#start",
            "text": "Start",
        },
    }


def test_normalize_mcp_tool_payload_wraps_loose_find_elements_query() -> None:
    normalized = normalize_mcp_tool_payload(
        {"args": ["page-1"], "kwargs": {"text": "Start"}},
        "find_elements",
        ("page_id", "query"),
    )

    assert normalized == {
        "page_id": "page-1",
        "query": {"text": "Start"},
    }


def test_normalize_mcp_tool_payload_wraps_drag_and_drop_targets() -> None:
    normalized = normalize_mcp_tool_payload(
        {
            "page_id": "page-1",
            "source_target": "#source",
            "dest_target": {"text": "Drop here"},
        },
        "drag_and_drop",
        ("page_id", "source_element_id", "source_target", "target_element_id", "dest_target", "observe"),
    )

    assert normalized == {
        "page_id": "page-1",
        "source_target": {"selector": "#source"},
        "dest_target": {"text": "Drop here"},
    }


def test_normalize_fill_form_payload_wraps_single_loose_field() -> None:
    normalized = normalize_fill_form_payload(
        {
            "page_id": "page-1",
            "text": "Username",
            "value": "alice",
            "submit": True,
        }
    )

    assert normalized == {
        "page_id": "page-1",
        "fields": [{"target": {"text": "Username"}, "value": "alice"}],
        "submit": True,
    }


def test_normalize_fill_form_payload_normalizes_field_and_form_targets() -> None:
    normalized = normalize_fill_form_payload(
        {
            "page_id": "page-1",
            "fields": [{"target": "#username", "value": "alice"}],
            "form_target": "#login-form",
        }
    )

    assert normalized == {
        "page_id": "page-1",
        "fields": [{"target": {"selector": "#username"}, "value": "alice"}],
        "form_target": {"selector": "#login-form"},
    }


def test_normalize_mcp_tool_payload_wraps_run_action_and_describe_step() -> None:
    normalized = normalize_mcp_tool_payload(
        {
            "tool": "click",
            "page_id": "page-1",
            "target": "#submit",
            "mode": "compact",
        },
        "run_action_and_describe",
        ("action", "expect", "mode"),
    )

    assert normalized == {
        "action": {
            "tool": "click",
            "page_id": "page-1",
            "target": {"selector": "#submit"},
        },
        "mode": "compact",
    }


def test_normalize_mcp_tool_payload_splits_press_key_chord_string() -> None:
    normalized = normalize_mcp_tool_payload(
        {"page_id": "page-1", "keys": "Control+K"},
        "press_key_chord",
        ("page_id", "keys", "observe"),
    )

    assert normalized == {"page_id": "page-1", "keys": ["Control", "K"]}


def test_normalize_fill_and_click_payload_wraps_single_field_and_click_target() -> None:
    normalized = normalize_fill_and_click_payload(
        {
            "page_id": "page-1",
            "text": "Username",
            "value": "alice",
            "click_target": "#submit",
        }
    )

    assert normalized == {
        "page_id": "page-1",
        "fields": [{"target": {"text": "Username"}, "value": "alice"}],
        "click_target": {"selector": "#submit"},
    }


def test_normalize_click_and_wait_payload_wraps_wait_target() -> None:
    normalized = normalize_click_and_wait_payload(
        {
            "page_id": "page-1",
            "target": "#submit",
            "wait_for": "url",
            "pattern": "*/dashboard*",
        }
    )

    assert normalized == {
        "page_id": "page-1",
        "target": {"selector": "#submit"},
        "wait_for": "url",
        "wait_target": {"pattern": "*/dashboard*"},
    }


@pytest.mark.asyncio
async def test_wait_for_tool_validation_accepts_loose_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    wait_for = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(server.APP, "wait_for", wait_for)

    tool = server.mcp._tool_manager.get_tool("wait_for")
    assert tool is not None

    result = await tool.run(
        {
            "args": [{"page_id": "page-fe9495aed94b", "text": "Stopped peer0.org2.example.com"}],
            "kwargs": {"timeout_ms": 10000},
        }
    )

    assert result == {"ok": True}
    assert wait_for.await_args.args == (
        {"text": "Stopped peer0.org2.example.com", "timeout_ms": 10000},
        "visible",
        "page-fe9495aed94b",
        "auto",
    )


@pytest.mark.asyncio
async def test_find_elements_tool_validation_accepts_loose_query(monkeypatch: pytest.MonkeyPatch) -> None:
    find_elements = AsyncMock(return_value={"matches": []})
    monkeypatch.setattr(server.APP, "find_elements", find_elements)

    tool = server.mcp._tool_manager.get_tool("find_elements")
    assert tool is not None

    result = await tool.run({"args": ["page-1"], "kwargs": {"text": "Start"}})

    assert result == {"matches": []}
    assert find_elements.await_args.args == ("page-1", {"text": "Start"})


@pytest.mark.asyncio
async def test_click_tool_validation_accepts_string_target(monkeypatch: pytest.MonkeyPatch) -> None:
    click = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "click", click)

    tool = server.mcp._tool_manager.get_tool("click")
    assert tool is not None

    result = await tool.run({"args": [], "kwargs": {"page_id": "page-1", "target": "#start"}})

    assert result == {"success": True}
    assert click.await_args.args == ("page-1", None, {"selector": "#start"}, "left", 1, server.DEFAULT_TIMEOUT_MS, "auto")


@pytest.mark.asyncio
async def test_type_text_tool_validation_preserves_text_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    type_text = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "type_text", type_text)

    tool = server.mcp._tool_manager.get_tool("type_text")
    assert tool is not None

    result = await tool.run(
        {
            "page_id": "page-4ecfc0762189",
            "text": "invoke create diploma-001 credential 4 \"John Doe\" 2024\n",
            "clear_first": False,
        }
    )

    assert result == {"success": True}
    assert type_text.await_args.args == (
        "invoke create diploma-001 credential 4 \"John Doe\" 2024\n",
        "page-4ecfc0762189",
        None,
        None,
        False,
        "auto",
        None,
        None,
        "auto",
    )


@pytest.mark.asyncio
async def test_type_text_tool_validation_accepts_target_shorthand_without_stealing_text(monkeypatch: pytest.MonkeyPatch) -> None:
    type_text = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "type_text", type_text)

    tool = server.mcp._tool_manager.get_tool("type_text")
    assert tool is not None

    result = await tool.run(
        {
            "page_id": "page-4ecfc0762189",
            "text": "hlf-help",
            "css": "textarea.terminal-input",
            "clear_first": False,
        }
    )

    assert result == {"success": True}
    assert type_text.await_args.args == (
        "hlf-help",
        "page-4ecfc0762189",
        None,
        {"css": "textarea.terminal-input"},
        False,
        "auto",
        None,
        None,
        "auto",
    )


@pytest.mark.asyncio
async def test_type_text_tool_validation_accepts_focused_keystroke_typing_options(monkeypatch: pytest.MonkeyPatch) -> None:
    type_text = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "type_text", type_text)

    tool = server.mcp._tool_manager.get_tool("type_text")
    assert tool is not None

    result = await tool.run(
        {
            "page_id": "page-4ecfc0762189",
            "text": "hlf-help\n",
            "clear_first": False,
            "typing_mode": "keystrokes",
            "keystroke_delay_ms": 35,
            "keystroke_jitter_ms": 12,
        }
    )

    assert result == {"success": True}
    assert type_text.await_args.args == (
        "hlf-help\n",
        "page-4ecfc0762189",
        None,
        None,
        False,
        "keystrokes",
        35,
        12,
        "auto",
    )


@pytest.mark.asyncio
async def test_fill_form_tool_validation_accepts_loose_single_field(monkeypatch: pytest.MonkeyPatch) -> None:
    fill_form = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "fill_form", fill_form)

    tool = server.mcp._tool_manager.get_tool("fill_form")
    assert tool is not None

    result = await tool.run({"args": ["page-1"], "kwargs": {"text": "Username", "value": "alice", "submit": True}})

    assert result == {"success": True}
    assert fill_form.await_args.args == ("page-1", [{"target": {"text": "Username"}, "value": "alice"}], None, True, "auto")


@pytest.mark.asyncio
async def test_run_action_and_describe_tool_validation_accepts_flat_action(monkeypatch: pytest.MonkeyPatch) -> None:
    run_action_and_describe = AsyncMock(return_value={"result": True})
    monkeypatch.setattr(server.APP, "run_action_and_describe", run_action_and_describe)

    tool = server.mcp._tool_manager.get_tool("run_action_and_describe")
    assert tool is not None

    result = await tool.run({"tool": "click", "page_id": "page-1", "target": "#submit"})

    assert result == {"result": True}
    assert run_action_and_describe.await_args.args == ({"tool": "click", "page_id": "page-1", "target": {"selector": "#submit"}}, None, "compact")


@pytest.mark.asyncio
async def test_press_key_chord_tool_validation_accepts_string(monkeypatch: pytest.MonkeyPatch) -> None:
    press_key_chord = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "press_key_chord", press_key_chord)

    tool = server.mcp._tool_manager.get_tool("press_key_chord")
    assert tool is not None

    result = await tool.run({"page_id": "page-1", "keys": "Control+K"})

    assert result == {"success": True}
    assert press_key_chord.await_args.args == ("page-1", ["Control", "K"], "auto")


@pytest.mark.asyncio
async def test_find_interactive_candidates_tool_validation_accepts_loose_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    find_interactive_candidates = AsyncMock(return_value={"candidates": []})
    monkeypatch.setattr(server.APP, "find_interactive_candidates", find_interactive_candidates)

    tool = server.mcp._tool_manager.get_tool("find_interactive_candidates")
    assert tool is not None

    result = await tool.run({"page_id": "page-1", "intent": "submit button", "text": "Submit"})

    assert result == {"candidates": []}
    assert find_interactive_candidates.await_args.args == ("page-1", "submit button", {"text": "Submit"}, 10)


@pytest.mark.asyncio
async def test_fill_and_click_tool_validation_accepts_flattened_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fill_and_click = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "fill_and_click", fill_and_click)

    tool = server.mcp._tool_manager.get_tool("fill_and_click")
    assert tool is not None

    result = await tool.run({"page_id": "page-1", "text": "Username", "value": "alice", "click_target": "#submit"})

    assert result == {"success": True}
    assert fill_and_click.await_args.args == (
        "page-1",
        [{"target": {"text": "Username"}, "value": "alice"}],
        {"selector": "#submit"},
        "auto",
    )


@pytest.mark.asyncio
async def test_click_and_wait_tool_validation_accepts_common_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    click_and_wait = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "click_and_wait", click_and_wait)

    tool = server.mcp._tool_manager.get_tool("click_and_wait")
    assert tool is not None

    result = await tool.run(
        {
            "args": [],
            "kwargs": {
                "page_id": "page-1",
                "target": {"selector": "form button[type='submit']"},
                "wait_for": "navigation",
            },
        }
    )

    assert result == {"success": True}
    assert click_and_wait.await_args.args == (
        "page-1",
        None,
        {"selector": "form button[type='submit']"},
        "left",
        1,
        server.DEFAULT_TIMEOUT_MS,
        "navigation",
        None,
        "auto",
    )


@pytest.mark.asyncio
async def test_submit_form_tool_validation_accepts_common_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    submit_form = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server.APP, "submit_form", submit_form)

    tool = server.mcp._tool_manager.get_tool("submit_form")
    assert tool is not None

    result = await tool.run(
        {
            "args": [],
            "kwargs": {
                "page_id": "page-9caf1250da33",
                "target": {"selector": "form.form-stack"},
            },
        }
    )

    assert result == {"success": True}
    assert submit_form.await_args.args == ("page-9caf1250da33", None, {"selector": "form.form-stack"}, "auto")
