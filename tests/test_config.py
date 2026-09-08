"""Config-file loading: defaults, overrides, and per-key fallback on bad values."""

import json

from chrome_web_mcp import server


def write_config(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_defaults_when_no_config_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CW_CONFIG", str(tmp_path / "missing.json"))
    cfg = server._load_config()
    assert cfg == server._CONFIG_DEFAULTS


def test_full_override(monkeypatch, tmp_path):
    path = write_config(tmp_path, {
        "show_browser": True,
        "hl": "en",
        "gl": "us",
        "limit": 3,
        "char_limit": 5000,
        "format": "text",
        "min_delay": 2.0,
        "max_delay": 4.0,
    })
    monkeypatch.setenv("CW_CONFIG", path)
    cfg = server._load_config()
    assert cfg == {
        "show_browser": True,
        "hl": "en",
        "gl": "us",
        "limit": 3,
        "char_limit": 5000,
        "format": "text",
        "min_delay": 2.0,
        "max_delay": 4.0,
    }


def test_bad_values_fall_back_per_key(monkeypatch, tmp_path, capsys):
    path = write_config(tmp_path, {
        "show_browser": "yes",
        "hl": "toolongcode!",
        "limit": 99,
        "char_limit": "lots",
        "format": "pdf",
        "min_delay": 5.0,
        "max_delay": 1.0,
        "bogus_key": 1,
    })
    monkeypatch.setenv("CW_CONFIG", path)
    cfg = server._load_config()
    assert cfg == server._CONFIG_DEFAULTS
    assert "bogus_key" in capsys.readouterr().err


def test_non_finite_delays_fall_back_to_defaults(monkeypatch, tmp_path, capsys):
    path = write_config(tmp_path, {"min_delay": float("nan"), "max_delay": float("inf")})
    monkeypatch.setenv("CW_CONFIG", path)
    assert server._load_config() == server._CONFIG_DEFAULTS
    assert "non-negative number" in capsys.readouterr().err


def test_broken_json_falls_back_to_defaults(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CW_CONFIG", str(path))
    assert server._load_config() == server._CONFIG_DEFAULTS


def test_default_path_used_when_cw_config_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("CW_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    conf_dir = tmp_path / "chrome-web-mcp"
    conf_dir.mkdir(parents=True)
    (conf_dir / "config.json").write_text(
        json.dumps({"show_browser": True}), encoding="utf-8")
    assert server._load_config()["show_browser"] is True


def test_env_display_mode_wins_over_config(monkeypatch, tmp_path):
    # BrowserRuntime uses CW_DISPLAY_MODE when set, else the config value.
    monkeypatch.setenv("CW_DISPLAY_MODE", "headless")
    monkeypatch.setenv("CW_CONFIG", write_config(tmp_path, {"show_browser": True}))
    assert server.BrowserRuntime().display_mode == "headless"
