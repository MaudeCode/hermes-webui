"""The favicon follows the resolved app appearance, including extension skins."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_favicon_follows_resolved_theme():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the appearance check")
    source = (Path(__file__).resolve().parents[1] / "static/boot.js").read_text()
    functions = source[source.index("function _skinKey("):source.index("function _applyTheme(")]
    result = subprocess.run([node, "-e", """
const assert = require('node:assert/strict');
const _SKINS = [{name:'Default'}, {name:'FixedDark', _extScheme:'dark'},
  {name:'FixedLight', _extScheme:'light'}];
let _resolvedThemeBaseDark = false;
let dark = false;
let prism = null;
const favicon = {setAttribute(key, value){this[key] = value;}};
const document = {
  documentElement: {dataset:{}, classList:{toggle(name, value){dark=value;}}},
  getElementById(id){return id === 'hermes-favicon' ? favicon : prism;}
};
function _syncThemeColorMeta(){}
""" + functions + """
for (const hasPrism of [false, true]) {
  prism = hasPrism ? {href:''} : null;
  for (const [skin, requested, expected] of [
    ['default',false,false], ['default',true,true], ['default',false,false],
    ['fixeddark',false,true], ['fixedlight',true,false]
  ]) {
    document.documentElement.dataset.skin = skin;
    _setResolvedTheme(requested);
    assert.equal(dark, expected);
    assert.equal(favicon.href, 'static/favicon-' + (expected ? 'dark' : 'light') + '.svg');
  }
}
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
