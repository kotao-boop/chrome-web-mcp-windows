# Security Policy

`chrome-web-mcp` is a Windows-first, browser-backed MCP server. It accepts
public HTTP(S) URLs and starts a separate Chrome process for each server
process. It is not intended to bypass authentication, CAPTCHAs, paywalls, or
site access controls.

## Reporting a vulnerability

Please do not publish credentials, private URLs, full exploit details, or
step-by-step reproduction instructions in a public issue.

The preferred channel is GitHub's private security advisory flow: open the
repository's **Security** tab and choose **Report a vulnerability**, if that
feature is enabled for the repository. If private reporting is unavailable,
open a public issue with only a short, non-sensitive description and ask for a
private contact channel. Remove real tokens, cookies, account data, and
personal information from all reports.

## Scope

Reports are especially useful for issues involving:

- requests reaching private networks or cloud metadata endpoints;
- credential-bearing URLs or secrets being exposed in tool output or logs;
- Chrome or child processes surviving after the MCP client exits;
- unsafe handling of downloaded or rendered page content; or
- Windows-only behavior that breaks the isolation and cleanup guarantees.

## Supported versions

The latest published release and the current default branch are the supported
versions. Please include the version, Windows version, Python version, Chrome
version, MCP client, and a minimal reproduction that contains no secrets.
