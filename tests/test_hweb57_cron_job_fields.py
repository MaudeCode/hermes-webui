"""HWEB-57: cron continuity, monitor, script, and reasoning-effort form fields.

The WebUI calls ``cron.jobs`` directly, so it has to perform the same interface
merge the agent's ``cronjob`` tool does: ``monitor`` is one string that the
store splits into ``monitor_script``/``monitor_url``, and ``continuity`` is
sugar for a ``context_from`` list containing ``"self"``. Neither is a key on the
stored job dict.
"""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PANELS_JS = (REPO / "static" / "panels.js").read_text(encoding="utf-8")
I18N_JS = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")

NEW_FIELDS = ("script", "no_agent", "monitor", "continuity", "context_from", "reasoning_effort")


class _JSONHandler:
    def __init__(self):
        self.status = None
        self.response_headers = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass


def _payload(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def _stub_cron_jobs(monkeypatch, calls, existing=None):
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")

    def create_job(**kwargs):
        calls.append(("create", kwargs))
        return {"id": "job-1", **kwargs}

    def update_job(job_id, updates):
        calls.append(("update", job_id, updates))
        return {"id": job_id, **updates}

    cron_jobs.create_job = create_job
    cron_jobs.update_job = update_job
    cron_jobs.get_job = lambda job_id: dict(existing or {})
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)
    return cron_jobs


def _create(monkeypatch, body, existing=None):
    import api.routes as routes

    calls = []
    _stub_cron_jobs(monkeypatch, calls, existing)
    handler = _JSONHandler()
    routes._handle_cron_create(handler, body)
    assert handler.status == 200, _payload(handler)
    return next(kwargs for kind, kwargs in ((c[0], c[1]) for c in calls) if kind == "create")


def _update(monkeypatch, body, existing=None):
    import api.routes as routes

    calls = []
    _stub_cron_jobs(monkeypatch, calls, existing)
    handler = _JSONHandler()
    routes._handle_cron_update(handler, body)
    assert handler.status == 200, _payload(handler)
    return next(c[2] for c in calls if c[0] == "update")


BASE = {"prompt": "ping", "schedule": "every 1h"}


# --- create forwarding -------------------------------------------------------


def test_create_forwards_each_in_scope_key(monkeypatch):
    kwargs = _create(
        monkeypatch,
        {
            **BASE,
            "script": "collect.sh",
            "context_from": ["abc123"],
            "reasoning_effort": "high",
            "repeat": 3,
            "continuity": True,
        },
    )
    assert kwargs["script"] == "collect.sh"
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["repeat"] == 3
    # continuity folds "self" into the reference list rather than being stored.
    assert kwargs["context_from"] == ["abc123", "self"]
    assert "continuity" not in kwargs


def test_create_omits_absent_keys_so_agent_defaults_apply(monkeypatch):
    kwargs = _create(monkeypatch, dict(BASE))
    for key in (*NEW_FIELDS, "repeat", "monitor_script", "monitor_url"):
        assert key not in kwargs, key


def test_create_splits_monitor_url_and_script(monkeypatch):
    url = _create(monkeypatch, {**BASE, "monitor": "https://example.com/status"})
    assert url["monitor_url"] == "https://example.com/status"
    assert url["monitor_script"] == ""
    assert "monitor" not in url

    script = _create(monkeypatch, {**BASE, "monitor": "check-feed.sh"})
    assert script["monitor_script"] == "check-feed.sh"
    assert script["monitor_url"] == ""


def test_create_forwards_no_agent_with_script(monkeypatch):
    kwargs = _create(monkeypatch, {**BASE, "no_agent": True, "script": "watchdog.sh"})
    assert kwargs["no_agent"] is True
    assert kwargs["script"] == "watchdog.sh"


# --- clearing semantics ------------------------------------------------------


def test_update_clearing_semantics_survive_the_webui(monkeypatch):
    updates = _update(
        monkeypatch,
        {
            "job_id": "job-1",
            "monitor": "",
            "script": "",
            "context_from": [],
            "continuity": False,
        },
    )
    assert updates["monitor_script"] == ""
    assert updates["monitor_url"] == ""
    assert updates["script"] == ""
    assert updates["context_from"] == []
    assert "monitor" not in updates
    assert "continuity" not in updates


def test_continuity_only_update_keeps_other_chained_jobs(monkeypatch):
    updates = _update(
        monkeypatch,
        {"job_id": "job-1", "continuity": True},
        existing={"id": "job-1", "context_from": ["abc123"]},
    )
    assert updates["context_from"] == ["abc123", "self"]

    off = _update(
        monkeypatch,
        {"job_id": "job-1", "continuity": False},
        existing={"id": "job-1", "context_from": ["abc123", "self"]},
    )
    assert off["context_from"] == ["abc123"]


