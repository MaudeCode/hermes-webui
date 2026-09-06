"""HWEB-58: the context ring's denominator must honor config ``model_overrides``.

``model_overrides`` (agent config, v0.21.0) patches a model's context window
without waiting for a catalog release. The WebUI owns no context-window table
of its own — it resolves through ``agent.model_metadata`` — so the override
only has to survive that hop. These tests pin that it does, including the
fill-gap ``_default`` precedence that is easy to break by "helpfully" applying
a default to catalog-known models.

The ring denominator is ``session.context_length``, produced by
``routes._resolve_context_length_for_session_model`` and rendered by
``static/ui.js``'s ``_syncCtxIndicator`` as ``ctxWindow = usage.context_length``.

The precedence tests need a real hermes-agent, which CI does not provision, so
they skip there like the rest of this repo's agent-dependent suite. The two
links that live in *this* repo — the resolver's provider handoff and the ring's
use of the denominator it is handed — are covered separately below with no
agent required, so a regression in either still turns CI red.
"""

import json
import sys
import types

import pytest
import yaml

from tests.test_issue3717_context_length_provider_overrides import (
    _install_fake_context_resolver,
)
from tests.test_issue4685_post_compression_context_metering import (
    _run_context_indicator,
)

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


def test_override_moves_the_rendered_ring(monkeypatch, tmp_path):
    """End to end: the override reaches the percentage the ring actually renders."""
    prompt_tokens = 20_000
    overrides = {"anthropic": {CATALOG_MODEL: {"context_window": 25_000}}}

    def render(context_length):
        return _run_context_indicator(
            {"last_prompt_tokens": prompt_tokens, "context_length": context_length}
        )

    catalog_ring = render(_resolve(monkeypatch, tmp_path, None, CATALOG_MODEL))
    override_ring = render(_resolve(monkeypatch, tmp_path, overrides, CATALOG_MODEL))

    assert catalog_ring["percent"] == "20"
    assert catalog_ring["tokens"].endswith("20000 / 100000 tokens used")
    assert override_ring["percent"] == "80"
    assert override_ring["tokens"].endswith("20000 / 25000 tokens used")


# ── Agent-free halves: these run everywhere, including CI ──────────────────


def _render_ring(prompt_tokens, context_length):
    return _run_context_indicator(
        {"last_prompt_tokens": prompt_tokens, "context_length": context_length}
    )


def test_ring_divides_by_the_denominator_it_is_handed():
    """The resolved window drives the rendered percentage, whatever its source."""
    assert _render_ring(20_000, 100_000)["percent"] == "20"
    assert _render_ring(20_000, 25_000)["percent"] == "80"
    assert _render_ring(20_000, 25_000)["tokens"].endswith("20000 / 25000 tokens used")


def test_resolver_hands_provider_and_model_to_the_override_aware_lookup(monkeypatch):
    """``_override_context_window`` needs both, or every override is ignored.

    Step 0b of ``get_model_context_length`` is gated on ``if provider and
    model``. Dropping either from this call silently reverts the whole feature,
    so pin the handoff where the agent itself is unavailable.
    """
    import api.config as api_config
    import api.routes as routes

    calls = _install_fake_context_resolver(monkeypatch)
    monkeypatch.setattr(api_config, "get_config", lambda *a, **k: {})

    resolved = routes._resolve_context_length_for_session_model(
        CATALOG_MODEL, "anthropic"
    )

    assert calls[-1]["provider"] == "anthropic"
    assert calls[-1]["model"] == CATALOG_MODEL
    # No WebUI-layer context setting is configured, so nothing may preempt the
    # override at resolution step 0: the agent's answer is returned verbatim.
    assert calls[-1]["config_context_length"] is None
    assert resolved == 256_000
