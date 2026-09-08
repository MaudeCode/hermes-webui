// ── Conversation Outline Panel (#2124) ───────────────────────────────────────
// Floating panel listing user messages as jump targets.
// _outlineSid guards against stale renders when the user switches sessions.

'use strict';

(function() {

let _outlineSid = null;       // session id the panel was last built for
let _panelOpen  = false;      // whether the panel is currently visible
let _outlineResizeObserver = null;
let _outlineWorkspaceObserver = null;

// Returns the current session id, or null if no session is loaded.
function _currentSid() {
  return (S && S.session && S.session.session_id) || null;
}

function _outlineAllowed() {
  const compact = window.matchMedia && window.matchMedia('(max-width:900px)').matches;
  // The outline is a chat-view affordance only — never show the toggle or panel
  // while another MAIN panel (settings, tasks, insights, …) is active. _currentPanel
  // is owned by panels.js; treat an undefined/absent value as the chat default.
  // 'todos' is a sidebar-only panel that leaves the chat transcript in <main>, so
  // the outline stays valid there too (and switching to it emits no <main> class
  // mutation for the observer, so allowing it keeps the toggle stable).
  const panel = (typeof _currentPanel === 'undefined') ? 'chat' : (_currentPanel || 'chat');
  const onChatView = panel === 'chat' || panel === 'todos';
  return window._showConversationOutline === true && !compact && onChatView;
}

function _syncOutlinePosition() {
  const root = document.documentElement;
  const panel = document.querySelector('.rightpanel');
  const open = root.dataset.workspacePanel === 'open';
  const width = open && panel ? Math.max(0, Math.round(panel.offsetWidth || 0)) : 0;
  root.style.setProperty('--outline-workspace-offset', width + 'px');
}

function applyConversationOutlinePreference() {
  const toggle = document.getElementById('outlineToggleBtn');
  const wrapper = document.getElementById('outlinePanelWrapper');
  const enabled = _outlineAllowed();
  document.documentElement.dataset.conversationOutline = enabled ? 'enabled' : 'disabled';
  _syncOutlinePosition();
  if (toggle) toggle.hidden = !enabled;
  if (!enabled) {
    _panelOpen = false;
    if (wrapper) wrapper.hidden = true;
  }
  _syncMinimap();
}

function _expandOutlineRenderWindow() {
  if (typeof _currentMessageRenderWindowSize !== 'function' ||
      typeof _messageRenderableMessageCount !== 'function' ||
      typeof _messageRenderWindowSize === 'undefined') return;
  _messageRenderWindowSize = Math.max(
    _currentMessageRenderWindowSize(),
    _messageRenderableMessageCount()
  );
}

function _ensureOutlineMessagesLoaded(sid) {
  if (!sid || S.busy || S.activeStreamId) return Promise.resolve(false);
  if (typeof _messagesTruncated === 'undefined' || !_messagesTruncated) {
    return Promise.resolve(false);
  }
  if (typeof _ensureAllMessagesLoaded !== 'function') return Promise.resolve(false);
  return _ensureAllMessagesLoaded().then(function() {
    if (!S.session || S.session.session_id !== sid) return false;
    _expandOutlineRenderWindow();
    // The load did a wholesale replace of S.messages; until the transcript is
    // rebuilt every row id still encodes the OLD index, so any msg-user-<i>
    // lookup would resolve to a different message.
    if (typeof renderMessages === 'function') renderMessages({ preserveScroll: true });
    return true;
  }).catch(function() {
    return false;
  });
}

// Extracts the first `maxLen` visible characters from a message content value.
function _excerptText(content, maxLen) {
  let text = '';
  if (Array.isArray(content)) {
    text = content
      .filter(p => p && p.type === 'text')
      .map(p => p.text || p.content || '')
      .join(' ');
  } else {
    text = String(content || '');
  }
  text = text.trim().replace(/\s+/g, ' ');
  const limit = maxLen || 60;
  return text.length > limit ? text.slice(0, limit) + '…' : text;
}

// Scrolls to a user message row identified by its rawIdx and flashes it.
function _jumpToMessage(rawIdx) {
  const sid = _currentSid();
  if (!sid) return;

  // For about a second after a session opens, the load-time bottom settle keeps
  // re-claiming the scroller (ResizeObserver + timers + rAF). Without taking
  // jump ownership the way ui.js's own question jump does, that settle wins the
  // race and a jump made right after load silently snaps back to the tail.
  if (typeof _cancelBottomSettle === 'function') _cancelBottomSettle();
  const scroller = document.getElementById('messages');
  if (scroller && typeof _beginMessageJumpScroll === 'function') {
    _beginMessageJumpScroll(scroller);
  }

  const rowId = 'msg-user-' + rawIdx;
  const row   = document.getElementById(rowId);
  if (row) {
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    _flashRow(row);
    return;
  }

  // Row is outside the render window — reload the full session and retry.
  // Use the bare messages=1 path (no msg_limit) so the server returns the
  // COMPLETE transcript: the target row is addressed by absolute index
  // (msg-user-<rawIdx>), so a bounded tail window would miss early rows.
  // (A previous version sent msg_limit=9999 as a "give me everything" hack,
  // but the server now clamps msg_limit, so the bare path is the correct way
  // to request the full transcript here.)
  if (typeof api !== 'function') return;
  if (S.busy || S.activeStreamId) return;
  api('/api/session?session_id=' + encodeURIComponent(sid) +
      '&messages=1&resolve_model=0')
    .then(function(data) {
      if (!data || !data.session) return;
      if (!S.session || S.session.session_id !== sid) return;  // session switched
      S.messages = data.session.messages || [];                // populate S
      _expandOutlineRenderWindow();
      if (typeof renderMessages === 'function') renderMessages({ preserveScroll: true });
      window.setTimeout(function() {
        if (!S.session || S.session.session_id !== sid) return;
        const r = document.getElementById('msg-user-' + rawIdx);
        if (r) { r.scrollIntoView({ block: 'center', behavior: 'smooth' }); _flashRow(r); }
      }, 120);
    })
    .catch(function() {});
}

// Brief highlight flash on a message row after jumping.
function _flashRow(row) {
  if (!row) return;
  row.classList.remove('outline-jump-flash');
  void row.offsetWidth;   // reflow to restart animation
  row.classList.add('outline-jump-flash');
  window.setTimeout(function() { row.classList.remove('outline-jump-flash'); }, 1200);
}

// Builds the list of user messages from S.messages.
// Returns [{rawIdx, label, excerpt}, …] for every user message with content.
function _buildEntries() {
  const msgs = (S && S.messages) || [];
  const entries = [];
  let userN = 0;

  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (!m || m.role !== 'user') continue;
    const text = _excerptText(m.content);
    if (!text) continue;
    userN++;
    entries.push({ rawIdx: i, label: userN, excerpt: text });
  }
  return entries;
}

// Renders the panel body.  Called every time the panel opens or session changes.
function _renderPanel() {
  const panel = document.getElementById('outlinePanel');
  if (!panel) return;

  const sid = _currentSid();

  // Session-scoped staleness guard.
  if (!sid) {
    panel.innerHTML = '<p class="outline-empty">' + t('outline_empty') + '</p>';
    _outlineSid = null;
    return;
  }

  if (!S.messages) {
    panel.innerHTML = '<p class="outline-empty">' + t('outline_loading') + '</p>';
    _outlineSid = sid;
    return;
  }

  _outlineSid = sid;
  const entries = _buildEntries();

  if (!entries.length) {
    panel.innerHTML = '<p class="outline-empty">' + t('outline_empty') + '</p>';
    return;
  }

  const items = entries.map(function(e) {
    return '<button class="outline-entry" type="button" ' +
      'onclick="window._outlineJump(' + e.rawIdx + ')">' +
      '<span class="outline-entry-num">' + e.label + '</span>' +
      '<span class="outline-entry-text">' + _escHtml(e.excerpt) + '</span>' +
      '</button>';
  });

  panel.innerHTML = items.join('');
}

// Simple HTML-escape for entry text.
function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Opens or closes the outline panel.
function toggleOutlinePanel() {
  if (!_outlineAllowed()) {
    applyConversationOutlinePreference();
    return;
  }
  _panelOpen = !_panelOpen;
  const wrapper = document.getElementById('outlinePanelWrapper');
  if (!wrapper) return;

  if (_panelOpen) {
    _syncOutlinePosition();
    wrapper.hidden = false;
    const sid = _currentSid();
    const panel = document.getElementById('outlinePanel');
    if (panel) panel.innerHTML = '<p class="outline-empty">' + t('outline_loading') + '</p>';
    _ensureOutlineMessagesLoaded(sid).then(function() {
      if (!_panelOpen || _currentSid() !== sid) return;
      _renderPanel();
      // Keep rendered data fresh after every renderMessages() call.
      _outlineSid = _currentSid();
    });
  } else {
    wrapper.hidden = true;
  }
}

// -- Turn minimap (HWEB-12) --------------------------------------------------
// The same _buildEntries() user turns, drawn as compact marks in the unused
// gutter left of the reading column. The labelled panel above stays the
// keyboard/touch fallback; this is the always-visible index for wide desktops.

const MINIMAP_MIN_MARKS   = 4;   // fewer turns than this and a map doesn't help
const MINIMAP_MIN_GUTTER  = 52;  // px of unused gutter needed to host the rail
const MINIMAP_PREVIEW_LEN = 140;

let _minimapSid      = null;      // session the marks were built for
let _minimapSig      = null;      // entry signature the marks were built from
let _minimapEntries  = [];
let _minimapObserver = null;      // one IntersectionObserver over user rows
let _minimapObserved = new Map(); // rawIdx -> the row element being observed
let _minimapVisible  = new Set(); // rawIdx currently intersecting the scroller
let _minimapActive   = null;      // rawIdx carrying aria-current
let _minimapFrame    = 0;
let _minimapShellObserver = null;

function _minimapEl() {
  return document.getElementById('outlineMinimap');
}

function _minimapMarks() {
  const el = _minimapEl();
  return el ? Array.prototype.slice.call(el.querySelectorAll('.outline-mark')) : [];
}

// Width of the empty space between the pane's left edge and the reading column.
// Measured rather than derived, so it already accounts for the .messages gutter,
// the scrollbar, full-width chat (--msg-max: 100%, column fills the pane) and
// browser zoom (which shrinks the pane in CSS pixels).
function _minimapGutter(shell) {
  const column = document.getElementById('msgInner');
  if (!shell || !column) return 0;
  return Math.max(0, column.getBoundingClientRect().left -
                     shell.getBoundingClientRect().left);
}

// The terminal assistant text of the turn that starts at rawIdx, or ''.
// Reads only loaded messages -- an unloaded turn simply has no reply preview.
function _turnReplyExcerpt(rawIdx) {
  const msgs = (S && S.messages) || [];
  let reply = '';
  for (let i = rawIdx + 1; i < msgs.length; i++) {
    const m = msgs[i];
    if (!m) continue;
    if (m.role === 'user') break;                 // next turn starts here
    if (m.role !== 'assistant') continue;
    const text = _excerptText(m.content, MINIMAP_PREVIEW_LEN);
    if (text) reply = text;                       // keep the LAST one
  }
  return reply;
}

function _minimapAllowed() {
  return _outlineAllowed();
}

function _teardownMinimap() {
  if (_minimapObserver) _minimapObserver.disconnect();
  _minimapObserved.clear();
  _minimapVisible.clear();
  _minimapActive = null;
}

function _ensureMinimapObserver() {
  if (_minimapObserver) return _minimapObserver;
  if (typeof IntersectionObserver === 'undefined') return null;
  _minimapObserver = new IntersectionObserver(function(records) {
    for (let i = 0; i < records.length; i++) {
      const r = records[i];
      const idx = Number(String(r.target.id).slice('msg-user-'.length));
      if (!isFinite(idx)) continue;
      if (r.isIntersecting) _minimapVisible.add(idx);
      else _minimapVisible.delete(idx);
    }
    _syncMinimapActive();
  }, { root: document.getElementById('messages'), threshold: 0 });
  return _minimapObserver;
}

// Keeps the observer pointed at whichever user rows are currently rendered.
// Virtualization drops and recreates rows without touching S.messages, so this
// runs on every render, not only when the mark list changes.
function _reobserveMinimapRows() {
  const obs = _ensureMinimapObserver();
  if (!obs) return;
  const live = new Set();
  for (let i = 0; i < _minimapEntries.length; i++) {
    const rawIdx = _minimapEntries[i].rawIdx;
    live.add(rawIdx);
    const row  = document.getElementById('msg-user-' + rawIdx);
    const prev = _minimapObserved.get(rawIdx) || null;
    if (prev === row) continue;
    if (prev) obs.unobserve(prev);
    if (row) {
      obs.observe(row);
      _minimapObserved.set(rawIdx, row);
    } else {
      _minimapObserved.delete(rawIdx);
      _minimapVisible.delete(rawIdx);
    }
  }
  const stale = [];
  _minimapObserved.forEach(function(row, rawIdx) {
    if (!live.has(rawIdx)) stale.push([rawIdx, row]);
  });
  for (let i = 0; i < stale.length; i++) {
    obs.unobserve(stale[i][1]);
    _minimapObserved.delete(stale[i][0]);
    _minimapVisible.delete(stale[i][0]);
  }
}

// The last turn that starts at or above the viewport's top edge. Used only when
// nothing intersects, so the O(turns) rect read never runs on a normal scroll.
function _precedingRenderedTurn() {
  const scroller = document.getElementById('messages');
  if (!scroller) return null;
  const top = scroller.getBoundingClientRect().top;
  let best = null;
  for (let i = 0; i < _minimapEntries.length; i++) {
    const row = document.getElementById('msg-user-' + _minimapEntries[i].rawIdx);
    if (!row) continue;
    if (row.getBoundingClientRect().top > top) break;   // entries are in order
    best = _minimapEntries[i].rawIdx;
  }
  return best;
}

// The nearest visible turn is the earliest user row still on screen. When a long
// answer fills the viewport nothing intersects, so fall back to geometry: the
// turn that answer belongs to. Deriving it rather than keeping the last active
// mark is what makes a first paint, a session switch and a programmatic jump
// (which skip the intermediate scroll states an observer would have reported)
// all land on the turn the reader is actually inside.
function _syncMinimapActive() {
  let next = null;
  _minimapVisible.forEach(function(idx) {
    if (next === null || idx < next) next = idx;
  });
  if (next === null) next = _precedingRenderedTurn();
  if (next === null) next = _minimapActive;   // scrolled above the first loaded turn
  if (next === null || next === _minimapActive) return;
  _minimapActive = next;
  const el = _minimapEl();
  const marks = _minimapMarks();
  const focusInside = !!(el && el.contains(document.activeElement));
  let hasStop = false;
  for (let i = 0; i < marks.length; i++) {
    const isActive = Number(marks[i].dataset.rawIdx) === next;
    if (isActive) marks[i].setAttribute('aria-current', 'true');
    else marks[i].removeAttribute('aria-current');
    // Tab lands on the turn the reader is at, unless they are already in the rail.
    if (!focusInside) marks[i].tabIndex = isActive ? 0 : -1;
    if (marks[i].tabIndex === 0) hasStop = true;
  }
  if (!focusInside && !hasStop && marks.length) marks[0].tabIndex = 0;
}

function _setMinimapTabStop(target) {
  const marks = _minimapMarks();
  for (let i = 0; i < marks.length; i++) marks[i].tabIndex = marks[i] === target ? 0 : -1;
}

function _minimapPreviewEl() {
  const el = _minimapEl();
  return el ? el.querySelector('.outline-mark-preview') : null;
}

function _hideMinimapPreview() {
  const preview = _minimapPreviewEl();
  if (preview) preview.hidden = true;
}

function _showMinimapPreview(mark) {
  const el = _minimapEl();
  const preview = _minimapPreviewEl();
  if (!el || !preview || !mark) return;
  const rawIdx = Number(mark.dataset.rawIdx);
  let entry = null;
  for (let i = 0; i < _minimapEntries.length; i++) {
    if (_minimapEntries[i].rawIdx === rawIdx) { entry = _minimapEntries[i]; break; }
  }
  if (!entry) return;
  const reply = _turnReplyExcerpt(rawIdx);
  preview.innerHTML =
    '<div class="outline-preview-user">' + _escHtml(entry.excerpt) + '</div>' +
    (reply ? '<div class="outline-preview-reply">' + _escHtml(reply) + '</div>' : '');
  preview.hidden = false;
  // Centre on the mark, then clamp inside the transcript pane so the first and
  // last marks do not push the card over the header or the composer.
  const shell = el.parentElement;
  if (!shell) return;
  const mapRect   = el.getBoundingClientRect();
  const markRect  = mark.getBoundingClientRect();
  const shellRect = shell.getBoundingClientRect();
  const height    = preview.offsetHeight;
  let top = markRect.top + markRect.height / 2 - height / 2;
  top = Math.min(Math.max(top, shellRect.top + 8), shellRect.bottom - height - 8);
  preview.style.top = Math.round(top - mapRect.top) + 'px';
}

function _renderMinimapMarks(entries) {
  const el = _minimapEl();
  if (!el) return;
  const active = _minimapActive;
  // A jump into unloaded history re-renders the transcript, which rebuilds the
  // marks under a keyboard user's feet. Put focus back on the same TURN: older
  // messages arriving ahead of it shift every rawIdx, but not its distance from
  // the end of the list.
  const before = _minimapMarks();
  const focusedAt = before.indexOf(document.activeElement);
  const focusedFromEnd = focusedAt >= 0 ? before.length - 1 - focusedAt : -1;
  el.innerHTML = entries.map(function(e) {
    const label = _escHtml(t('outline_minimap_mark', e.label, e.excerpt));
    const current = e.rawIdx === active ? ' aria-current="true"' : '';
    return '<button class="outline-mark" type="button" data-raw-idx="' + e.rawIdx +
      '" tabindex="-1" aria-label="' + label + '"' + current + '></button>';
  }).join('') + '<div class="outline-mark-preview" hidden aria-hidden="true"></div>';
  const marks = _minimapMarks();
  if (!marks.length) return;
  let stop = marks[0];
  for (let i = 0; i < marks.length; i++) {
    if (Number(marks[i].dataset.rawIdx) === active) { stop = marks[i]; break; }
  }
  if (focusedFromEnd >= 0) {
    const refocus = marks[marks.length - 1 - focusedFromEnd] || marks[marks.length - 1];
    refocus.tabIndex = 0;
    refocus.focus();
    return;
  }
  stop.tabIndex = 0;
}

// Rebuilds marks when the turn list changed, and always reconciles geometry,
// visibility and row observation. Cheap enough to run after every render.
function _syncMinimap() {
  const el = _minimapEl();
  if (!el) return;
  const shell = el.parentElement;

  if (!_minimapAllowed()) {
    el.hidden = true;
    _hideMinimapPreview();
    _teardownMinimap();
    _minimapSig = null;
    _minimapEntries = [];
    return;
  }

  const sid = _currentSid();
  if (sid !== _minimapSid) {          // session switch: no mark identity carries over
    _teardownMinimap();
    _minimapSid = sid;
    _minimapSig = null;
  }

  const entries = (sid && S && S.messages) ? _buildEntries() : [];
  const sig = entries.map(function(e) { return e.rawIdx + ':' + e.excerpt; }).join(' ');
  _minimapEntries = entries;
  if (sig !== _minimapSig) {
    _minimapSig = sig;
    _hideMinimapPreview();
    _renderMinimapMarks(entries);
  }

  const fits = entries.length >= MINIMAP_MIN_MARKS &&
               _minimapGutter(shell) >= MINIMAP_MIN_GUTTER;
  if (el.hidden !== !fits) el.hidden = !fits;
  if (!fits) {
    _hideMinimapPreview();
    _teardownMinimap();
    return;
  }
  _reobserveMinimapRows();
  if (_minimapActive === null) _syncMinimapActive();
}

function _scheduleMinimapSync() {
  if (_minimapFrame) return;
  const raf = window.requestAnimationFrame ||
    function(fn) { return window.setTimeout(fn, 16); };
  _minimapFrame = raf(function() {
    _minimapFrame = 0;
    _syncMinimap();
  }) || 1;
}

// A mark's rawIdx is an index into the CURRENTLY loaded messages. While the
// session is still truncated (the initial fetch is a tail window that
// _loadOlderMessages grows backwards) that index is tail-relative, and
// _jumpToMessage's own recovery path replaces S.messages with the COMPLETE
// transcript -- which renumbers every row. So when history is still unloaded,
// run the existing explicit full-load first, then re-resolve the mark by its
// position from the end of the turn list, which the prepend leaves fixed.
function _activateMinimapMark(mark) {
  const rawIdx = Number(mark.dataset.rawIdx);
  const sid = _currentSid();
  const truncated = typeof _messagesTruncated !== 'undefined' && _messagesTruncated;
  let fromEnd = -1;
  for (let i = 0; i < _minimapEntries.length; i++) {
    if (_minimapEntries[i].rawIdx === rawIdx) { fromEnd = _minimapEntries.length - 1 - i; break; }
  }
  if (!truncated || fromEnd < 0) {
    _jumpToMessage(rawIdx);
    return;
  }
  _ensureOutlineMessagesLoaded(sid).then(function(loaded) {
    if (_currentSid() !== sid) return;
    if (!loaded) { _jumpToMessage(rawIdx); return; }
    _syncMinimap();                      // marks now carry absolute indices
    const entry = _minimapEntries[_minimapEntries.length - 1 - fromEnd];
    _jumpToMessage(entry ? entry.rawIdx : rawIdx);
  });
}

function _onMinimapKeydown(ev) {
  const marks = _minimapMarks();
  const cur = marks.indexOf(document.activeElement);
  if (cur < 0) return;
  let next = -1;
  if (ev.key === 'ArrowDown' || ev.key === 'ArrowRight') next = Math.min(marks.length - 1, cur + 1);
  else if (ev.key === 'ArrowUp' || ev.key === 'ArrowLeft') next = Math.max(0, cur - 1);
  else if (ev.key === 'Home') next = 0;
  else if (ev.key === 'End') next = marks.length - 1;
  else return;
  ev.preventDefault();
  _setMinimapTabStop(marks[next]);
  marks[next].focus();
}

function _bindMinimap() {
  const el = _minimapEl();
  if (!el || el.dataset.bound === '1') return;
  el.dataset.bound = '1';
  const markOf = function(ev) {
    const target = ev.target;
    return target && target.closest ? target.closest('.outline-mark') : null;
  };
  el.addEventListener('click', function(ev) {
    const mark = markOf(ev);
    if (!mark) return;
    _setMinimapTabStop(mark);
    _activateMinimapMark(mark);
  });
  el.addEventListener('pointerover', function(ev) {
    const mark = markOf(ev);
    if (mark) _showMinimapPreview(mark);
  });
  el.addEventListener('pointerout', function(ev) {
    if (!el.contains(ev.relatedTarget)) _hideMinimapPreview();
  });
  el.addEventListener('focusin', function(ev) {
    const mark = markOf(ev);
    if (!mark) return;
    _setMinimapTabStop(mark);
    _showMinimapPreview(mark);
  });
  el.addEventListener('focusout', function(ev) {
    if (!el.contains(ev.relatedTarget)) _hideMinimapPreview();
  });
  el.addEventListener('keydown', _onMinimapKeydown);
  if (typeof ResizeObserver !== 'undefined' && el.parentElement && !_minimapShellObserver) {
    _minimapShellObserver = new ResizeObserver(_scheduleMinimapSync);
    _minimapShellObserver.observe(el.parentElement);
  }
}

// Jump target exposed on window so inline onclick handlers can reach it.
window._outlineJump = _jumpToMessage;
window.applyConversationOutlinePreference = applyConversationOutlinePreference;

// Re-render after renderMessages() if the panel is open and the session
// changed or new messages arrived since the last render.
(function _hookRenderMessages() {
  if (typeof window._outlineRenderHooked !== 'undefined') return;

  const _orig = window.renderMessages;
  if (typeof _orig !== 'function') {
    // renderMessages may not be defined yet — retry after DOMContentLoaded.
    if (!window._outlineRenderHookPending) {
      window._outlineRenderHookPending = true;
      document.addEventListener('DOMContentLoaded', _hookRenderMessages, { once: true });
    }
    return;
  }
  window._outlineRenderHooked = true;
  window._outlineRenderHookPending = false;
  window.renderMessages = function() {
    const result = _orig.apply(this, arguments);
    if (_panelOpen) {
      const sid = _currentSid();
      if (sid && (sid !== _outlineSid || (S.messages || []).length > 0)) {
        _renderPanel();
      }
    }
    _scheduleMinimapSync();
    return result;
  };
})();

// Expose public API.
window.toggleOutlinePanel = toggleOutlinePanel;

document.addEventListener('DOMContentLoaded', function() {
  _bindMinimap();
  applyConversationOutlinePreference();
  const root = document.documentElement;
  const rightPanel = document.querySelector('.rightpanel');
  if (rightPanel && typeof ResizeObserver !== 'undefined' && !_outlineResizeObserver) {
    _outlineResizeObserver = new ResizeObserver(_syncOutlinePosition);
    _outlineResizeObserver.observe(rightPanel);
  }
  if (!_outlineWorkspaceObserver) {
    _outlineWorkspaceObserver = new MutationObserver(applyConversationOutlinePreference);
    _outlineWorkspaceObserver.observe(root, {
      attributes: true,
      attributeFilter: ['data-workspace-panel']
    });
    // Also re-evaluate when the active main panel changes. switchPanel() is a
    // global function declaration (called via inline onclick), so it can't be
    // reliably wrapped from this script; instead we watch the `showing-<panel>`
    // class it toggles on <main>. The outline is a chat-only affordance, so this
    // hides the toggle + closes the panel when leaving chat (settings, tasks,
    // insights, …) and restores the toggle on return to chat. _outlineAllowed()
    // reads _currentPanel for the actual gate; this observer just triggers it.
    const mainEl = document.querySelector('main.main');
    if (mainEl) {
      _outlineWorkspaceObserver.observe(mainEl, {
        attributes: true,
        attributeFilter: ['class']
      });
    }
  }
});
window.addEventListener('resize', applyConversationOutlinePreference);

})();
