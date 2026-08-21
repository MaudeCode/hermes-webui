import json
import subprocess
import textwrap
from pathlib import Path

SESSIONS_JS = Path("static/sessions.js").read_text(encoding="utf-8")
MESSAGES_JS = Path("static/messages.js").read_text(encoding="utf-8")
CHANGELOG = Path("CHANGELOG.md").read_text(encoding="utf-8")


def _extract_function(source: str, signature: str) -> str:
    start = source.index(signature)
    # Look for the function body's opening brace, not an object literal inside
    # a default argument such as `options={}`.
    brace = source.index("{\n", start)
    depth = 0
    for idx in range(brace, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"Function body not closed for {signature}")


def _new_session_function() -> str:
    return _extract_function(SESSIONS_JS, "async function newSession")


def test_new_chat_syncs_model_picker_when_default_provider_changes_but_model_id_matches():
    fn = _new_session_function()
    assert "currentModelState" in fn
    assert "currentProvider" in fn
    assert "sessionProvider" in fn
    assert "sessionProvider !== currentProvider" in fn
    assert "_applyModelToDropdown(S.session.model,modelSel,sessionProvider)" in fn


def test_new_chat_inserts_session_model_when_static_picker_lacks_default():
    fn = _new_session_function()
    assert "sessionModelApplied" in fn
    assert "document.createElement('option')" in fn
    assert "opt.value=S.session.model" in fn
    assert "opt.dataset.provider=sessionProvider||''" in fn
    assert "modelSel.appendChild(opt)" in fn


def test_boot_model_hydration_prefers_active_session_over_persisted_model():
    boot_js = Path("static/boot.js").read_text(encoding="utf-8")
    marker = "const sessionModelState=S.session&&S.session.model"
    assert marker in boot_js
    session_branch = boot_js[boot_js.index(marker) : boot_js.index("if(S.session) syncTopbar();", boot_js.index(marker))]
    assert "_applyModelToDropdown(sessionModelState.model,$('modelSelect'),sessionModelState.model_provider||null)" in session_branch
    assert "savedState" in session_branch
    assert session_branch.index("sessionModelState") < session_branch.index("savedState"), (
        "active session model must be considered before localStorage so stale saved model preferences cannot override new chats"
    )


def test_hard_refresh_hydrates_saved_session_model_before_revealing_model_chip():
    boot_js = Path("static/boot.js").read_text(encoding="utf-8")
    load_marker = "await loadSession(saved, {preserveActiveInput:true});"
    assert load_marker in boot_js
    restore_end = "await checkInflightOnBoot(saved);"
    saved_restore = boot_js[boot_js.index(load_marker) : boot_js.index(restore_end, boot_js.index(load_marker))]
    assert "await _startBootModelDropdown();" in saved_restore
    assert saved_restore.index("await _startBootModelDropdown();") > saved_restore.index(load_marker)
    assert saved_restore.index("await _startBootModelDropdown();") < saved_restore.index("S._bootReady=true;"), (
        "hard refresh must hydrate/re-apply the active session model before S._bootReady lets syncModelChip display stale static HTML defaults"
    )


def test_pwa_new_chat_launch_does_not_block_first_paint_on_model_catalog():
    boot_js = Path("static/boot.js").read_text(encoding="utf-8")
    launch_marker = "if(_shouldStartFreshPwaChat(pwaLaunchAction,urlSession)){"
    assert launch_marker in boot_js
    launch_branch = boot_js[boot_js.index(launch_marker) : boot_js.index("const savedLocal=localStorage.getItem", boot_js.index(launch_marker))]
    assert "await newSession(true);" in launch_branch
    assert "await _startBootModelDropdown();" not in launch_branch
    assert "Promise.resolve(_startBootModelDropdown()).catch(()=>{})" in launch_branch
    assert launch_branch.index("Promise.resolve(_startBootModelDropdown()).catch(()=>{})") < launch_branch.index("S._bootReady=true;"), (
        "PWA new-chat launches should kick model hydration in the background before revealing the empty chat"
    )


def test_hard_refresh_injects_missing_active_session_model_option():
    boot_js = Path("static/boot.js").read_text(encoding="utf-8")
    marker = "if(!applied&&sessionModelState&&typeof _ensureModelOptionInDropdown==='function')"
    assert marker in boot_js
    branch = boot_js[boot_js.index(marker) : boot_js.index("else if(!applied&&!sessionModelState", boot_js.index(marker))]
    assert "_ensureModelOptionInDropdown(sessionModelState.model,$('modelSelect'),sessionModelState.model_provider||null)" in branch


