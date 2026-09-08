# chrome-web-mcp Configuration

`config.json` must contain valid JSON. JSON does not support comments, so the
explanations for each setting are in this file instead of inside the JSON.

On Windows, copy both files to the user's configuration directory:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\chrome-web-mcp"
Copy-Item examples\config.json, examples\config.md "$env:APPDATA\chrome-web-mcp\"
```

The Windows edition automatically reads `%APPDATA%\chrome-web-mcp\config.json`.
Use the `CW_CONFIG` environment variable when the file is somewhere else.

The file is per user. You do not need to change the server code for a
different language or region.

## Windows desktop and headless environments

On a normal Windows desktop, set this when you want a real Chrome window kept
out of sight:

```json
{ "show_browser": false }
```

On Windows this uses an off-screen native Chrome window (not `--headless`).
New Chrome windows are hidden continuously, but a hidden CAPTCHA or permission
dialog is not visible to the user. The built-in default is `true` for desktop
use.

For a Windows service, CI runner, scheduled task, SSH session, or any machine
without an interactive desktop, set `CW_DISPLAY_MODE=headless` in the MCP
client's environment. This environment variable takes priority over the JSON
setting and selects Chrome's `--headless=new` mode.

## Settings

### `show_browser`

Controls whether the browser window is visible:

- `true`: visible browser. Windows uses a normal Chrome window.
- `false`: hidden browser. Windows uses an off-screen native window and a small
  watcher that hides windows created later by Chrome.

No separate display server is required. The built-in default is
`true`, so use `false` on an interactive desktop when you do not want a window;
use `CW_DISPLAY_MODE=headless` when there is no desktop session.

### `hl` and `gl`

These are Google search parameters, not passwords or model settings. They are
independent settings:

- `hl`: Google interface language. `ja` means Japanese; `en` means English.
- `gl`: Google result region. `jp` means Japan; `us` means the United States.

Common combinations are:

```json
{ "hl": "ja", "gl": "jp" }
```

Japanese interface and Japan-oriented results. For English/US-oriented
results, use:

```json
{ "hl": "en", "gl": "us" }
```

If you omit these keys, the built-in defaults (`ja`/`jp`) or the values in
your config file are used. If a tool call explicitly includes `hl` or `gl`,
that call's values take priority over the config file.

### `limit`

Default maximum number of results returned by `google_search` when the tool
call does not provide `limit`. Allowed range: 1-20.

### `char_limit`

Default maximum number of characters returned by `fetch_url` when the tool call
does not provide `char_limit`. Allowed range: 100-200000.

### `format`

Default output format for `fetch_url`:

- `markdown`: readable markdown with boilerplate removed. Recommended.
- `text`: full rendered page text when markdown extraction looks incomplete.
- `links`: readable text plus follow-up link targets.

### `min_delay` and `max_delay`

Minimum and maximum number of seconds between Google search starts. The actual
delay is randomized in this range and shared across server processes to reduce
CAPTCHA risk. `max_delay` must be greater than or equal to `min_delay`.

## Precedence and restart

When the same setting is specified more than once, the order is:

1. Tool-call argument
2. Environment variable (`CW_DISPLAY_MODE`, `CW_MIN_DELAY`, or
   `CW_MAX_DELAY`)
3. `config.json`
4. Built-in default

Restart the MCP client after changing the file. Values are loaded when the
server process starts, not on every tool call.
