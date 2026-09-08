# Contributing to chrome-web-mcp

## Setup

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[test]"
```

Requires Python 3.10+ and Chrome/Chromium. The supported development platform
for this edition is Windows 10/11; native/hidden/headless Chrome does not
require a separate display server.

## Verification

```powershell
pytest -q
pytest -q -m "not live"  # deterministic tests; browser fixtures use local responses
python -m build
uv lock --check
uvx --from pyright pyright --project pyrightconfig.json --pythonpath .venv\Scripts\python.exe
```

- `pytest -q` runs the full suite.
- `pytest -q -m "not live"` runs only deterministic tests (synthetic
  responses and local fixtures, no Google dependency).
- `python -m build` verifies the wheel/sdist builds.
- `uv lock --check` verifies that `uv.lock` matches `pyproject.toml`.
- `uvx --from pyright ... --pythonpath ...` checks the product code without
  adding a runtime dependency to the Windows package or rewriting the venv.

The end-to-end tests exercise the real stdio MCP handshake, `tools/list`,
Google search, public URL fetching, and shutdown cleanup. They require Chrome
and network access. Windows contributors can install an isolated browser with
`python scripts/install-chrome-for-testing.py`. Tests marked `live` contact
Google and can fail if the network is unavailable or Google requires a
CAPTCHA. Browser security regression tests use synthetic responses and local
fixtures, without depending on Google.

E2E tests use a sandbox dir (`.e2e-sandbox` by default, overridable via
`CW_E2E_SANDBOX`) so test Chrome profiles/locks do not collide with a live
server holding the default profile lock.
