import asyncio
import json
import os
import subprocess
import sys
import tempfile

import pytest

# Use a throwaway Chrome profile/lock so the in-process live test does not
# collide with a live chrome-web-v2 server holding the default profile lock.
_SANDBOX = tempfile.mkdtemp(prefix="cw-v2-test-")
os.environ["CW_PROFILE_DIR"] = os.path.join(_SANDBOX, "profile")
os.environ["CW_LOCK_PATH"] = os.path.join(_SANDBOX, ".instance.lock")
os.environ["CW_RATE_LIMIT_DB"] = os.path.join(_SANDBOX, "rate-limit.sqlite3")

from chrome_web_mcp import server


def test_exposes_google_search_and_fetch_url_tool_names():
    tools = asyncio.run(server.list_tools())
    assert sorted(tool.name for tool in tools) == ["fetch_url", "google_search"]


def test_browser_environment_cannot_fall_back_to_user_wayland_session(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")

    env = server.BrowserRuntime._browser_environment(":77")

    assert env["DISPLAY"] == ":77"
    assert env["XDG_SESSION_TYPE"] == "x11"
    assert "WAYLAND_DISPLAY" not in env


def test_shared_rate_limiter_reserves_slots_across_processes(tmp_path):
    db_path = tmp_path / "search-rate-limit.sqlite3"
    code = (
        "import sys; "
        "from chrome_web_mcp.server import SharedSearchRateLimiter; "
        "print(SharedSearchRateLimiter(sys.argv[1], min_delay=1.0, max_delay=1.0).reserve_slot(), flush=True)"
    )
    env = dict(os.environ)
    first = subprocess.Popen([sys.executable, "-c", code, str(db_path)], stdout=subprocess.PIPE, text=True, env=env)
    second = subprocess.Popen([sys.executable, "-c", code, str(db_path)], stdout=subprocess.PIPE, text=True, env=env)
    slots = sorted([float(first.communicate(timeout=10)[0]), float(second.communicate(timeout=10)[0])])

    assert slots[1] - slots[0] >= 0.9


def test_call_tool_fetch_url_validates_and_delegates(monkeypatch):
    captured = {}

    async def fake_fetch(url, char_limit):
        captured["url"] = url
        captured["char_limit"] = char_limit
        return {"url": url, "title": "Example", "text": "hello", "truncated": False}

    monkeypatch.setattr(server, "_fetch_page", fake_fetch)
    content = asyncio.run(server.call_tool("fetch_url", {"url": "https://example.com", "char_limit": 500}))
    payload = __import__("json").loads(content[0].text)
    assert captured == {"url": "https://example.com", "char_limit": 500}
    assert payload["success"] is True
    assert payload["data"]["title"] == "Example"


def test_call_tool_fetch_url_rejects_bad_args():
    payload = __import__("json").loads(
        asyncio.run(server.call_tool("fetch_url", {"url": ""}))[0].text
    )
    assert payload["success"] is False
    assert "url is required" in payload["error"]
    payload2 = __import__("json").loads(
        asyncio.run(server.call_tool("fetch_url", {"url": "https://example.com", "char_limit": 10}))[0].text
    )
    assert payload2["success"] is False


def test_normalizes_direct_and_legacy_google_result_links():
    direct = "https://example.com/article"
    legacy = "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Farticle&sa=U"
    assert server._normalize_candidate_href(direct) == direct
    assert server._normalize_candidate_href(legacy) == direct


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "http://10.0.0.1/private",
        "http://169.254.169.254/latest/meta-data",
        "https://example.com/?token=sk-abcdefghijklmnopqrstuvwxyz",
        "file:///etc/passwd",
    ],
)
def test_rejects_non_public_or_secret_result_urls(url):
    with pytest.raises(ValueError, match="Blocked"):
        server._validate_public_url(url)


def test_call_tool_returns_single_structured_json_layer(monkeypatch):
    async def fake_search(query, limit):
        assert query == "Hermes Agent"
        assert limit == 2
        return [{"title": "Hermes", "url": "https://example.com/", "description": "Agent", "position": 1}]

    monkeypatch.setattr(server, "_search_google", fake_search)
    content = asyncio.run(server.call_tool("google_search", {"query": "Hermes Agent", "limit": 2}))
    payload = __import__("json").loads(content[0].text)
    assert payload == {
        "success": True,
        "data": {"web": [{"title": "Hermes", "url": "https://example.com/", "description": "Agent", "position": 1}]},
    }


def test_build_results_deduplicates_and_honors_limit():
    candidates = [
        {"title": "One", "href": "https://one.example/a", "description": "first"},
        {"title": "One duplicate", "href": "https://one.example/a", "description": "duplicate"},
        {"title": "Two", "href": "https://two.example/b", "description": "second"},
        {"title": "Three", "href": "https://three.example/c", "description": "third"},
    ]

    async def resolve(href):
        return href

    results = asyncio.run(server._build_results(candidates, 2, resolve_url=resolve))
    assert results == [
        {"title": "One", "url": "https://one.example/a", "description": "first", "position": 1},
        {"title": "Two", "url": "https://two.example/b", "description": "second", "position": 2},
    ]


def test_live_google_search_returns_real_external_results():
    results = asyncio.run(server._search_google("Hermes Agent Nous Research", 3))
    assert len(results) == 3
    assert [item["position"] for item in results] == [1, 2, 3]
    assert all(item["title"] for item in results)
    assert all(item["url"].startswith(("http://", "https://")) for item in results)
    assert all("google.com/goto" not in item["url"] for item in results)
    assert any("hermes-agent.nousresearch.com" in item["url"] for item in results)