def test_sync_topbar_preserves_missing_session_model_as_dropdown_option():
    ui_js = Path("static/ui.js").read_text(encoding="utf-8")
    assert "function _ensureModelOptionInDropdown" in ui_js
    sync_topbar = _extract_function(ui_js, "function syncTopbar")
    branch_start = sync_topbar.index("const applied=_applyModelToDropdown(currentModel,modelSel,S.session.model_provider||null);")
    session_model_branch = sync_topbar[branch_start:]
    assert "_ensureModelOptionInDropdown(currentModel,modelSel,S.session.model_provider||null)" in session_model_branch
    assert "const fallback=_applySessionModelFallback(modelSel);" in session_model_branch
    assert session_model_branch.index("_ensureModelOptionInDropdown(currentModel,modelSel,S.session.model_provider||null)") < session_model_branch.index("const fallback=_applySessionModelFallback(modelSel);"), (
        "active session models missing from the current catalog must be injected before fallback can select the static/default model"
    )


def test_new_chat_does_not_send_stale_dropdown_model_when_session_has_default_model():
    assert "model:S.session.model||$('modelSelect').value" in MESSAGES_JS
    assert "model_provider:S.session.model_provider||null" in MESSAGES_JS


def test_new_session_posts_picker_model_before_server_default():
    fn = _new_session_function()
    assert "reqBody.model=newModelState.model" in fn
    assert "explicitModelOverride" in fn
    assert "}else if(window._defaultModel){" in fn
    assert "modelSelForNew&&modelSelForNew.value&&typeof _modelStateForSelect==='function'" in fn
    provider_assignment = fn[fn.index("reqBody.model_provider="):].split(";", 1)[0]
    assert "newModelState.model_provider" in provider_assignment
    assert "_fallbackProvider" in provider_assignment
    assert "window._activeProvider" in fn
    assert "S.session&&S.session.model_provider" in fn
    pos_override = fn.index("explicitModelOverride")
    pos_default = fn.index("}else if(window._defaultModel){")
    pos_legacy = fn.index("modelSelForNew&&modelSelForNew.value&&typeof _modelStateForSelect==='function'")
    assert pos_override < pos_default < pos_legacy, (
        "newSession() must prefer the empty-composer override first, then the configured default, then legacy picker state"
    )
    assert "_familyMismatch" in fn
    assert "_readPersistedModelState" in fn


def test_model_picker_persists_without_active_session():
    boot_js = Path("static/boot.js").read_text(encoding="utf-8")
    body = boot_js[boot_js.index("$('modelSelect').onchange=async()=>") : boot_js.index("$('msg').addEventListener", boot_js.index("$('modelSelect').onchange=async()=>"))]
    assert "_writePersistedModelState(modelState.model,modelState.model_provider)" in body
    assert "_rememberEmptyComposerModelOverride(modelState.model,modelState.model_provider)" in body
    assert "if(!ownerSession){" in body
    assert body.index("if(!ownerSession){") < body.index("await api('/api/session/update'")


def test_session_model_changes_do_not_write_empty_composer_override():
    boot_js = Path("static/boot.js").read_text(encoding="utf-8")
    body = boot_js[boot_js.index("$('modelSelect').onchange=async()=>") : boot_js.index("$('msg').addEventListener", boot_js.index("$('modelSelect').onchange=async()=>"))]
    session_branch = body[body.index("if(typeof _rememberPendingSessionModel==='function')"):]
    assert "_rememberPendingSessionModel(ownerSid,modelState.model,modelState.model_provider)" in body
    assert "ownerSession.model=modelState.model" in body
    assert "_rememberEmptyComposerModelOverride(modelState.model,modelState.model_provider)" not in session_branch


def test_new_chat_prefers_explicit_empty_composer_override_before_configured_default():
    fn = _new_session_function()
    assert "explicitModelOverride" in fn
    assert "hasLoadedSession" in fn
    assert "consumedExplicitModelOverride" in fn
    assert "usingConfiguredDefault" in fn
    assert "_clearEmptyComposerModelOverride" in fn
    assert "newModelState={model:window._defaultModel,model_provider:null};" in fn
    assert fn.index("explicitModelOverride") < fn.index("}else if(window._defaultModel){") < fn.index("_modelStateForSelect"), (
        "newSession() must prefer the empty-composer override first, then the configured default, then legacy picker state"
    )


def test_new_session_keeps_provider_fallback_guards_after_model_precedence():
    fn = _new_session_function()
    provider_assignment = fn[fn.index("reqBody.model_provider="):].split(";", 1)[0]
    assert "newModelState.model_provider" in provider_assignment
    assert "_fallbackProvider" in provider_assignment
    assert "_familyMismatch" in provider_assignment
    assert "_fallbackIsNamedCustom" in provider_assignment
    assert "usingConfiguredDefault?window._activeProvider" in fn


