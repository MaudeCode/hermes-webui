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

def _function_body(name: str) -> str:
    """Return the body of a top-level function in panels.js."""
    start = PANELS_JS.index(f"function {name}(")
    brace = PANELS_JS.index("{", PANELS_JS.index(")", start))
    depth = 0
    for idx in range(brace, len(PANELS_JS)):
        if PANELS_JS[idx] == "{":
            depth += 1
        elif PANELS_JS[idx] == "}":
            depth -= 1
            if depth == 0:
                return PANELS_JS[brace + 1 : idx]
    raise AssertionError(f"{name} body did not terminate")


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


# --- script-only creates (Codex P1) ------------------------------------------


def test_script_only_create_is_not_rejected_for_an_empty_prompt(monkeypatch):
    # The script-only form omits the prompt textarea entirely, so it posts
    # prompt: ''. A blanket require(prompt) would 400 every script-only create
    # before no_agent ever reached create_job.
    kwargs = _create(
        monkeypatch,
        {"prompt": "", "schedule": "every 1h", "no_agent": True, "script": "watchdog.sh"},
    )
    assert kwargs["prompt"] == ""
    assert kwargs["no_agent"] is True
    assert kwargs["script"] == "watchdog.sh"


def test_skills_only_create_is_not_rejected_for_an_empty_prompt(monkeypatch):
    kwargs = _create(monkeypatch, {"prompt": "", "schedule": "every 1h", "skills": ["triage"]})
    assert kwargs["skills"] == ["triage"]


def test_create_still_rejects_a_payload_with_no_prompt_script_or_skills(monkeypatch):
    import api.routes as routes

    _stub_cron_jobs(monkeypatch, [])
    handler = _JSONHandler()
    routes._handle_cron_create(handler, {"prompt": "", "schedule": "every 1h"})
    assert handler.status == 400
    assert "prompt" in _payload(handler)["error"]


def test_create_still_requires_a_schedule(monkeypatch):
    import api.routes as routes

    _stub_cron_jobs(monkeypatch, [])
    handler = _JSONHandler()
    routes._handle_cron_create(handler, {"prompt": "ping"})
    assert handler.status == 400
    assert "schedule" in _payload(handler)["error"]


# --- script-only profile snapshots (Codex round 2) ----------------------------


def test_script_only_create_skips_profile_model_snapshot_resolution(monkeypatch):
    # A script-only job never calls a model, so an unresolvable profile LLM
    # config must not 400 an otherwise valid create.
    import api.routes as routes

    def _explode(*a, **kw):
        raise AssertionError("no_agent create must not resolve model snapshots")

    monkeypatch.setattr(routes, "_normalize_cron_profile_value", lambda v: v or None)
    assert (
        routes._selected_profile_snapshot_updates(
            "work", provider=None, model=None, no_agent=True
        )
        == {}
    )
    # ... and the create path passes the request's no_agent through to it.
    seen = {}
    monkeypatch.setattr(
        routes,
        "_selected_profile_snapshot_updates",
        lambda profile, **kw: seen.update(kw) or {},
    )
    _create(
        monkeypatch,
        {
            "prompt": "",
            "schedule": "every 1h",
            "profile": "work",
            "no_agent": True,
            "script": "watchdog.sh",
        },
    )
    assert seen["no_agent"] is True


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


def test_mode_toggle_falls_back_to_the_last_rendered_prompt():
    # The script-only re-render drops the prompt textarea; a DOM-only snapshot
    # would read '' and eat the user's prompt on the way back (Codex P2).
    assert "let _cronFormRendered = null;" in PANELS_JS
    assert "_cronFormRendered = { prompt, script, monitor" in PANELS_JS
    assert "const last = _cronFormRendered || {};" in PANELS_JS
    assert "prompt: val('cronFormPrompt', 'prompt')," in PANELS_JS


def test_context_from_picker_excludes_foreign_profile_jobs():
    # Cross-profile jobs arrive read_only; their bare IDs cannot be resolved in
    # the active profile's cron store (Codex P2), matching _cronList's own guard.
    assert "!job.read_only && job.id !== editingId" in PANELS_JS


