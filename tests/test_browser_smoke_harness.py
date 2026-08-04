from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = (ROOT / "tests" / "browser_smoke.py").read_text(encoding="utf-8")


def test_browser_smoke_stays_on_its_isolated_interpreter_and_agent_dir():
    assert 'os.makedirs(no_agent_dir, exist_ok=True)' in SMOKE
    assert 'os.path.join(no_agent_dir, "run_agent.py")' in SMOKE
    assert '"HERMES_WEBUI_PYTHON": sys.executable' in SMOKE
    assert '"HERMES_WEBUI_AGENT_DIR": no_agent_dir' in SMOKE
    assert '"HERMES_WEBUI_PASSWORD": ""' in SMOKE
    assert '"HERMES_WEBUI_DEFAULT_WORKSPACE": workspace_dir' in SMOKE
    assert '"HERMES_WEBUI_PLUGINS_DIR": plugins_dir' in SMOKE
    assert 'os.path.join(state_dir, "no-agent")' in SMOKE
