# chrome-web-mcp

A stdio Model Context Protocol server that exposes two focused browser tools:

- `google_search` — Google search through a JavaScript-rendered Chrome instance.
- `fetch_url` — JavaScript-rendered readable text extraction for public HTTP(S) URLs.

The server is designed to be configured by any MCP client that can launch a
stdio command. It does not depend on Hermes Agent.

## Features

- JS-rendered Google search + public URL fetch through a real (non-headless)
  Chrome on a private Xvfb display — harder to bot-detect than `--headless`.
- Shaped markdown by default (`trafilatura` + `html2text`, pure-Python, no
  extra service), with full-text fallback and follow-up link targets.
- Language/region hints (`hl`/`gl`) for reproducible JA/EN results.
- Parallel-safe: concurrent searches and fetches serialize only where the
  browser lifecycle requires it; each fetch uses its own tab.
- Fail-closed fetching: private networks, metadata hosts, and
  credential-bearing URLs are blocked, including post-redirect targets.
- Shared SQLite pacing for Google searches across MCP processes.
- `health_check` for display/browser/queue/CAPTCHA observability.
- Linux only (Xvfb/Xephyr, `fcntl`, process groups).

## Requirements

- Python 3.10 or newer
- Google Chrome, Google Chrome for Testing, or Chromium
- `Xvfb` on Linux
- `xpra` on Linux if interactive CAPTCHA recovery is desired

The Python dependencies are installed with the package. Chrome and Xvfb remain
host prerequisites because they are external browser processes.

Non-headless Chrome on Xvfb is intentional: `--headless` is easier to
bot-detect, so Xvfb is kept as a requirement even though it is heavier.

## Platform support

Linux only. Windows and macOS are not supported: this server depends on
`Xvfb`/`Xephyr`, Chromium with `--ozone-platform=x11`, `fcntl.flock`, and
process-group signaling (`killpg`), none of which work as-is on Windows.
A Windows/macOS port would need headless Chrome plus a different locking
scheme. Docker helps only on a Linux host with an X server for `xephyr` mode.

## Linux quickstart (Debian/Ubuntu, copy-paste)

```bash
# 1. System dependencies (headless xvfb mode needs only these two)
sudo apt update && sudo apt install -y chromium xvfb
which chromium || which google-chrome || which chromium-browser
which Xvfb
python3 --version  # 3.10+

# Optional: visible-window mode only
# sudo apt install -y xserver-xephyr

# 2. Install the package (either one)
python3 -m pip install -e '.'
# or: python -m pip install chrome_web_mcp-0.2.0-py3-none-any.whl
```

MCP handshake check (`TOOLS: ['fetch_url', 'google_search', 'health_check']` expected):

```bash
uv run python -c "
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    params = StdioServerParameters(command='uv', args=['run','--directory','/absolute/path/to/chrome-web-mcp','chrome-web-mcp'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print('TOOLS:', [t.name for t in (await session.list_tools()).tools])
asyncio.run(main())
"
```

opencode (`~/.config/opencode/opencode.json`, Linux path example):

```json
{
  "mcp": {
    "chrome-web": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/home/user/projects/chrome-web-mcp", "chrome-web-mcp"],
      "enabled": true
    }
  }
}
```

Notes:

- Replace `/absolute/path/to/chrome-web-mcp` with your checkout path.
- `CW_CHROME=/usr/bin/chromium` only if auto-detection misses your binary.
- Call `google_search` and `fetch_url` sequentially, not in parallel:
  parallel calls from one server process can hit the profile lock
  (`Another chrome-web MCP instance owns this profile`).
- `xephyr` mode needs a real desktop `DISPLAY` plus `xserver-xephyr`;
  on a headless host or over SSH without X forwarding it will not start.
  Default `xvfb` mode needs no `DISPLAY`.

## Install and run

From a built wheel:

```bash
python -m pip install chrome_web_mcp-0.2.0-py3-none-any.whl
chrome-web-mcp
```

For a published package, an MCP client can let `uvx` install it on demand:

```json
{
  "mcpServers": {
    "chrome-web": {
      "command": "uvx",
      "args": ["--from", "chrome-web-mcp==0.2.0", "chrome-web-mcp"]
    }
  }
}
```

For a local checkout:

```json
{
  "mcpServers": {
    "chrome-web": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/chrome-web-mcp", "chrome-web-mcp"]
    }
  }
}
```

The same server can be registered in Hermes with YAML:

```yaml
mcp_servers:
  chrome-web:
    command: /absolute/path/to/python
    args:
      - -m
      - chrome_web_mcp
    timeout: 120
    connect_timeout: 60
    enabled: true
```

For a wheel installation, use the Python interpreter from the environment
where the wheel was installed. MCP clients should launch the process over
stdio and must not add shell-specific quoting around the arguments.

## Docker

The image bundles Python, dependencies, Chromium, Xvfb (plus Xephyr/xdotool
for visible-window mode), so users only need Docker:

```bash
docker build -t chrome-web-mcp .
```

```json
{
  "mcpServers": {
    "chrome-web": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "chrome-web-mcp"]
    }
  }
}
```

Visible-window mode needs the host X socket (Linux with X11):

```bash
docker run -i --rm -e DISPLAY=$DISPLAY -e CW_DISPLAY_MODE=xephyr \
  -v /tmp/.X11-unix:/tmp/.X11-unix chrome-web-mcp
```

Image size is about 1.5 GB (mostly Chromium and fonts).

## Install size (rough, Debian host)

- Python environment (`.venv`, incl. trafilatura/html2text): ~100 MB
- This package source: under 1 MB
- Chromium set: ~485 MB
- Xvfb + x11-utils: ~5 MB
- Total: ~600 MB, dominated by Chromium. No new system packages were added
  for markdown shaping (pure-Python dependencies only).

