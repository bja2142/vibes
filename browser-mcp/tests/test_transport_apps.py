from __future__ import annotations

from browser_puppet.server import build_network_app


def _route_paths(app) -> set[str]:
    return {getattr(route, "path", None) for route in app.routes}


def test_build_network_app_for_sse_exposes_sse_routes() -> None:
    app = build_network_app("sse")

    paths = _route_paths(app)

    assert "/sse" in paths
    assert "/messages" in paths
    assert "/mcp" not in paths


def test_build_network_app_for_http_exposes_mcp_route() -> None:
    app = build_network_app("http")

    paths = _route_paths(app)

    assert "/mcp" in paths
    assert "/sse" not in paths


def test_build_network_app_for_both_exposes_both_route_families() -> None:
    app = build_network_app("both")

    paths = _route_paths(app)

    assert "/mcp" in paths
    assert "/sse" in paths
    assert "/messages" in paths
