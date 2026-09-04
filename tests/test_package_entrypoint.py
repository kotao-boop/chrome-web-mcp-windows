import inspect


def test_package_exposes_sync_console_entrypoint():
    from chrome_web_mcp.server import run

    assert callable(run)
    assert not inspect.iscoroutinefunction(run)


def test_package_has_mcp_server_module():
    from chrome_web_mcp import server

    assert server.app.name == "chrome-web"