def test_duplicate_carries_a_finite_repeat_limit_into_the_create_form():
    # The store keeps repeat as {times, completed}; only `times` is the limit
    # the user set, and an unlimited job has times == null (Codex round 2).
    assert "repeat: (job.repeat && job.repeat.times != null) ? job.repeat.times : ''," in PANELS_JS


def test_mode_toggle_preserves_selects_that_have_not_loaded_yet():
    # The model and delivery selects are filled by an async fetch and show a
    # placeholder until it lands. A toggle inside that window must not snapshot
    # a cleared model override or a defaulted 'local' target (Codex round 3).
    assert "_cronFormRendered = { prompt, script, monitor, deliver, model, provider };" in PANELS_JS
    assert "const modelLoaded = !!(modelEl && modelEl.dataset.loaded === '1');" in PANELS_JS
    assert "const delivLoaded = !!(delivEl && !delivEl.querySelector('option[value=\"\"][disabled]'));" in PANELS_JS
    assert "deliver: (delivLoaded ? delivEl.value : last.deliver) || 'local'," in PANELS_JS
    assert "model: modelLoaded ? modelEl.value : (last.model || '')," in PANELS_JS


def test_repeat_guard_rejects_fractional_counts():
    # The header Save button is onclick="saveCronForm()" (index.html), so it
    # bypasses the input's step="1" constraint validation (Codex round 7).
    body = _function_body("saveCronForm")
    assert "Number.isInteger(Number(repeatRaw)) && Number(repeatRaw)>=1" in body


def test_save_uses_the_preserved_model_pin_while_the_picker_reloads():
    # Counterpart of the delivery fix: omitting model/provider while the picker
    # reloads silently drops a pin the user set before the toggle.
    body = _function_body("saveCronForm")
    assert "} else if (modelEl && _cronFormRendered && _cronFormRendered.model) {" in body
    assert "} else if (_cronFormRendered && _cronFormRendered.model) {" in body
    assert body.count("_cronModelBareName(_cronFormRendered.model, _cronFormRendered.provider)") == 2


def test_save_uses_the_preserved_delivery_target_while_the_select_loads():
    # _cronFormValues alone was not enough: saveCronForm read the select
    # directly, so a duplicate saved during the load window sent deliver: ''
    # and the server defaulted it to local (Codex round 6).
    body = _function_body("saveCronForm")
    assert "const delivLoaded=!!(delivEl && !delivEl.querySelector('option[value=\"\"][disabled]'));" in body
    assert "const deliver=(delivLoaded ? delivEl.value : ((_cronFormRendered||{}).deliver))||'local';" in body


def test_advanced_section_opens_for_a_configured_script():
    # A script is an advanced value too, so an agent job carrying one must not
    # open with the section collapsed and the script hidden (Codex round 3).
    assert "const isOpen = !!(isNoAgent || script || monitor || continuity" in PANELS_JS


def test_mode_toggle_rebinds_the_skill_picker():
    # The re-render replaces #cronFormSkillSearch, so the listener bound to the
    # old element goes with it and the picker is silently dead (Codex round 5).
    assert "if ($('cronFormSkillSearch')) _bindCronSkillPicker();" in PANELS_JS


def test_reasoning_effort_options_match_the_canonical_levels():
    # The cron path validates through the AGENT's
    # hermes_constants.VALID_REASONING_EFFORTS (imported by cron/jobs.py's
    # _normalize_reasoning_effort), whose grammar is
    # none|minimal|low|medium|high|xhigh|max|ultra. That is deliberately wider
    # than the WebUI's own api/config.py mirror, which is the chat surface's
    # copy and does not gate cron.
    assert (
        "const CRON_REASONING_EFFORTS = ['none', 'minimal', 'low', 'medium', "
        "'high', 'xhigh', 'max', 'ultra'];" in PANELS_JS
    )