def test_update_never_forwards_bare_repeat_integer(monkeypatch):
    # The store keeps repeat as a {"times", "completed"} record once the job
    # exists; a bare integer would clobber the run counter.
    updates = _update(monkeypatch, {"job_id": "job-1", "repeat": 5})
    assert "repeat" not in updates


# --- read-back ---------------------------------------------------------------


def test_cron_job_for_api_round_trips_each_key():
    import api.routes as routes

    payload = routes._cron_job_for_api(
        {
            "id": "job-1",
            "script": "collect.sh",
            "no_agent": False,
            "monitor_url": "https://example.com/status",
            "monitor_script": None,
            "context_from": ["abc123", "self"],
            "reasoning_effort": "high",
            "repeat": {"times": 3, "completed": 1},
        }
    )
    assert payload["script"] == "collect.sh"
    assert payload["no_agent"] is False
    assert payload["reasoning_effort"] == "high"
    assert payload["repeat"] == {"times": 3, "completed": 1}
    assert payload["context_from"] == ["abc123", "self"]
    assert payload["monitor"] == "https://example.com/status"
    assert payload["continuity"] is True


def test_cron_job_for_api_projects_script_monitor_and_absent_continuity():
    import api.routes as routes

    payload = routes._cron_job_for_api(
        {"id": "job-2", "monitor_script": "check-feed.sh", "context_from": ["abc123"]}
    )
    assert payload["monitor"] == "check-feed.sh"
    assert payload["continuity"] is False


# --- form contract -----------------------------------------------------------


def test_form_submits_monitor_as_a_string_not_a_boolean():
    # A boolean would be silently wrong against the agent's schema, which reads
    # the value's shape to pick the http(s) vs script transport.
    assert "const monitor=monitorEl?String(monitorEl.value).trim():'';" in PANELS_JS
    assert "if(monitor)body.monitor=monitor;" in PANELS_JS
    assert 'type="text" id="cronFormMonitor"' in PANELS_JS


def test_form_never_submits_monitor_and_no_agent_both_set():
    assert "if(isNoAgent && monitor){errEl.textContent=t('cron_monitor_no_agent_conflict')" in PANELS_JS
    # The submit is blocked rather than the field locked, so a monitor left over
    # from before the script-only toggle stays clearable in place.
    assert "id=\"cronFormMonitor\" value=\"${esc(monitor || '')}\" autocomplete=\"off\" placeholder" in PANELS_JS


def test_create_body_is_unchanged_when_no_new_field_is_set():
    body = PANELS_JS[PANELS_JS.index("    const body={schedule,prompt,deliver,profile"):]
    body = body[: body.index("const res = await api('/api/crons/create'")]
    for key in ("script", "no_agent", "monitor", "continuity", "context_from", "reasoning_effort", "repeat"):
        # every new key is added only under a truthiness guard
        assert f"body.{key}=" in body, key
    assert "if(script)body.script=script;" in body
    assert "if(isNoAgent)body.no_agent=true;" in body
    assert "if(continuity)body.continuity=true;" in body
    assert "if(contextFrom.length)body.context_from=contextFrom;" in body
    assert "if(reasoningEffort)body.reasoning_effort=reasoningEffort;" in body
    assert "if(repeatRaw)body.repeat=Number(repeatRaw);" in body


def test_every_new_label_has_an_english_i18n_entry():
    for key in (
        "cron_advanced_label",
        "cron_no_agent_label",
        "cron_no_agent_hint",
        "cron_no_agent_script_required",
        "cron_monitor_label",
        "cron_monitor_hint",
        "cron_monitor_no_agent_hint",
        "cron_monitor_no_agent_conflict",
        "cron_continuity_label",
        "cron_continuity_hint",
        "cron_context_from_label",
        "cron_context_from_hint",
        "cron_context_from_empty_hint",
        "cron_reasoning_effort_label",
        "cron_reasoning_effort_default",
        "cron_reasoning_effort_hint",
        "cron_reasoning_effort_no_agent_hint",
        "cron_repeat_label",
        "cron_repeat_hint",
        "cron_script_context_hint",
    ):
        assert f"{key}:" in I18N_JS, key
        assert f"t('{key}')" in PANELS_JS, key


def test_reasoning_effort_options_match_the_canonical_levels():
    assert (
        "const CRON_REASONING_EFFORTS = ['none', 'minimal', 'low', 'medium', "
        "'high', 'xhigh', 'max', 'ultra'];" in PANELS_JS
    )
