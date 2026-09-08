# Windows setup

`chrome-web-mcp` runs natively on Windows and does not need a separate display
server.

## Requirements

- Windows 10 or 11
- Python 3.10+
- Google Chrome, Chromium, or a project-local Chrome for Testing build

Browser modes:

- `show_browser: true` -> native visible Chrome window
- `show_browser: false` -> off-screen native Chrome (not `--headless`)
- `CW_DISPLAY_MODE=headless` -> Chrome `--headless=new` (CI / no desktop)

Hidden mode keeps a real Chrome process rather than using `--headless=new`. It
is intended for an interactive Windows user session; a Windows service or CI
job without a desktop must use headless mode.
The server also watches the Chrome process tree and hides windows created after
startup. This is still not a way to view a CAPTCHA or permission dialog.

## Quickstart

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

If `chrome-web-mcp` cannot find Chrome, either install Google Chrome or run:

```bat
python scripts\install-chrome-for-testing.py
```

The installer stores the official Chrome for Testing build under the
git-ignored `.local-chrome\` directory. Starting the MCP server never
downloads a browser by itself.

Then:

```bat
chrome-web-mcp
```

## Browser discovery order

1. `CW_CHROME`
2. project-local `.local-chrome\**\chrome.exe`
3. `C:\Program Files\Google\Chrome\Application\chrome.exe`
4. `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`
5. `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`
6. PATH `chrome.exe` / Chromium
7. Chrome Beta / Canary

An invalid explicit `CW_CHROME` value fails closed instead of silently
falling back to another browser.

## Cursor MCP example

In Cursor Settings -> MCP, or in `%USERPROFILE%\.cursor\mcp.json`:

```json
{
  "mcpServers": {
    "chrome-web": {
      "command": "C:\\Users\\YOU\\chrome-web-mcp-windows\\.venv\\Scripts\\chrome-web-mcp.exe"
    }
  }
}
```

Replace the path with your checkout. After saving, restart Cursor (or reload
MCP) and check that `google_search`, `fetch_url`, and `health_check` appear.

Optional environment in the same entry:

```json
{
  "mcpServers": {
    "chrome-web": {
      "command": "C:\\Users\\YOU\\chrome-web-mcp-windows\\.venv\\Scripts\\chrome-web-mcp.exe",
      "env": {
        "CW_DISPLAY_MODE": "hidden"
      }
    }
  }
}
```

## Codex Desktop and Codex CLI

Codex reads its local MCP configuration from `%USERPROFILE%\\.codex\\config.toml`.
Merge the server block from
[`examples/mcp-config.codex.toml`](../examples/mcp-config.codex.toml), replace
the example checkout path, and restart Codex:

```toml
[mcp_servers.chrome-web]
command = 'C:\Users\YOU\chrome-web-mcp-windows\.venv\Scripts\chrome-web-mcp.exe'
cwd = 'C:\Users\YOU\chrome-web-mcp-windows'
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled = true

[mcp_servers.chrome-web.env]
CW_DISPLAY_MODE = "hidden"
```

After restarting, use `/mcp` in a local Codex task and confirm that
`chrome-web` is enabled. Use `CW_DISPLAY_MODE = "headless"` in environments
without an interactive Windows desktop.

## Other common MCP clients

The repository includes copy-ready configuration examples for the main Windows MCP
clients:

| Client | Configuration file or screen | Example |
| --- | --- | --- |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` or Cursor Settings -> MCP | [`mcp-config.windows.json`](../examples/mcp-config.windows.json) |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | [`mcp-config.windows.json`](../examples/mcp-config.windows.json) |
| VS Code / GitHub Copilot | `.vscode\mcp.json` or `MCP: Open User Configuration` | [`mcp-config.vscode.json`](../examples/mcp-config.vscode.json) |
| Antigravity CLI | `%USERPROFILE%\.gemini\config\mcp_config.json` or workspace `.agents\mcp_config.json` | [`mcp-config.antigravity.json`](../examples/mcp-config.antigravity.json) |
| Windsurf / Cascade | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` | [`mcp-config.windsurf.json`](../examples/mcp-config.windsurf.json) |
| OpenCode | Workspace `opencode.jsonc` | [`mcp-config.opencode.jsonc`](../examples/mcp-config.opencode.jsonc) |

The Cursor, Claude Desktop, Antigravity CLI, and Windsurf examples use the common
`mcpServers` JSON shape. VS Code uses `servers` and an explicit `stdio` type;
OpenCode uses `mcp.servers` and a command array. For complete instructions,
including local-checkout paths, see the configuration examples in the
[English README](../README.md) or [Japanese README](../README.jp.md).

## Config file

Default path: `%APPDATA%\chrome-web-mcp\config.json`

`CW_CONFIG` can point to another JSON file and takes priority over the default
AppData path.

```json
{
  "show_browser": true,
  "hl": "ja",
  "gl": "jp"
}
```

## Profile isolation

The server does not use the normal user Chrome profile by default. Each MCP
server process gets a temporary per-process profile under `%TEMP%`. Do not
point `CW_PROFILE_DIR` at the normal Chrome profile unless that is explicitly
intended.

## CAPTCHA behavior

The server detects Google CAPTCHA pages but does not bypass them.

- visible mode: solve the challenge in the Chrome window, then retry
- hidden / headless: the tool returns `captcha_required: true`; wait or
  set `CW_DISPLAY_MODE=native` in the MCP client's server environment and restart.
  This overrides `show_browser`; when the environment variable is unset,
  `show_browser: true` also works. Search again after restarting, solve any
  challenge in the visible window, then retry.

## Updating

Stop all MCP servers using the checkout before updating: Windows cannot replace
a running executable. Follow the [README update steps](../README.md#updating)
in a separate PowerShell window, and reconnect only after installation succeeds.

## Cleanup

Windows assigns Chrome to a Job Object with kill-on-close, so a hard-killed
MCP client should take the browser with it. If Windows refuses the assignment,
startup fails instead of leaving an untracked Chrome process. Startup also
sweeps leftover per-pid profiles from previous crashes.

## Testing

```bat
set CW_DISPLAY_MODE=headless
python -m pytest -q -m "not live"
```

Tests marked `live` contact Google and can legitimately encounter a CAPTCHA.
The Windows suite also checks Chrome discovery, native display flags, process
identity, cleanup, and Job Object behavior.
