"""HWEB-56: the six Hermes Agent v0.21.0 providers must reach the WebUI catalog.

`_seed_provider_models_from_core()` deliberately refuses to add vendors that are
not already in the WebUI catalog, so a new agent provider stays invisible until
it is curated into the three static tables by hand.  These tests pin the
canonical slugs (the agent separates slug from marketing name — "Tencent
TokenPlan" is `tencent-tokenplan`, "Actual Computer" is `actual`) and the
API-key env vars, and prove no provider gets registered twice.
"""

from __future__ import annotations

import pytest

import api.config as config
import api.plugin_providers as plugin_providers
import api.providers as providers

# Canonical agent slug → (display name, API-key env var).
NEW_PROVIDERS = {
    "commandcode": ("CommandCode", "COMMANDCODE_API_KEY"),
    "tencent-tokenplan": ("Tencent TokenPlan", "TOKENPLAN_API_KEY"),
    "tencent-tokenhub": ("Tencent TokenHub", "TOKENHUB_API_KEY"),
    "nebius-token-factory": ("Nebius Token Factory", "NEBIUS_API_KEY"),
    "router": ("Ramp Router", "RAMP_ROUTER_API_KEY"),
    "actual": ("Actual Computer", "ACTUAL_API_KEY"),
    "meta-ai": ("Meta Model API", "MODEL_API_KEY"),
}

# Marketing names and agent aliases that must NOT be used as catalog keys.
NON_CANONICAL_KEYS = (
    "tokenplan",
    "tokenhub",
    "tencent",
    "actual-computer",
    "actualcomputer",
    "aci",
    "nebius",
    "tokenfactory",
    "ramp-router",
    "ramp",
    "router.com",
    "meta",
    "muse",
    "muse-spark",
    "commandcode-chat",
)


@pytest.mark.parametrize("slug", sorted(NEW_PROVIDERS))
def test_slug_resolves_in_all_three_catalog_tables(slug):
    display, env_var = NEW_PROVIDERS[slug]
    assert config._PROVIDER_DISPLAY[slug] == display
    assert slug in config._PROVIDER_MODELS
    assert providers._PROVIDER_ENV_VAR[slug] == env_var
    assert providers._provider_env_var_for(slug) == env_var


@pytest.mark.parametrize("bad_key", NON_CANONICAL_KEYS)
def test_marketing_names_and_aliases_are_not_catalog_keys(bad_key):
    """The catalog is keyed by agent-canonical slugs, never by aliases."""
    assert bad_key not in config._PROVIDER_DISPLAY
    assert bad_key not in config._PROVIDER_MODELS
    assert bad_key not in providers._PROVIDER_ENV_VAR


def test_tencent_lanes_are_two_distinct_providers():
    """v0.21.0 ships TokenPlan alongside TokenHub — one is not an alias of the other."""
    assert config._PROVIDER_DISPLAY["tencent-tokenplan"] != config._PROVIDER_DISPLAY["tencent-tokenhub"]
    assert providers._PROVIDER_ENV_VAR["tencent-tokenplan"] != providers._PROVIDER_ENV_VAR["tencent-tokenhub"]


@pytest.mark.parametrize("slug", sorted(NEW_PROVIDERS))
def test_catalog_models_are_well_formed(slug):
    entries = config._PROVIDER_MODELS[slug]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        assert str(entry.get("id") or "").strip()
        assert str(entry.get("label") or "").strip()
    if slug not in ("router", "actual"):
        # Only the account/cluster-scoped providers are allowed an empty cold
        # catalog; the rest must offer something before the live probe lands.
        assert entries, f"{slug} needs a static model fallback"


def _isolate_key_lookup(monkeypatch, tmp_path):
    """Point key detection at an empty HERMES_HOME with no pooled credentials."""
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(config, "_has_explicit_pool_credentials", lambda _pid: False)


@pytest.mark.parametrize("slug", sorted(NEW_PROVIDERS))
def test_provider_has_key_follows_the_env_var(monkeypatch, tmp_path, slug):
    _isolate_key_lookup(monkeypatch, tmp_path)
    env_var = NEW_PROVIDERS[slug][1]

    monkeypatch.delenv(env_var, raising=False)
    assert providers._provider_has_key(slug, config_data={}) is False

    monkeypatch.setenv(env_var, "hweb56-test-key")
    assert providers._provider_has_key(slug, config_data={}) is True


