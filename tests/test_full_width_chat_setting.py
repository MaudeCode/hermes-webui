import json
import shutil
import subprocess
from pathlib import Path

import pytest

import api.config as config


ROOT = Path(__file__).parent.parent
BOOT_JS = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
I18N_JS = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")


def test_full_width_chat_is_opt_in_and_round_trips_as_boolean(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)

    assert config.load_settings()["full_width_chat"] is False
    saved = config.save_settings({"full_width_chat": True})

    assert saved["full_width_chat"] is True
    assert json.loads(settings_file.read_text(encoding="utf-8"))["full_width_chat"] is True
    assert config.load_settings()["full_width_chat"] is True
    assert "full_width_chat" in config._SETTINGS_BOOL_KEYS


def test_full_width_chat_control_is_wired_through_settings_and_boot():
    assert 'id="settingsFullWidthChat"' in INDEX_HTML
    assert "full_width_chat:" in PANELS_JS
    assert "settings.full_width_chat" in PANELS_JS
    assert "s.full_width_chat" in BOOT_JS
    assert "_applyFullWidthChat" in BOOT_JS
    assert ':root[data-chat-width="full"]' in STYLE_CSS
    assert I18N_JS.count("settings_label_full_width_chat:") == 15
    assert I18N_JS.count("settings_desc_full_width_chat:") == 15


def test_apply_full_width_chat_updates_the_document_mode():
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for frontend behavior tests")
    start = BOOT_JS.index("function _applyFullWidthChat(")
    end = BOOT_JS.index("function ", start + 1)
    helper = BOOT_JS[start:end]
    script = f"""
      'use strict';
      const document = {{ documentElement: {{ dataset: {{}} }} }};
      {helper}
      _applyFullWidthChat(true);
      const enabled = document.documentElement.dataset.chatWidth;
      _applyFullWidthChat(false);
      const disabled = document.documentElement.dataset.chatWidth || null;
      process.stdout.write(JSON.stringify({{enabled, disabled}}));
    """
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"enabled": "full", "disabled": None}