## Tools

Workflow: first `google_search`, then `fetch_url` on interesting result URLs
for full text. `health_check` reports server state without starting a browser.

### `google_search`

Input:

```json
{"query": "search terms", "limit": 5, "hl": "ja", "gl": "jp"}
```

`limit` is an integer from 1 to 20. Results are returned as structured JSON
with `title`, `url`, `description`, and `position` fields, plus `waited_ms`
(the shared rate-limiter queue wait). `hl`/`gl` are optional Google language
(`ja`/`en`) and region (`jp`/`us`) hints; defaults preserve Japanese results.
`query` is required, max 512 chars. Searches run one at a time per process
and are paced across processes (see Runtime configuration).

### `fetch_url`

Input:

```json
{"url": "https://example.com", "char_limit": 15000, "format": "markdown"}
```

Only public `http://` and `https://` URLs without embedded credentials are
accepted. Localhost, private IP ranges, metadata hosts, and non-public DNS
resolutions are rejected. Redirect destinations are validated before they are
used. `char_limit` is an integer from 100 to 200000. Text is cut at a
sentence boundary when possible. Returns `requested_url`, `final_url`,
`redirected`, `total_chars`, and `truncated` alongside `title` and content
(`url` mirrors `final_url` for compatibility). `format` defaults to
`markdown`: shaped readable markdown with boilerplate removed
(`trafilatura`, `html2text` fallback). The response reports `formatted: true`
and the `extraction` method, so agents can tell it was shaped — if content
looks missing, retry with `format: "text"` for the full rendered text.
`format: "links"` adds follow-up link targets. Concurrent fetches are
parallel-safe; each uses its own tab. `url` is required, max 2048 chars.
Failures return `{"success": false, "error": "..."}` (plus
`"captcha_required": true` for Google challenges). This shaping reuses the same
`trafilatura` + `html2text` approach as a self-hosted jina-compatible Reader,
without needing the extra service.

### `health_check`

Input: `{}` (no arguments).

Returns `display_mode`, browser/process liveness, the rate-limiter queue wait,
its `min`/`max` delays, and the last CAPTCHA time.

## Runtime configuration

Optional environment variables:

- `CW_CHROME` — explicit Chrome/Chromium executable path.
- `CW_PROFILE_DIR` — explicit browser profile directory. By default, each
  server process uses an isolated per-PID temporary profile.
- `CW_LOCK_PATH` — explicit lock-file path when `CW_PROFILE_DIR` is set.
- `CW_RATE_LIMIT_DB` — shared SQLite path for the Google-search start-slot
  queue. By default it is `/tmp/chrome-web-mcp/search-rate-limit.sqlite3`, so
  separate MCP processes of the same user share one limiter.
- `CW_MIN_DELAY` / `CW_MAX_DELAY` — randomized gap (seconds) between Google
  search starts. Defaults `1.0` / `2.5`.
- `CW_DISPLAY_MODE` — `xvfb` (default) runs Chrome on a private, fully hidden
  display. `xephyr` runs Chrome inside a nested `Xephyr` window titled
  `chrome-web-mcp` on your desktop: visible, minimizable, and movable, but
  tool calls can never pop a window to the front outside of it. Requires
  `Xephyr` (`xserver-xephyr`) and a user `DISPLAY`. Recommended when you want
  to watch searches or solve a CAPTCHA by hand.
- When `CW_DISPLAY_MODE=xephyr`, the server preserves an explicit `XAUTHORITY`
  or automatically discovers Mutter's `.mutter-Xwaylandauth.*` file under
  `XDG_RUNTIME_DIR`, then falls back to `~/.Xauthority`. This lets stdio MCP
  clients that filter their inherited environment still connect to the user's
  Xwayland display.
- `CW_XPRA_EXPOSE` — set to `1` to re-enable automatic Xpra attach when a
  CAPTCHA appears. Off by default: automatic attach once crashed the desktop
  session, so the server only reports the CAPTCHA and leaves the browser
  where it is.

The default per-process profile prevents separate MCP clients from contending
for one Chrome profile. Do not share a profile between live server processes
unless that is intentional.

Google-search calls also reserve a slot in the shared SQLite queue. Separate
MCP processes therefore start searches one at a time with a 1.0–2.5 second
randomized gap. A reserved slot is not retried automatically if Google returns
an error; the tool returns the error to the MCP client. `fetch_url` is not put
through this Google-search queue.

## Security and scope

- The server exposes no arbitrary page-context JavaScript tool.
- Fetching is fail-closed for private networks and credential-bearing URLs.
- Each server owns and cleans up its Chrome and Xvfb process groups.
- Chrome is explicitly forced onto the private X11/Xvfb display, even when the
  host desktop session uses Wayland; the user desktop should not be surfaced.
- SIGTERM and SIGINT trigger browser cleanup before exit.
- Google result URLs are normalized and deduplicated before returning.

This package does not bypass authentication or CAPTCHA challenges. It is a
browser-backed search/fetch MCP server, not a general remote browser-control
API.

When Google presents a CAPTCHA during `google_search`, the server returns
`captcha_required: true`. In `xephyr` display mode, solve the challenge in the
`chrome-web-mcp` window on your desktop, then retry the same search. In the
default `xvfb` mode, wait a while and retry. Automatic Xpra attach is disabled
unless `CW_XPRA_EXPOSE=1` is set, because it once crashed the desktop session.

## Development and verification

```bash
python -m pip install -e '.[test]'
pytest -q
python -m build
```

The end-to-end tests exercise the real stdio MCP handshake, `tools/list`,
Google search, public URL fetching, and shutdown cleanup. They require Chrome,
Xvfb, and network access.