def test_meta_model_api_is_registered_exactly_once(monkeypatch):
    """Static entry wins; plugin discovery must not also claim ``meta-ai``.

    ``_webui_static_provider_ids()`` is memoised, so clear it first — otherwise
    a cache warmed before this module's import would answer for the old table.
    """
    monkeypatch.setattr(plugin_providers, "_WEBUI_STATIC_PROVIDER_IDS", None)
    monkeypatch.setattr(
        plugin_providers,
        "_PROFILES_BY_NAME",
        {"meta-ai": object()},
    )

    assert plugin_providers.is_plugin_model_provider("meta-ai") is False
    assert "meta-ai" not in plugin_providers.plugin_model_provider_ids()
    assert plugin_providers.effective_provider_display_name(
        "meta-ai", config._PROVIDER_DISPLAY
    ) == "Meta Model API"


def test_seeding_still_refuses_to_invent_providers(monkeypatch):
    """`_seed_provider_models_from_core` must not add vendors of its own."""
    import sys
    import types

    before = set(config._PROVIDER_MODELS)
    fake = types.ModuleType("hermes_cli.models")
    fake._PROVIDER_MODELS = {"totally-new-vendor": ["some-model"]}
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake)

    config._seed_provider_models_from_core()

    assert set(config._PROVIDER_MODELS) == before


def test_new_providers_reach_the_model_picker(monkeypatch, tmp_path):
    """End-to-end: an authenticated v0.21.0 provider renders as a picker group.

    Before the catalog entries existed these slugs fell through
    ``_build_available_models_uncached``'s "unrecognized provider" branch and
    were dropped, which is the bug HWEB-56 describes.
    """
    pytest.importorskip("hermes_cli.models")
    import hermes_cli.auth as hermes_auth
    import hermes_cli.models as hermes_models

    monkeypatch.setattr(
        hermes_models,
        "list_available_providers",
        lambda: [{"id": pid, "authenticated": True} for pid in NEW_PROVIDERS],
        raising=False,
    )
    monkeypatch.setattr(
        hermes_auth,
        "get_auth_status",
        lambda pid: {"key_source": "env", "logged_in": False},
        raising=False,
    )
    # No live catalog probe — this asserts the static fallback path.
    monkeypatch.setattr(config, "_read_live_provider_model_ids", lambda _pid: [])
    # Building a catalog reloads the config cache and rebinds `_cfg_path` to the
    # tmp config. Left behind, that makes the next `get_config()` see
    # `path_changed` and force a reload that discards whatever cfg a later test
    # installed in memory. Snapshot the cache identity through monkeypatch so
    # this test cannot leak into another test's routing decisions.
    monkeypatch.setattr(config, "_cfg_path", config._cfg_path, raising=False)
    monkeypatch.setattr(config, "_available_models_cache", config._available_models_cache)
    monkeypatch.setattr(config, "_models_cache_provenance", config._models_cache_provenance)
    monkeypatch.setattr(config, "_advertised_model_ids_memo", config._advertised_model_ids_memo)
    monkeypatch.setattr(config, "_models_cache_path", tmp_path / "models_cache.json")
    monkeypatch.setattr(config, "_get_config_path", lambda: tmp_path / "missing-config.yaml")
    monkeypatch.setattr("api.profiles.get_active_hermes_home", lambda: tmp_path, raising=False)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg.update({"model": {}})
    config._cfg_mtime = 0.0
    config.invalidate_models_cache()
    try:
        result = config.get_available_models()
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()

    groups = {group["provider_id"]: group for group in result["groups"]}

    for slug, (display, _env) in NEW_PROVIDERS.items():
        if not config._PROVIDER_MODELS[slug]:
            # router / actual carry no static catalog; with the live probe
            # stubbed out there is nothing to render and the zero-model filter
            # (#1568) hides them. Covered by the models-well-formed test above.
            continue
        assert slug in groups, f"{slug} missing from the picker"
        assert groups[slug]["provider"] == display
        assert groups[slug]["models"]


# Aggregators that serve foreign vendor namespaces under their own key. A bare
# ``vendor/model`` row from one of these falls through
# ``resolve_model_provider``'s OpenRouter default, which either fails outright
# (OpenRouter unconfigured) or bills the wrong account.
NAMESPACED_AGGREGATORS = ("commandcode", "nebius-token-factory", "router", "actual")


