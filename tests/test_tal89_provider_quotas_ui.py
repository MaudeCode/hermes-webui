"""Behavioral frontend coverage for TAL-89 multi-provider quota rendering."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is required for frontend quota behavior coverage")
def test_multi_provider_sources_render_and_targeted_refresh_keeps_source_identity():
    panels = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
    start = panels.index("async function _fetchProviderQuotas")
    end = panels.index("\nasync function renderProviderCostChart", start)
    source = panels[start:end]
    harness = f"""
const calls=[];
const listeners=[];
const replacements=[];
const api=async endpoint=>{{
  calls.push(endpoint);
  return {{version:1,sources:[{{
    source_id:'qsrc work/1',provider_id:'openrouter',provider_label:'OpenRouter',
    account_label:'Work',is_active_provider:true,supported:true,status:'available',
    plan:'Pro',windows:[{{label:'Weekly',used_percent:25,remaining_percent:75,reset_at:'2030-03-17T17:30:00Z'}}],
    quota:null,details:[],fetched_at:'2030-03-17T12:30:00Z'
  }},{{
    source_id:'qsrc-personal',provider_id:'deepseek',provider_label:'DeepSeek',
    account_label:'Personal',is_active_provider:false,supported:true,status:'available',
    plan:null,windows:[],balances:[{{currency:'USD',total:8.5}}],quota:null,details:[]
  }}]}};
}};
const t=(key,...args)=>({{
  provider_quota_title:'Provider quotas',provider_quota_active_provider:'Active provider',
  provider_quota_refresh_usage:'Refresh usage',provider_quota_refreshing:'Refreshing...',
  provider_quota_refresh_title:'Refresh provider usage limits now',provider_quota_refresh_succeeded:'refreshed',
  provider_quota_refresh_failed:'failed',provider_quota_unavailable:'unavailable',providers_empty:'empty',
  provider_quota_status_available:'available',provider_quota_status_stale:'stale',provider_quota_status_removed:'removed',
  provider_quota_last_checked:'checked '+args[0],provider_quota_last_checked_after_refresh:'checked after refresh',
  provider_quota_window_fallback:'Window',provider_quota_weekly_limit:'Weekly limit',provider_quota_used_meta:args[0]+' used',
  provider_quota_resets_meta:'resets '+args[0]
}}[key]||key);
const esc=value=>String(value??'');
const localStorage={{getItem:()=>null,setItem:()=>{{}}}};
const showToast=()=>{{}};
const $=()=>null;
const renderProviderCostChart=()=>{{}};
class FakeElement{{
  constructor(tag){{this.tag=tag;this.dataset={{}};this.children=[];this.isConnected=true;this._html='';}}
  set innerHTML(value){{this._html=String(value);}}
  get innerHTML(){{return this._html;}}
  set textContent(value){{this._text=String(value);}}
  get textContent(){{return this._text||'';}}
  appendChild(child){{this.children.push(child);return child;}}
  append(...children){{this.children.push(...children);}}
  addEventListener(type,callback){{listeners.push([this,type,callback]);}}
  setAttribute(){{}}
  removeAttribute(){{}}
  replaceWith(next){{replacements.push(next);this.isConnected=false;}}
  querySelector(selector){{
    if(selector==='[data-provider-quota-refresh]'||selector==='[data-provider-quota-refresh-all]'){{
      this._button=this._button||new FakeElement('button');
      return this._button;
    }}
    return null;
  }}
}}
const document={{createElement:tag=>new FakeElement(tag)}};
{source}
(async()=>{{
  const payload=await _fetchProviderQuotas(false);
  const collection=_buildProviderQuotaCollection(payload);
  const list=collection.children[1];
  const card=list.children[0];
  await _refreshProviderQuotaSource(card,card._button,payload.sources[0]);
  console.log(JSON.stringify({{
    cards:list.children.length,
    source:card.dataset.providerQuotaSource,
    provider:card.dataset.providerQuotaProvider,
    html:card.innerHTML,
    calls,
    replacements:replacements.length
  }}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    result = subprocess.run(
        [NODE, "-e", harness],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert rendered["cards"] == 2
    assert rendered["source"] == "qsrc work/1"
    assert rendered["provider"] == "openrouter"
    assert "OpenRouter" in rendered["html"]
    assert "Work · Pro" in rendered["html"]
    assert "Active provider" in rendered["html"]
    assert "75%" in rendered["html"]
    assert rendered["calls"][0] == "/api/provider/quotas"
    assert "source=qsrc+work%2F1" in rendered["calls"][1]
    assert "refresh=1" in rendered["calls"][1]
    assert rendered["replacements"] == 1
