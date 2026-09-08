# chrome-web-mcp

[![Verify](https://github.com/kotao-boop/chrome-web-mcp-windows/actions/workflows/verify.yml/badge.svg)](https://github.com/kotao-boop/chrome-web-mcp-windows/actions/workflows/verify.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[日本語版 README](README.jp.md)

> This repository is an independent Windows-focused fork of
> [kuraneko1/chrome-web-mcp](https://github.com/kuraneko1/chrome-web-mcp). It is
> not an official Windows edition of the upstream project and is not endorsed
> by the upstream maintainers.

`chrome-web-mcp` is a Model Context Protocol (MCP) server that lets AI tools
use Google Chrome to retrieve Google search results and readable web-page text.
It runs on Windows 10 and Windows 11.

It provides three main tools:

- `google_search`: Runs a Google search and returns structured data such as
  titles, URLs, and descriptions.
- `fetch_url`: Fetches a public web page and returns its readable content without
  unnecessary page decoration.
- `health_check`: Reports browser status, search queue wait time, and CAPTCHA
  state.

---

## Table of contents

1. [Important prerequisites and cautions](#important-prerequisites-and-cautions)
2. [Main features](#main-features)
3. [Quickstart](#quickstart)
4. [Tool details and specifications](#tool-details-and-specifications)
5. [Configuration and customization](#configuration-and-customization)
6. [CAPTCHA handling](#captcha-handling)
7. [Safety and security design](#safety-and-security-design)
8. [Environment and storage requirements](#environment-and-storage-requirements)
9. [Updating](#updating)
10. [Development and testing](#development-and-testing)
11. [Origins and acknowledgements](#origins-and-acknowledgements)
12. [License](#license)

---

## Important prerequisites and cautions

> **Windows only (Windows 10 / Windows 11)**
> This software is designed and tested for Windows. Windows is the only
> supported operating system. No additional display server is required; it
> runs with standard Windows capabilities.

> [!CAUTION]
> **Search frequency and browser startup**
> Each MCP server process starts one browser. Searches are processed in order
> instead of being sent as a large burst.
>
> - Normal use should not hit the rate limiter. Starting 15 or more searches in
>   one minute produces a `pace_warning`.
> - A warning does not stop the search. However, excessive repeated requests
>   can cause Google to present a CAPTCHA (a “prove you are not a robot” check).
>   If that happens, wait a few minutes and retry, or enable visible browser
>   mode and solve the challenge manually.
> - Starting separate MCP server processes starts separate browsers. Avoid
>   sending excessive searches from several AI tools at the same time.

---

## Main features

- **Real Chrome execution**: Search and page extraction run in a real Chrome
  browser, including pages whose content changes through JavaScript.
- **Readable Markdown shaping**: `trafilatura` and `html2text` remove common
  headers, footers, advertisements, and other boilerplate before returning
  readable content.
- **Language and region hints**: Choose Google language and region values such
  as `hl: "ja"` and `gl: "jp"`.
- **Switchable browser display**: Use a normal visible window, an off-screen
  hidden window, or headless mode for environments without an interactive
  desktop.
- **Safe network access**: Only public destinations are allowed; `localhost`
  and private IP addresses are blocked.
- **Reliable cleanup**: A Windows Job Object makes Chrome and related processes
  follow the MCP client when it exits.

---

## Quickstart

This Windows edition is distributed through this GitHub repository and its
GitHub Releases. The most predictable setup is to run the executable from a
local virtual environment, so every MCP client uses the same checked-out copy.

### 1. Set up a local checkout

Clone the repository and install the runtime dependencies from Command Prompt
or PowerShell:

```bat
git clone https://github.com/kotao-boop/chrome-web-mcp-windows.git
cd chrome-web-mcp-windows
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

If Google Chrome is not already installed, place a project-local Chrome for
Testing build with the following command. This step is not needed when a
supported Chrome or Chromium installation is already available:

```bat
python scripts\install-chrome-for-testing.py
```

### 2. Add the MCP client configuration

Use the absolute path to the executable inside the virtual environment. Replace
`YOU` with your Windows user name and adjust the checkout path if needed:

```json
{
  "mcpServers": {
    "chrome-web": {
      "command": "C:\\Users\\YOU\\chrome-web-mcp-windows\\.venv\\Scripts\\chrome-web-mcp.exe"
    }
  }
}
```

The repository includes copy-ready examples for the main Windows MCP clients.
Merge the relevant entry into your existing configuration instead of replacing
the whole file.

#### Cursor and Claude Desktop

Use [`examples/mcp-config.windows.json`](examples/mcp-config.windows.json) in
`%USERPROFILE%\.cursor\mcp.json` or
`%APPDATA%\Claude\claude_desktop_config.json`.

#### VS Code / GitHub Copilot

Create or edit `.vscode/mcp.json` in the workspace, or run
`MCP: Open User Configuration` from the Command Palette. Use
[`examples/mcp-config.vscode.json`](examples/mcp-config.vscode.json).

#### Google Antigravity CLI

Antigravity CLI is Google's current terminal agent. If you are moving from
Gemini CLI, use Google's [migration guide](https://antigravity.google/docs/cli/gcli-migration).
Merge [`examples/mcp-config.antigravity.json`](examples/mcp-config.antigravity.json)
into the global configuration at
`%USERPROFILE%\.gemini\config\mcp_config.json`. For a project-only setup, put
the same entry at `.agents\mcp_config.json`. Antigravity IDE uses the same
`mcp_config.json` format; see Google's
[Antigravity MCP guide](https://antigravity.google/docs/cli/mcp/) for current
details.

#### Windsurf (Cascade)

Merge [`examples/mcp-config.windsurf.json`](examples/mcp-config.windsurf.json)
into `%USERPROFILE%\.codeium\windsurf\mcp_config.json`. You can also open the
file from Windsurf Settings -> Cascade -> MCP Servers -> View Raw Config.

#### OpenCode

Add [`examples/mcp-config.opencode.jsonc`](examples/mcp-config.opencode.jsonc)
to `opencode.jsonc` in your workspace. OpenCode expects the executable path in
a one-element `command` array.

#### Codex example

For Codex Desktop or Codex CLI, add
[`examples/mcp-config.codex.toml`](examples/mcp-config.codex.toml) to
`%USERPROFILE%\.codex\config.toml`. Replace `YOU` with your Windows user name.

```toml
[mcp_servers.chrome-web]
command = 'C:\Users\YOU\chrome-web-mcp-windows\.venv\Scripts\chrome-web-mcp.exe'
args = []
cwd = 'C:\Users\YOU\chrome-web-mcp-windows'
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled = true

[mcp_servers.chrome-web.env]
# Change this to "headless" when no interactive desktop is available.
CW_DISPLAY_MODE = "hidden"
```

Save the file and restart the MCP client.

---

## Tool details and specifications

### 1. `google_search`

Runs a Google search and returns structured results.

#### Input parameters

```json
{
  "query": "search keywords",
  "limit": 5,
  "hl": "ja",
  "gl": "jp"
}
```

- `query` (required): Search text, up to 512 characters.
- `limit` (optional): Number of results, from 1 to 20. The default is 5.
- `hl` (optional): Google display-language code, 2 to 8 characters.
- `gl` (optional): Search-region code, 2 to 8 characters.

#### Successful response example

```json
{
  "success": true,
  "data": {
    "web": [
      {
        "title": "Page title",
        "url": "https://example.com/page",
        "description": "Search-result description",
        "position": 1
      }
    ],
    "waited_ms": 1240,
    "pace_warning": null
  }
}
```

The result list is in `data.web`. `waited_ms` is the time spent waiting for a
search slot, in milliseconds. `pace_warning` is normally `null`; when searches
are concentrated in a short period, it contains a warning message.

---

### 2. `fetch_url`

Reads a public web page and returns its content in the requested format.

#### Input parameters

```json
{
  "url": "https://example.com",
  "char_limit": 15000,
  "format": "markdown"
}
```

- `url` (required): An `http://` or `https://` URL, up to 2048 characters.
- `char_limit` (optional): Maximum returned text length, from 100 to 200000
  characters. The default is 15000.
- `format` (optional): Output format. The default is `"markdown"`.
  - `"markdown"`: Readable Markdown with common boilerplate removed. The body
    is in `data.markdown`, and `data.links` contains up to 200 page links.
  - `"text"`: Full rendered page text in `data.text`. No `links` array is
    returned. Use this when Markdown extraction looks incomplete.
  - `"links"`: Plain-text body in `data.text` plus up to 200 page links in
    `data.links`.

#### Successful response example (`format: "markdown"`)

```json
{
  "success": true,
  "data": {
    "requested_url": "https://example.com",
    "final_url": "https://example.com/page",
    "redirected": true,
    "title": "Page title",
    "total_chars": 8500,
    "truncated": false,
    "format": "markdown",
    "formatted": true,
    "extraction": "trafilatura",
    "markdown": "# Article heading\n\nArticle text...",
    "links": [
      {"text": "Related page", "url": "https://example.com/subpage"}
    ]
  }
}
```

Only public destinations can be fetched. `localhost`, private IP addresses,
cloud metadata hosts, credential-bearing URLs, and similar destinations are
rejected. The response includes the final URL, redirect status, total length,
truncation status, and extraction method.

---

### 3. `health_check`

Takes no arguments. Passing arguments returns an error.

#### Successful response example

```json
{
  "success": true,
  "data": {
    "platform": "Windows 10 (AMD64)",
    "display_mode": "native",
    "chrome_binary": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "chrome_version": "Google Chrome 150.0.0.0",
    "chrome_detection_error": null,
    "chrome_alive": true,
    "windows_job_attached": true,
    "gpu_device": null,
    "gpu_renderer": null,
    "gpu_backend": "UNKNOWN",
    "rate_limiter_queue_wait_s": 0.0,
    "rate_limit_min_delay_s": 1.0,
    "rate_limit_max_delay_s": 2.5,
    "recent_searches_60s": 2,
    "pace_warning": null,
    "last_captcha_at": null
  }
}
```

This reports browser liveness, Chrome version, Job Object attachment, recent
search count, and other runtime information. Calling it does not start Chrome.

---

## Configuration and customization

### Creating the configuration file

Place a `config.json` file to change the built-in defaults. Run the following
commands in PowerShell to copy the example configuration and its explanation:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\chrome-web-mcp"
Copy-Item examples\config.json, examples\config.md "$env:APPDATA\chrome-web-mcp\"
```

The default path is `%APPDATA%\chrome-web-mcp\config.json`. To use another
file, set `CW_CONFIG` to its path.

```json
{
  "show_browser": true,
  "hl": "ja",
  "gl": "jp",
  "limit": 5,
  "char_limit": 15000,
  "format": "markdown",
  "min_delay": 1.0,
  "max_delay": 2.5
}
```

The main settings are:

- `show_browser`: `true` opens a normal visible Chrome window. `false` moves
  the window off-screen. Use `CW_DISPLAY_MODE=headless` when there is no
  interactive desktop.
- `hl` / `gl`: Google display language and search region. Use `ja` / `jp` for
  Japanese and Japan-oriented results.
- `limit`: Default number of `google_search` results, from 1 to 20.
- `char_limit`: Default maximum `fetch_url` text length, from 100 to 200000.
- `format`: Default `fetch_url` format: `markdown`, `text`, or `links`.
- `min_delay` / `max_delay`: Search-start interval in seconds, randomized within
  the configured range.

### Running in a service or background environment

For Task Scheduler, CI, automation, or another environment without a desktop,
explicitly select headless mode with an environment variable:

```bat
set CW_DISPLAY_MODE=headless
chrome-web-mcp
```

`CW_DISPLAY_MODE` takes priority over `show_browser` and accepts `native`,
`hidden`, or `headless`.

### Main environment variables

| Environment variable | Purpose | Default |
| :--- | :--- | :--- |
| `CW_CONFIG` | Path to the JSON configuration file | `%APPDATA%\chrome-web-mcp\config.json` |
| `CW_DISPLAY_MODE` | `native`, `hidden`, or `headless`; takes priority over `show_browser` | Not set |
| `CW_CHROME` | Path to the Chrome executable | Automatic discovery |
| `CW_PROFILE_DIR` | Chrome profile directory | `%TEMP%\chrome-web-v2-profile\{process ID}` |
| `CW_LOCK_PATH` | Profile lock-file path | `.instance.lock` inside the profile |
| `CW_RATE_LIMIT_DB` | SQLite path used for search pacing | `%TEMP%\chrome-web-mcp\search-rate-limit.sqlite3` |
| `CW_MIN_DELAY` / `CW_MAX_DELAY` | Search-start interval in seconds | `1.0` / `2.5` |

Unknown configuration keys and invalid values produce a warning on standard
error and keep the relevant built-in default. Restart the MCP client after
changing configuration.

---

## CAPTCHA handling

This server does not automatically bypass Google image verification or password
authentication.

When repeated searches cause Google to request verification, the tool returns an
error like this:

```json
{
  "success": false,
  "error": "...",
  "captcha_required": true
}
```

- **Visible browser (`show_browser: true`)**: Operate the open Chrome window,
  complete the challenge yourself, and run the same search again.
- **Hidden browser (`show_browser: false`)**: The hidden challenge cannot be
  completed from the tool. Wait a few minutes and retry, or switch to
  `show_browser: true` and restart.

---

## Safety and security design

- **Local-destination blocking**: `localhost` and private IP addresses are
  blocked; only public web pages can be fetched.
- **Credential-bearing URL rejection**: URLs containing passwords or recognizable
  secret patterns are rejected.
- **Automatic process cleanup**: Chrome is assigned to a Windows Job Object and
  related processes are cleaned up when the MCP client exits. Startup stops if
  the assignment cannot be made safely.
- **Profile isolation**: Chrome runs with a per-process temporary profile,
  separate from your normal browsing history and saved passwords.
- **Orphan recovery**: After an abnormal exit, recorded Chrome processes are
  recovered by checking their process ID and start time. Unrelated Chrome
  processes are not terminated by name alone.

This server does not bypass authentication or CAPTCHA challenges. It is not a
general remote browser-control API and does not expose arbitrary page-context
JavaScript execution.

---

## Environment and storage requirements

- **Supported OS**: Windows 10 or Windows 11 (64-bit)
- **Python**: 3.10 or newer
- **Browser**: Google Chrome, Google Chrome for Testing, or Chromium
- **Approximate disk usage**:
  - Python virtual environment, including dependencies: about 100 MB
  - Package source: less than 1 MB
  - Chrome for Testing, when installed locally: about 485 MB
  - Total: about 600 MB

If Google Chrome is already installed, the Chrome for Testing space is not
needed. Actual size varies with the Python version and Chrome release.

---

## Updating

### Updating a local checkout

Review local changes before updating. Do not use `reset --hard`, because it can
discard work.

```powershell
git -C "C:\Users\YOU\chrome-web-mcp" fetch origin
git -C "C:\Users\YOU\chrome-web-mcp" pull --ff-only
& "C:\Users\YOU\chrome-web-mcp-windows\.venv\Scripts\python.exe" -m pip install -e "C:\Users\YOU\chrome-web-mcp-windows[test]"
```

Restart the MCP client after updating. If you start the server with `uv run`,
dependencies are synchronized on the next launch.

---

## Development and testing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and build
instructions.

To run tests that do not contact external services and should produce the same
results each time:

```bat
pytest -q -m "not live"
```

Tests marked `live` contact Google and can be affected by network conditions or
CAPTCHA challenges.

---

## Origins and acknowledgements

This repository is an independent Windows-focused fork of
[kuraneko1/chrome-web-mcp](https://github.com/kuraneko1/chrome-web-mcp). It began
with browser-backed web processing from
[antirez/ds4](https://github.com/antirez/ds4). The upstream license notice
credits `The ds4.c authors` and `The ggml authors`; that notice is preserved in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The Git history records the following authors and stages of development:

- `kuraneko1`: Established the initial `chrome-web-mcp` structure and browser
  runtime.
- `ryu`: Expanded the MCP search and page-fetch features, Markdown shaping,
  configuration, and documentation.
- `_ryu15_`: Clarified the platform support boundary and added the DS4 license
  notice.

The current Windows edition is a full redesign and reimplementation for Python
and MCP. It is not a simple redistribution of the original code. It adds and
reworks search, public-URL fetching, readable content extraction, safe
connection checks, rate limiting, and Windows Chrome startup and cleanup.
We thank the earlier contributors and the ds4.c and ggml contributors for the
foundation and ideas behind this work.

For questions and bug reports about this Windows edition, please use this
repository's [Issues](https://github.com/kotao-boop/chrome-web-mcp-windows/issues).
Please do not send Windows-specific support requests to the upstream project.

---

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for the
full text. The DS4-derived notice is kept separately in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

For security reports, see [SECURITY.md](SECURITY.md). Do not include real
passwords, cookies, or other secrets in public issues.