@pytest.mark.parametrize("slug", NAMESPACED_AGGREGATORS)
def test_namespaced_rows_route_to_their_own_provider(slug):
    """Every catalog row resolves back to *slug*, active or cross-provider."""
    assert slug in config._PORTAL_PROVIDERS
    own_cfg = {"model": {"provider": slug, "default": "x"}}
    other_cfg = {"model": {"provider": "anthropic", "default": "x"}}

    # router / actual carry no static rows — exercise the id shapes their live
    # account catalogs actually return.
    rows = config._PROVIDER_MODELS[slug] or [
        {"id": "accounts/fireworks/models/kimi-k3", "label": "x"},
        {"id": "Qwen/Qwen2.5-0.5B-Instruct-GGUF", "label": "x"},
        {"id": "deepseek/deepseek-v4-pro", "label": "x"},
    ]
    for entry in rows:
        raw_id = entry["id"]

        # Selected while this provider is active — the id stays bare.
        active_rows = config._apply_provider_prefix([dict(entry)], slug, slug)
        _model, provider, _base = config.resolve_model_provider(
            active_rows[0]["id"], config_data=own_cfg
        )
        assert provider == slug, f"{raw_id} misrouted to {provider!r} while active"

        # Selected from another provider's session — must be qualified.
        cross_rows = config._apply_provider_prefix([dict(entry)], slug, "anthropic")
        cross_id = cross_rows[0]["id"]
        assert cross_id == f"@{slug}:{raw_id}"
        resolved, provider, _base = config.resolve_model_provider(
            cross_id, config_data=other_cfg
        )
        assert provider == slug, f"{raw_id} misrouted to {provider!r} cross-provider"
        assert resolved == raw_id, f"{raw_id} mangled to {resolved!r}"


@pytest.mark.parametrize("slug", NAMESPACED_AGGREGATORS)
def test_namespaced_rows_route_correctly_with_no_active_provider(slug):
    """A fresh install with `model.provider` unset must not leak rows to OpenRouter.

    `_apply_provider_prefix()` short-circuits when nothing is active, which is
    right for an ordinary provider (no sibling to be confused with) but wrong for
    a portal one: `resolve_model_provider()`'s cross-provider branch claims the
    bare `vendor/model` for OpenRouter.
    """
    no_provider_cfg = {"model": {}}
    rows = config._PROVIDER_MODELS[slug] or [
        {"id": "accounts/fireworks/models/kimi-k3", "label": "x"},
        {"id": "Qwen/Qwen2.5-0.5B-Instruct-GGUF", "label": "x"},
    ]
    for entry in rows:
        raw_id = entry["id"]
        picked = config._apply_provider_prefix([dict(entry)], slug, "")[0]["id"]
        resolved, provider, _base = config.resolve_model_provider(
            picked, config_data=no_provider_cfg
        )
        assert provider == slug, f"{raw_id} misrouted to {provider!r} with no active provider"
        assert resolved == raw_id


def test_no_active_provider_leaves_ordinary_providers_untouched():
    """The short-circuit still applies to everything outside _PORTAL_PROVIDERS."""
    for pid, mid in (("openrouter", "deepseek/deepseek-v4-pro"), ("anthropic", "claude-opus-4.7")):
        assert pid not in config._PORTAL_PROVIDERS
        rows = config._apply_provider_prefix([{"id": mid, "label": "x"}], pid, "")
        assert rows[0]["id"] == mid


def test_portal_prefixing_leaves_non_aggregators_alone():
    """Only ``_PORTAL_PROVIDERS`` gained the namespaced-id qualification."""
    rows = config._apply_provider_prefix(
        [{"id": "deepseek/deepseek-v4-pro", "label": "x"}], "openrouter", "anthropic"
    )
    assert rows[0]["id"] == "deepseek/deepseek-v4-pro"


def test_nvidia_namespaced_rows_survive_the_round_trip():
    """The pre-existing portal provider keeps resolving to itself either way."""
    cfg_nvidia = {"model": {"provider": "nvidia", "default": "x"}}
    cfg_other = {"model": {"provider": "anthropic", "default": "x"}}
    for entry in config._PROVIDER_MODELS["nvidia"]:
        raw_id = entry["id"]
        active_id = config._apply_provider_prefix([dict(entry)], "nvidia", "nvidia")[0]["id"]
        assert config.resolve_model_provider(active_id, config_data=cfg_nvidia)[:2] == (raw_id, "nvidia")
        cross_id = config._apply_provider_prefix([dict(entry)], "nvidia", "anthropic")[0]["id"]
        assert config.resolve_model_provider(cross_id, config_data=cfg_other)[:2] == (raw_id, "nvidia")


