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
