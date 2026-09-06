"""HWEB-58: the context ring's denominator must honor config ``model_overrides``.

``model_overrides`` (agent config, v0.21.0) patches a model's context window
without waiting for a catalog release. The WebUI owns no context-window table
of its own — it resolves through ``agent.model_metadata`` — so the override
only has to survive that hop. These tests pin that it does, including the
fill-gap ``_default`` precedence that is easy to break by "helpfully" applying
a default to catalog-known models.

The ring denominator is ``session.context_length``, produced by
``routes._resolve_context_length_for_session_model`` and rendered by
``static/ui.js`` as ``ctxWindow = usage.context_length``.
"""

import json
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")

CATALOG_MODEL = "catalog-known-model"
CATALOG_CONTEXT = 100_000
UNKNOWN_MODEL = "hweb58-model-the-catalog-never-heard-of"

_SYNTHETIC_CATALOG = {
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "models": {
            CATALOG_MODEL: {
                "id": CATALOG_MODEL,
                "name": "Catalog Known Model",
                "limit": {"context": CATALOG_CONTEXT, "output": 8192},
            },
        },
    },
    "openai": {"id": "openai", "name": "OpenAI", "models": {}},
}


def _import_models_dev():
    """Import the agent's override resolver, or skip.

    ``agent/models_dev.py`` imports ``requests`` at module scope purely for the
    live models.dev fetch. These tests never reach the network (a synthetic
    disk cache is always present), and ``requests`` is not a WebUI dependency,
    so stub it rather than skipping wherever the agent is installed but its
    venv is not the one running pytest.
    """
    try:
        return _import_agent_metadata()
    except ModuleNotFoundError:  # pragma: no cover - env-dependent
        sys.modules.setdefault("requests", types.ModuleType("requests"))
    try:
        return _import_agent_metadata()
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"hermes-agent model metadata not importable: {exc}")


def _import_agent_metadata():
    import agent.model_metadata  # noqa: F401
    import agent.models_dev as models_dev

    return models_dev


def _resolve(monkeypatch, tmp_path, overrides, model, provider="anthropic"):
    """Resolve a context window for *model* against an isolated agent home."""
    models_dev = _import_models_dev()

    home = tmp_path / f"home-{len(list(tmp_path.iterdir()))}"
    home.mkdir(parents=True)
    (home / "models_dev_cache.json").write_text(
        json.dumps(_SYNTHETIC_CATALOG), encoding="utf-8"
    )
    config = {"model": {"provider": provider, "default": model}}
    if overrides is not None:
        config["model_overrides"] = overrides
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(home))
    # The registry is memoised per process; force a reload from the home above.
    monkeypatch.setattr(models_dev, "_models_dev_cache", {}, raising=False)
    monkeypatch.setattr(models_dev, "_models_dev_cache_time", 0, raising=False)

    import api.config as api_config
    import api.routes as routes

    # The WebUI's own provider / custom-provider context settings are a
    # separate, higher-precedence layer; keep them empty so each assertion is
    # about model_overrides alone.
    monkeypatch.setattr(api_config, "get_config", lambda *a, **k: {})

    return routes._resolve_context_length_for_session_model(model, provider)


def test_explicit_override_sets_the_ring_denominator(monkeypatch, tmp_path):
    """An explicit per-model override wins over the catalog value."""
    assert _resolve(monkeypatch, tmp_path, None, CATALOG_MODEL) == CATALOG_CONTEXT
    assert (
        _resolve(
            monkeypatch,
            tmp_path,
            {"anthropic": {CATALOG_MODEL: {"context_window": 4242}}},
            CATALOG_MODEL,
        )
        == 4242
    )


def test_default_fills_gaps_without_displacing_the_catalog(monkeypatch, tmp_path):
    """``_default`` applies to unknown models only — the rule in both directions."""
    overrides = {"anthropic": {"_default": {"context_window": 4242}}}

    assert _resolve(monkeypatch, tmp_path, overrides, UNKNOWN_MODEL) == 4242
    assert _resolve(monkeypatch, tmp_path, overrides, CATALOG_MODEL) == CATALOG_CONTEXT


def test_provider_default_beats_global_default(monkeypatch, tmp_path):
    """A provider-scoped ``_default`` outranks the global ``_default``."""
    overrides = {
        "anthropic": {"_default": {"context_window": 4242}},
        "_default": {"context_window": 111_000},
    }

    assert _resolve(monkeypatch, tmp_path, overrides, UNKNOWN_MODEL) == 4242
    assert (
        _resolve(monkeypatch, tmp_path, overrides, UNKNOWN_MODEL, provider="openai")
        == 111_000
    )


@pytest.mark.parametrize("overrides", [None, {}, {"anthropic": {}}])
def test_absent_or_empty_overrides_change_nothing(monkeypatch, tmp_path, overrides):
    """The default path is untouched when no override applies."""
    assert _resolve(monkeypatch, tmp_path, overrides, CATALOG_MODEL) == CATALOG_CONTEXT
    assert _resolve(monkeypatch, tmp_path, overrides, UNKNOWN_MODEL) != 4242


def test_ring_reads_the_resolved_context_length():
    """Pin the last hop: the ring divides by ``usage.context_length``."""
    assert "const ctxWindow=usage.context_length||DEFAULT_CTX;" in UI_JS
    assert "Math.round((contextPromptTok/ctxWindow)*100)" in UI_JS