@pytest.mark.parametrize("slug", ["router", "actual"])
def test_live_only_providers_report_their_catalog_on_the_providers_endpoint(
    monkeypatch, tmp_path, slug
):
    """`/api/providers` must not report 0 models for a live-only provider.

    `get_providers()` initialises from `_PROVIDER_MODELS`, which is empty for
    these two by design. Without the live lookup the Settings card reads "0
    models" while `/api/models` renders the same provider's live catalog.
    """
    import api.profiles as profiles

    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "_PROVIDER_DISPLAY", {slug: config._PROVIDER_DISPLAY[slug]})
    monkeypatch.setattr(providers, "_PROVIDER_MODELS", {slug: []})
    monkeypatch.setattr(providers, "_OAUTH_PROVIDERS", frozenset())
    monkeypatch.setattr(providers, "plugin_model_provider_ids", lambda: set())
    monkeypatch.setattr(providers, "is_plugin_model_provider", lambda _pid: False)
    monkeypatch.setattr(providers, "get_config", lambda: {"model": {}, "providers": {}})
    monkeypatch.setattr(providers, "_provider_has_key", lambda _pid, **_kw: True)
    monkeypatch.setattr(
        providers, "_read_live_provider_model_ids", lambda _pid: ["vendor/model-a", "model-b"]
    )

    entry = next(p for p in providers.get_providers()["providers"] if p["id"] == slug)

    assert entry["models_total"] == 2
    assert {m["id"] for m in entry["models"]} == {"vendor/model-a", "model-b"}


def test_env_var_removal_clears_every_name_that_grants_access():
    """No provider may be detected via a name its key-removal path won't clear.

    `_provider_has_key()` also honours `_PROVIDER_ENV_VAR_ALIASES`, but removal
    writes only the canonical var. Any new provider that gained an alias here
    would show "removed" in Settings while still being configured after reload.
    """
    for slug in NEW_PROVIDERS:
        assert slug not in providers._PROVIDER_ENV_VAR_ALIASES


def _force_env_fallback(monkeypatch):
    """Make `hermes_cli` unimportable so detection takes its env-var fallback."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ("hermes_cli.models", "hermes_cli.auth"):
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def _fallback_groups(monkeypatch, tmp_path, env):
    import api.profiles as profiles

    _force_env_fallback(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
        monkeypatch.setitem(profiles._INITIAL_PROCESS_ENV, name, value)
    monkeypatch.setattr(config, "_models_cache_path", tmp_path / "models_cache.json")
    monkeypatch.setattr(config, "_get_config_path", lambda: tmp_path / "missing-config.yaml")
    monkeypatch.setattr(config, "_cfg_path", config._cfg_path, raising=False)
    monkeypatch.setattr(config, "_available_models_cache", config._available_models_cache)
    monkeypatch.setattr(config, "_models_cache_provenance", config._models_cache_provenance)
    monkeypatch.setattr(config, "_advertised_model_ids_memo", config._advertised_model_ids_memo)
    monkeypatch.setattr("api.profiles.get_active_hermes_home", lambda: tmp_path, raising=False)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg.update({"model": {}})
    config._cfg_mtime = 0.0
    config.invalidate_models_cache()
    try:
        return {g["provider_id"] for g in config.get_available_models()["groups"]}
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()


# Only the providers that ship a static catalog: router / actual render zero
# models on this path (no live probe) and the zero-model filter drops them.
_FALLBACK_DETECTABLE = [s for s in sorted(NEW_PROVIDERS) if s not in ("router", "actual")]


@pytest.mark.parametrize("slug", _FALLBACK_DETECTABLE)
def test_env_var_detection_reaches_the_no_hermes_cli_fallback(monkeypatch, tmp_path, slug):
    """Settings and the picker must agree even when `hermes_cli` is unavailable.

    This path used to scan a hand-maintained copy of the env-var list, so a
    provider added to `_PROVIDER_ENV_VAR` but not to that copy reported
    "configured" on the Providers card while its picker group was missing.
    """
    env_var = NEW_PROVIDERS[slug][1]
    assert slug in _fallback_groups(monkeypatch, tmp_path, {env_var: "hweb56-test-key"})


def test_fallback_detection_reads_the_canonical_key_table(monkeypatch, tmp_path):
    """A provider in `_PROVIDER_ENV_VAR` is detectable without a bespoke branch."""
    # nvidia was in the table but absent from the old hardcoded list.
    assert "nvidia" in _fallback_groups(monkeypatch, tmp_path, {"NVIDIA_API_KEY": "k"})


def test_fallback_detection_keeps_the_openai_slug_special_case(monkeypatch, tmp_path):
    """`OPENAI_API_KEY` maps to openai-api/openai-codex, never a bare `openai`.

    The agent registry has no bare `openai` provider (#3443), so the table-driven
    pass must not add one just because `_PROVIDER_ENV_VAR` is keyed that way.
    """
    groups = _fallback_groups(monkeypatch, tmp_path, {"OPENAI_API_KEY": "k"})
    assert "openai" not in groups
    assert {"openai-api", "openai-codex"} <= groups