def test_save_settings_syncs_default_model_provider_with_saved_model():
    panels_js = Path("static/panels.js").read_text(encoding="utf-8")
    save_block = _extract_function(panels_js, "async function saveSettings")
    apply_saved_block = _extract_function(panels_js, "function _applySavedSettingsUi")
    autosave_block = panels_js[panels_js.index("const pwField=$('settingsPassword');"):panels_js.index("if(!pwDirty&&!modelDirty){", panels_js.index("const pwField=$('settingsPassword');")) + 24]

    assert "_captureModelDropdownSelection($('settingsModel'))" in save_block
    assert "JSON.stringify({model,provider:modelState.model_provider||null})" in save_block
    assert "body.default_model_provider=(modelState&&modelState.model===model)?(modelState.model_provider||null):null;" in save_block
    assert "const modelChanged=(model||'')!==(_settingsHermesDefaultModelOnOpen||'')||((modelState.model_provider||null)!==(_settingsHermesDefaultModelProviderOnOpen||null));" in save_block
    assert "if(Object.prototype.hasOwnProperty.call(body,'default_model_provider')) window._activeProvider=body.default_model_provider||null;" in apply_saved_block
    assert "_settingsHermesDefaultModelProviderOnOpen=(models&&models.active_provider)||null;" in panels_js
    assert "if(Object.prototype.hasOwnProperty.call(body,'default_model_provider')) _settingsHermesDefaultModelProviderOnOpen=body.default_model_provider||null;" in apply_saved_block
    assert "(modelState.model_provider||null)!==(_settingsHermesDefaultModelProviderOnOpen||null)" in autosave_block
    assert "_captureModelDropdownSelection(modelSel)||{model:String((modelSel&&modelSel.value)||''),model_provider:null}" in panels_js
    assert "_captureModelDropdownSelection($('settingsModel'))||{model:String(model||''),model_provider:null}" in save_block


def test_password_settings_finalizer_applies_committed_save_and_refreshes_auth():
    panels_js = Path("static/panels.js").read_text(encoding="utf-8")
    helper = _extract_function(panels_js, "async function _finalizePasswordSettingsSave")
    script = textwrap.dedent(
        f"""
        const events=[];
        const fields={{
          settingsCurrentPassword:{{value:'old-password'}},
          settingsPassword:{{value:'new-password'}},
        }};
        const $=(id)=>fields[id]||null;
        let _settingsPasswordAuthEnabled=false;
        const _applySavedSettingsUi=(saved,body,uiState)=>events.push(['apply',saved,body,uiState]);
        const _updateCurrentPasswordVisibility=()=>events.push(['visibility']);
        const api=async(path)=>{{ events.push(['api',path]); return {{auth_enabled:true}}; }};
        const _renderSettingsAuthStatus=(status)=>events.push(['render',status]);
        const _updateAuthWarningBadge=(status)=>events.push(['badge',status]);
        const _updateAuthDisabledWarning=(status)=>events.push(['warning',status]);
        {helper}
        (async()=>{{
          await _finalizePasswordSettingsSave(
            {{password_auth_enabled:true}},
            {{theme:'dark'}},
            {{theme:'dark'}}
          );
          console.log(JSON.stringify({{
            events,
            current:fields.settingsCurrentPassword.value,
            next:fields.settingsPassword.value,
            enabled:_settingsPasswordAuthEnabled,
          }}));
        }})().catch((err)=>{{ console.error(err); process.exit(1); }});
        """
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["current"] == ""
    assert payload["next"] == ""
    assert payload["enabled"] is True
    assert payload["events"][0][0] == "apply"
    assert ["api", "/api/auth/status"] in payload["events"]


def test_password_model_partial_failure_uses_committed_settings_finalizer():
    panels_js = Path("static/panels.js").read_text(encoding="utf-8")
    save_block = _extract_function(panels_js, "async function saveSettings")
    password_branch = save_block[save_block.index("if(pw && pw.trim())"):]
    failure = password_branch.index("Failed to update default model")
    before_failure = password_branch[:failure]

    assert "await _finalizePasswordSettingsSave(saved,body,settingsUiState);" in before_failure
    assert "_settingsDirty=true;" in before_failure
    assert "_showSettingsUnsavedBar();" in before_failure
    assert "t(saved.auth_just_enabled?'settings_saved_pw':'settings_saved_pw_updated')" in password_branch[failure - 500:failure + 500]


def test_changelog_mentions_new_chat_default_model_provider_sync():
    unreleased = CHANGELOG.split("## [v0.51.103]", 1)[0]
    assert "New conversations now resync" in unreleased
    assert "default model provider" in unreleased
