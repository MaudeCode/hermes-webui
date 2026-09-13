// Hub pages: the main area is the page.
//
// The inherited layout parked every collection (skills, memory sections,
// spaces, profiles, scheduled jobs) in the left column and left the main
// area empty until something was clicked. This layer moves each page's
// list into its main view so the collection is the content, and shows the
// detail in place with a Back control. Control-only sidebars (insights,
// logs) become a toolbar under the main header. The DOM nodes keep their
// ids, so every existing loader and click handler keeps working; only the
// parent changes. Pure relocation, no behaviour rewrite.
(function () {
  'use strict';
  const HUB = {
    skills:     { panel: 'panelSkills',     main: 'mainSkills',     empty: 'skillDetailEmpty',     body: 'skillDetailBody',     title: 'skillDetailTitle' },
    memory:     { panel: 'panelMemory',     main: 'mainMemory',     empty: 'memoryDetailEmpty',    body: 'memoryDetailBody',    title: 'memoryDetailTitle' },
    workspaces: { panel: 'panelWorkspaces', main: 'mainWorkspaces', empty: 'workspaceDetailEmpty', body: 'workspaceDetailBody', title: 'workspaceDetailTitle' },
    profiles:   { panel: 'panelProfiles',   main: 'mainProfiles',   empty: 'profileDetailEmpty',   body: 'profileDetailBody',   title: 'profileDetailTitle' },
    tasks:      { panel: 'panelTasks',      main: 'mainTasks',      empty: 'taskDetailEmpty',      body: 'taskDetailBody',      title: 'taskDetailTitle' },
    insights:   { panel: 'panelInsights',   main: 'mainInsights',   toolbar: true },
    logs:       { panel: 'panelLogs',       main: 'mainLogs',       toolbar: true },
  };
  const BACK_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>';

  function showList(cfg) {
    const body = document.getElementById(cfg.body);
    const empty = document.getElementById(cfg.empty);
    const title = document.getElementById(cfg.title);
    if (body) body.style.display = 'none';
    if (empty) empty.style.display = '';
    if (title) title.textContent = '';
  }

  function mountPage(name, cfg) {
    const panel = document.getElementById(cfg.panel);
    const main = document.getElementById(cfg.main);
    if (!panel || !main || panel.dataset.hubMounted) return;
    panel.dataset.hubMounted = '1';
    const header = main.querySelector('.main-view-header');
    if (cfg.toolbar) {
      const bar = document.createElement('div');
      bar.className = 'hub-toolbar';
      bar.appendChild(panel);
      if (header) header.after(bar); else main.prepend(bar);
    } else {
      const empty = document.getElementById(cfg.empty);
      const body = document.getElementById(cfg.body);
      if (!empty) return;
      const list = document.createElement('div');
      list.className = 'hub-list';
      list.appendChild(panel);
      empty.prepend(list);
      empty.classList.add('hub-empty');
      if (header) {
        const back = document.createElement('button');
        back.type = 'button';
        back.className = 'hub-back';
        back.setAttribute('aria-label', 'Back');
        back.innerHTML = BACK_ICON;
        back.addEventListener('click', () => showList(cfg));
        header.prepend(back);
      }
      const sync = () => {
        const detail = !!(body && body.style.display !== 'none');
        main.classList.toggle('hub-detail', detail);
        main.classList.toggle('hub-listing', !detail);
      };
      if (body) new MutationObserver(sync).observe(body, { attributes: true, attributeFilter: ['style'] });
      sync();
    }
    main.classList.add('hub-page');
    main.dataset.hubPage = name;
  }

  function isMobile() { return window.matchMedia('(max-width: 768px)').matches; }

  // The sidebar has nothing to show on a hub page; hide it on desktop and
  // close the drawer on phones so the relocated list is what the user sees.
  function syncShell() {
    const main = document.querySelector('main.main');
    if (!main) return;
    const active = Object.keys(HUB).find(n => main.classList.contains('showing-' + n));
    document.documentElement.classList.toggle('hub-active', !!active);
    if (active && isMobile()) {
      const sidebar = document.querySelector('.sidebar');
      if (sidebar) sidebar.classList.remove('mobile-open');
    }
  }

  // Chat gets a real panel header. The window title bar used to be the only
  // place the session title lived; syncTopbar() already looks for
  // #topbarTitle / #topbarMeta, so providing them is enough to populate it.
  function mountChatHeader() {
    const main = document.getElementById('mainChat');
    const staticHeader = main && main.querySelector('.chat-header');
    if (staticHeader) { mountContextLine(staticHeader); return; }
    if (!main || document.getElementById('topbarTitle')) return;
    const header = document.createElement('div');
    header.className = 'chat-header';
    const title = document.createElement('h1');
    title.className = 'chat-header-title';
    title.id = 'topbarTitle';
    title.dataset.empty = (typeof t === 'function' && t('new_conversation') !== 'new_conversation') ? t('new_conversation') : 'New conversation';
    const meta = document.createElement('div');
    meta.className = 'chat-header-meta';
    meta.id = 'topbarMeta';
    const text = document.createElement('div');
    text.className = 'chat-header-text';
    text.append(title, meta);
    header.appendChild(text);
    main.prepend(header);
    mountContextLine(header);
    if (typeof syncTopbar === 'function') { try { syncTopbar(); } catch (e) { /* not booted yet; boot calls it */ } }
  }

  // Brand mark at the top of the rail (the title bar's icon, which is hidden
  // on desktop browsers) and a home screen for the empty chat: recent
  // conversations plus quick links, built from the sidebar list so it stays
  // in sync with whatever the app already loaded.
  // Chat sidebar: the filter field hides behind a search button in the
  // header and expands on demand, so the list starts at the top.
  function mountSidebarSearch() {
    const panel = document.getElementById('panelChat');
    const header = panel && (panel.querySelector('.panel-head .panel-head-actions') || panel.querySelector('.panel-head'));
    const search = panel && panel.querySelector('.sidebar-search');
    const input = search && search.querySelector('input');
    if (!header || !search || !input) return;
    let btn = header.querySelector('.sidebar-search-toggle');
    if (btn && btn.dataset.wired) return;
    if (!btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'panel-head-btn sidebar-search-toggle';
      btn.setAttribute('aria-label', 'Filter conversations');
      btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';
    }
    btn.dataset.wired = '1';
    const open = (on) => { panel.classList.toggle('search-open', on); btn.classList.toggle('active', on); if (on) input.focus(); };
    btn.addEventListener('click', () => open(!panel.classList.contains('search-open')));
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); open(false); } });
    input.addEventListener('blur', () => { setTimeout(() => { if (!input.value && document.activeElement !== input) open(false); }, 120); });
    if (!btn.parentElement) { const newChat = header.querySelector('#btnNewChat'); if (newChat) newChat.before(btn); else header.appendChild(btn); }
  }

  function mountRailBrand() {
    const rail = document.querySelector('.rail');
    const icon = document.querySelector('.app-titlebar-icon');
    if (!rail || !icon || rail.querySelector('.rail-brand')) return; // static markup already provides it
    const brand = document.createElement('button');
    brand.type = 'button';
    brand.className = 'rail-brand';
    brand.setAttribute('aria-label', 'New conversation');
    brand.addEventListener('click', () => {
      if (typeof switchPanel === 'function') switchPanel('chat');
      const b = document.getElementById('btnNewChat');
      if (b) b.click();
    });
    const clone = icon.cloneNode(true);
    // The mark's gradients are referenced by id; a clone would point at the
    // hidden original's defs, which do not paint. Give the copy its own ids.
    clone.querySelectorAll('[id]').forEach(n => { n.id = n.id + '-rail'; });
    clone.innerHTML = clone.innerHTML.replace(/url\(#([^)]+)\)/g, 'url(#$1-rail)');
    brand.appendChild(clone);
    rail.prepend(brand);
  }

  // Context line under the session title: profile · model · effort · workspace.
  // Each part mirrors an existing control and clicking it opens that control,
  // so the composer footer can drop its chips.
  function mountContextLine(header) {
    const text = header.querySelector('.chat-header-text');
    if (!text) return;
    let line = text.querySelector('.chat-context');
    if (line && line.dataset.wired) return;
    if (line) {
      // Static markup: wire the existing buttons.
      line.dataset.wired = '1';
      line.querySelectorAll('.chat-context-item').forEach(b => {
        const srcSel = b.dataset.src, openId = b.dataset.open;
        b.addEventListener('click', (e) => { e.stopPropagation(); document.getElementById(openId)?.click(); });
        const sync = () => { const src = document.querySelector(srcSel); const v = src ? src.textContent.trim() : ''; if (!v && b.textContent && document.documentElement.classList.contains('booting')) return; b.textContent = v; b.hidden = !v; };
        sync();
        const src = document.querySelector(srcSel);
        if (src) new MutationObserver(sync).observe(src, { childList: true, characterData: true, subtree: true });
      });
      const profile = document.getElementById('titlebarProfileBtn');
      if (profile) header.appendChild(profile);
      return;
    }
    line = document.createElement('div');
    line.className = 'chat-context';
    const parts = [
      { key: 'profile',   src: '#titlebarProfileLabel',          open: () => document.getElementById('titlebarProfileBtn')?.click() },
      { key: 'model',     src: '#composerModelChip .composer-model-label', open: () => document.getElementById('composerModelChip')?.click() },
      { key: 'effort',    src: '#composerReasoningChip .composer-reasoning-label', open: () => document.getElementById('composerReasoningChip')?.click() },
      { key: 'workspace', src: '#composerMobileWorkspaceLabel',  open: () => document.getElementById('composerMobileWorkspaceAction')?.click() },
    ];
    parts.forEach((p, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chat-context-item chat-context-' + p.key;
      b.addEventListener('click', (e) => { e.stopPropagation(); p.open(); });
      const sync = () => {
        const src = document.querySelector(p.src);
        const v = src ? src.textContent.trim() : '';
        if (!v && b.textContent && document.documentElement.classList.contains('booting')) return; // keep cached value
        b.textContent = v;
        b.hidden = !v;
      };
      sync();
      const src = document.querySelector(p.src);
      if (src) new MutationObserver(sync).observe(src, { childList: true, characterData: true, subtree: true });
      line.appendChild(b);
    });
    text.appendChild(line);
    // Profile switcher lives in the header on the right.
    const profile = document.getElementById('titlebarProfileBtn');
    if (profile) header.appendChild(profile);
  }

  // Source filter (WebUI / CLI) as a small menu button instead of a row.
  function mountSourceMenu() {
    const tabs = document.querySelector('#panelChat .session-source-tabs:not(.source-menu .session-source-tabs)');
    const head = document.querySelector('#panelChat .panel-head .panel-head-actions');
    if (!tabs || !head) return;
    const existing = head.querySelector('.source-menu');
    if (existing) {
      // The list re-renders its tabs; adopt the fresh row into the menu.
      existing.replaceChildren(tabs);
      tabs.addEventListener('click', () => setTimeout(() => { existing.hidden = true; head.querySelector('.source-menu-btn')?.classList.remove('active'); }, 0));
      return;
    }
    let btn = head.querySelector('.source-menu-btn');
    if (!btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'panel-head-btn source-menu-btn';
      btn.setAttribute('aria-label', 'Conversation source');
      btn.setAttribute('aria-haspopup', 'menu');
      btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>';
    }
    const wrap = document.createElement('div');
    wrap.className = 'source-menu';
    wrap.hidden = true;
    wrap.appendChild(tabs);
    const close = () => { wrap.hidden = true; btn.classList.remove('active'); document.removeEventListener('click', onDoc, true); };
    const onDoc = (e) => { if (!wrap.contains(e.target) && e.target !== btn) close(); };
    btn.addEventListener('click', () => {
      if (wrap.hidden) { wrap.hidden = false; btn.classList.add('active'); setTimeout(() => document.addEventListener('click', onDoc, true), 0); }
      else close();
    });
    tabs.addEventListener('click', () => setTimeout(close, 0));
    if (!btn.parentElement) head.insertBefore(btn, head.firstChild);
    head.appendChild(wrap);
  }

  // Model picker as a two-pane palette: providers on the left, that provider's
  // models on the right, search across everything on top. The app renders a
  // flat list (heading, body, heading, body…) and re-renders on search; this
  // regroups whatever it rendered without touching the rows' handlers.
  function transformModelPalette(dd) {
    if (dd.querySelector('.mp-main')) return;
    const kids = [...dd.children];
    const isHeading = (n) => n.classList && n.classList.contains('model-group') && !n.classList.contains('sub') && !n.classList.contains('model-custom-sep');
    const firstHeading = kids.findIndex(isHeading);
    if (firstHeading < 0) return;
    const top = document.createElement('div'); top.className = 'mp-top';
    kids.slice(0, firstHeading).forEach(n => top.appendChild(n));
    const sections = [];
    let cur = null;
    kids.slice(firstHeading).forEach(n => {
      if (isHeading(n)) { cur = document.createElement('div'); cur.className = 'mp-section'; cur.appendChild(n); sections.push(cur); }
      else if (cur) cur.appendChild(n);
    });
    const main = document.createElement('div'); main.className = 'mp-main';
    const nav = document.createElement('div'); nav.className = 'mp-nav';
    const pane = document.createElement('div'); pane.className = 'mp-pane';
    const show = (sec) => {
      sections.forEach(x => x.classList.toggle('active', x === sec));
      nav.querySelectorAll('.mp-nav-btn').forEach(b => b.classList.toggle('active', b._section === sec));
      pane.scrollTop = 0;
    };
    sections.forEach(sec => {
      const heading = sec.firstElementChild;
      const label = heading.textContent.trim();
      const m = label.match(/^(.*?)\s*\((\d+)\)\s*$/);
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'mp-nav-btn'; btn._section = sec;
      const name = document.createElement('span'); name.className = 'mp-nav-name'; name.textContent = m ? m[1] : label;
      btn.appendChild(name);
      if (m) { const c = document.createElement('span'); c.className = 'mp-nav-count'; c.textContent = m[2]; btn.appendChild(c); }
      btn.addEventListener('click', (e) => { e.stopPropagation(); show(sec); });
      nav.appendChild(btn);
      pane.appendChild(sec);
    });
    main.append(nav, pane);
    dd.append(top, main);
    const active = sections.find(sec => sec.querySelector('.model-opt.active')) || sections[0];
    show(active);
    const input = dd.querySelector('.model-search-input');
    const syncSearch = () => dd.classList.toggle('mp-search', !!(input && input.value.trim()));
    if (input) input.addEventListener('input', syncSearch);
    syncSearch();
  }
  function mountModelPalette() {
    const dd = document.getElementById('modelDropdown') || document.querySelector('.composer-model-wrap .model-dropdown') || document.querySelector('.model-dropdown');
    if (!dd) return;
    let timer = null;
    const schedule = () => { clearTimeout(timer); timer = setTimeout(() => transformModelPalette(dd), 40); };
    new MutationObserver(schedule).observe(dd, { childList: true });
    schedule();
  }

  // Remember which session is empty so the next reload paints the hero layout
  // from the first frame instead of starting at the bottom and jumping.
  const EMPTY_KEY = 'hermes-webui-session-empty';
  function currentSid() {
    const m = (location.pathname || '').match(/\/session\/([^\/?#]+)/);
    if (m) return m[1];
    try { return localStorage.getItem('hermes-webui-session') || ''; } catch (e) { return ''; }
  }
  function syncEmptyMemo() {
    const empty = document.getElementById('emptyState');
    const inner = document.getElementById('msgInner');
    if (!empty || !inner) return;
    const hasRows = !!inner.querySelector('.msg-row');
    const shown = getComputedStyle(empty).display !== 'none';
    const id = currentSid();
    try {
      if (shown && !hasRows && id) localStorage.setItem(EMPTY_KEY, id);
      else if (hasRows && localStorage.getItem(EMPTY_KEY) === id) localStorage.removeItem(EMPTY_KEY);
    } catch (e) { /* storage unavailable */ }
  }
  function mountEmptyMemo() {
    // Hook the app's own empty-state switches: top-level function declarations
    // are window properties, so wrapping them catches every caller, including
    // the case where the state was already visible and no DOM attribute changes.
    ['showConversationEmptyState', 'hideConversationEmptyState'].forEach(name => {
      const orig = window[name];
      if (typeof orig !== 'function' || orig._hubWrapped) return;
      const wrapped = function () { const r = orig.apply(this, arguments); setTimeout(syncEmptyMemo, 0); return r; };
      wrapped._hubWrapped = true;
      window[name] = wrapped;
    });
    const inner = document.getElementById('msgInner');
    if (inner) new MutationObserver(syncEmptyMemo).observe(inner, { childList: true });
    window.addEventListener('popstate', syncEmptyMemo);
  }

  // Boot snapshots: the rendered sidebar and the current transcript are kept
  // in localStorage and painted back synchronously during HTML parse (see the
  // inline scripts in index.html). The real loaders replace them; these
  // observers keep the snapshots current and clear the "snapshot" marker the
  // moment the app paints its own content.
  const SIDEBAR_KEY = 'hermes-boot:sidebar', TRANSCRIPT_KEY = 'hermes-boot:transcript';
  const LIMIT = 350000;
  function saveSidebarSnapshot() {
    const list = document.getElementById('sessionList');
    if (!list || document.getElementById('sessionListBoot')) return;
    if (!list.querySelector('.session-item') || list.querySelector('.skeleton-row')) return;
    // Archived rows are a transient toggle (state resets on reload); don't snapshot them.
    try { if (typeof _showArchived !== 'undefined' && _showArchived) return; } catch (e) { /* fine */ }
    const html = list.innerHTML;
    try { if (html.length < LIMIT) localStorage.setItem(SIDEBAR_KEY, html); } catch (e) { /* quota */ }
  }
  function saveTranscriptSnapshot() {
    const inner = document.getElementById('msgInner');
    if (!inner || inner.dataset.bootSnapshot) return;
    let loading = false, session = null;
    try { loading = (typeof _loadingSessionId !== 'undefined') && !!_loadingSessionId; } catch (e) { loading = false; }
    try { session = (typeof S !== 'undefined' && S) ? S.session : null; } catch (e) { session = null; }
    const sid = currentSid();
    if (loading || !sid || !session || String(session.id || session.session_id || '') !== sid) return;
    const rows = inner.querySelectorAll('.msg-row').length;
    const empty = document.getElementById('emptyState');
    const emptyShown = empty && getComputedStyle(empty).display !== 'none';
    if (!rows && !emptyShown) return;
    const ctx = {};
    document.querySelectorAll('.chat-context-item').forEach(b => { const k = b.className.match(/chat-context-(profile|model|effort|workspace)\b/); if (k && !b.hidden && b.textContent) ctx[k[1]] = b.textContent; });
    const title = (document.getElementById('topbarTitle') || {}).textContent || '';
    const heroEl = document.getElementById('emptyHeroTitle');
    const hero = (heroEl && heroEl.classList.contains('ready')) ? heroEl.textContent : '';
    const html = rows ? inner.innerHTML : '';
    // Context ring (usage %) so it shows on reload instead of appearing later.
    let ring = null;
    const wrap = document.getElementById('ctxIndicatorWrap'), ind = document.getElementById('ctxIndicator');
    const val = document.getElementById('ctxRingValue'), pct = document.getElementById('ctxPercent');
    if (wrap && ind && val && wrap.style.display !== 'none') ring = { cls: ind.className, dasharray: val.style.strokeDasharray, dashoffset: val.style.strokeDashoffset, pct: pct ? pct.textContent : '' };
    const snap = { sid, rows, title, hero, ctx, ring, html: html.length < LIMIT ? html : '', ts: Date.now() };
    try { localStorage.setItem(TRANSCRIPT_KEY, JSON.stringify(snap)); } catch (e) { /* quota */ }
  }
  let sbTimer = null, trTimer = null;
  function mountBootSnapshots() {
    const list = document.getElementById('sessionList');
    const inner = document.getElementById('msgInner');
    if (list) new MutationObserver(() => {
      if (document.getElementById('sessionListBoot')) return; // overlay still shown; release removes it
      clearTimeout(sbTimer); sbTimer = setTimeout(saveSidebarSnapshot, 600);
    }).observe(list, { childList: true, subtree: true, characterData: true });
    if (inner) new MutationObserver(() => {
      if (inner.dataset.bootSnapshot) return;
      clearTimeout(trTimer); trTimer = setTimeout(saveTranscriptSnapshot, 1200);
    }).observe(inner, { childList: true, subtree: true, characterData: true });
    ['showConversationEmptyState', 'hideConversationEmptyState'].forEach(name => {
      const orig = window[name];
      if (typeof orig !== 'function' || orig._hubSnap) return;
      const wrapped = function () { const r = orig.apply(this, arguments); clearTimeout(trTimer); trTimer = setTimeout(saveTranscriptSnapshot, 300); return r; };
      wrapped._hubSnap = true; wrapped._hubWrapped = orig._hubWrapped;
      window[name] = wrapped;
    });
    window.addEventListener('pagehide', () => { saveSidebarSnapshot(); saveTranscriptSnapshot(); });
  }

  // Text the snapshot painted (title, headline) is held during boot; the app's
  // writes are remembered and applied once at release, so nothing flickers
  // through intermediate values.
  const holds = [];
  function holdDuringBoot(el, opts) {
    if (!el || !el.textContent) return;
    const kept = { text: el.textContent, cls: el.className };
    let last = null, guard = false;
    const obs = new MutationObserver(() => {
      if (guard || !document.documentElement.classList.contains('booting')) return;
      last = { text: el.textContent, cls: el.className };
      if (el.textContent !== kept.text || (opts && opts.keepClass && el.className !== kept.cls)) {
        guard = true; el.textContent = kept.text; if (opts && opts.keepClass) el.className = kept.cls; guard = false;
      }
    });
    obs.observe(el, { childList: true, characterData: true, subtree: true, attributes: !!(opts && opts.keepClass), attributeFilter: (opts && opts.keepClass) ? ['class'] : undefined });
    holds.push(() => { obs.disconnect(); if (last && last.text) { el.textContent = last.text; if (opts && opts.keepClass) el.className = last.cls; } });
  }
  function mountBootHolds() {
    const title = document.getElementById('topbarTitle');
    if (title && title.textContent) holdDuringBoot(title);
    else if (title) document.documentElement.classList.add('title-pending'); // blank until final
    holdDuringBoot(document.getElementById('emptyHeroTitle'), { keepClass: true });
  }

  // Global caches (not per session): context values and the welcome headline.
  // They make the first paint complete even before any session snapshot exists.
  function saveGlobalCaches() {
    try {
      const ctx = {};
      document.querySelectorAll('.chat-context-item').forEach(b => { const k = b.className.match(/chat-context-(profile|model|effort|workspace)\b/); if (k && !b.hidden && b.textContent) ctx[k[1]] = b.textContent; });
      if (Object.keys(ctx).length) localStorage.setItem('hermes-boot:ctx', JSON.stringify(ctx));
      const hero = document.getElementById('emptyHeroTitle');
      if (hero && hero.classList.contains('ready') && hero.textContent) localStorage.setItem('hermes-boot:hero', hero.textContent);
    } catch (e) { /* storage unavailable */ }
  }
  let gcTimer = null;
  function mountGlobalCaches() {
    const schedule = () => { clearTimeout(gcTimer); gcTimer = setTimeout(saveGlobalCaches, 500); };
    const ctx = document.querySelector('.chat-context');
    if (ctx) new MutationObserver(schedule).observe(ctx, { childList: true, characterData: true, subtree: true, attributes: true });
    const hero = document.getElementById('emptyHeroTitle');
    if (hero) new MutationObserver(schedule).observe(hero, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    window.addEventListener('pagehide', saveGlobalCaches);
  }

  // Workspace panel: header names the workspace and shows its path, the empty
  // state offers the two actions that fill it, and the Artifacts tab lights up
  // when a run produced something.
  function mountWorkspacePanel() {
    const heading = document.getElementById('workspacePanelHeading');
    const group = heading && heading.closest('.workspace-panel-title-group');
    const nameSrc = document.getElementById('sidebarWsName');
    const pathSrc = document.getElementById('sidebarWsPath');
    if (group && !group.querySelector('.workspace-panel-path')) {
      const path = document.createElement('span');
      path.className = 'workspace-panel-path';
      group.appendChild(path);
      const sync = () => {
        const name = nameSrc ? nameSrc.textContent.trim() : '';
        const p = pathSrc ? pathSrc.textContent.trim() : '';
        if (name && name !== 'Workspace') heading.textContent = name;
        path.textContent = p;
        path.title = p;
        path.hidden = !p;
      };
      sync();
      [nameSrc, pathSrc].forEach(el => { if (el) new MutationObserver(sync).observe(el, { childList: true, characterData: true, subtree: true }); });
    }
    const empty = document.getElementById('wsEmptyState');
    if (empty && !document.querySelector('.ws-empty-actions')) {
      const actions = document.createElement('div');
      actions.className = 'ws-empty-actions';
      [['Upload a file', 'btnUploadWorkspace'], ['New file', 'btnNewFile']].forEach(([label, id]) => {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'ws-empty-btn'; b.textContent = label;
        b.addEventListener('click', () => document.getElementById(id)?.click());
        actions.appendChild(b);
      });
      empty.after(actions);
      const syncEmpty = () => { actions.style.display = (empty.style.display !== 'none' && empty.textContent.indexOf('No workspace') < 0) ? '' : 'none'; };
      new MutationObserver(syncEmpty).observe(empty, { attributes: true, attributeFilter: ['style'], childList: true, characterData: true, subtree: true });
      syncEmpty();
    }
    const count = document.getElementById('workspaceArtifactsCount');
    const tab = document.getElementById('workspaceArtifactsTab');
    if (count && tab) {
      const syncCount = () => tab.classList.toggle('has-artifacts', /^[1-9]/.test(count.textContent.trim()));
      new MutationObserver(syncCount).observe(count, { childList: true, characterData: true, subtree: true });
      syncCount();
    }
  }

  // Phone: a bottom tab bar (Chat · Tasks · Kanban · Agent · More) replaces the
  // rail-inside-the-drawer. Agent and More open a small sheet of the remaining
  // destinations. The drawer keeps the conversation list.
  const TABS = [
    { key: 'chat', label: 'Chat', panel: 'chat' },
    { key: 'tasks', label: 'Tasks', panel: 'tasks' },
    { key: 'kanban', label: 'Kanban', panel: 'kanban' },
    { key: 'agent', label: 'Agent', items: [['skills', 'Skills'], ['memory', 'Memory'], ['profiles', 'Profiles'], ['workspaces', 'Spaces']] },
    { key: 'more', label: 'More', items: [['todos', 'Todos'], ['insights', 'Insights'], ['logs', 'Logs'], ['settings', 'Settings']] },
  ];
  function railIcon(panel) { const b = document.querySelector('.rail-btn[data-panel="' + panel + '"] svg'); return b ? b.cloneNode(true) : null; }
  function mountTabbar() {
    if (document.querySelector('.tabbar')) return;
    const bar = document.createElement('nav');
    bar.className = 'tabbar';
    bar.setAttribute('aria-label', 'Primary navigation');
    let sheet = null;
    const closeSheet = () => { if (sheet) { sheet.remove(); sheet = null; } bar.querySelectorAll('.tabbar-btn').forEach(b => b.classList.remove('open')); };
    const openSheet = (tab, btn) => {
      closeSheet();
      sheet = document.createElement('div');
      sheet.className = 'tabbar-sheet';
      tab.items.forEach(([panel, label]) => {
        const it = document.createElement('button');
        it.type = 'button'; it.className = 'tabbar-sheet-item';
        const ic = railIcon(panel); if (ic) it.appendChild(ic);
        const t = document.createElement('span'); t.textContent = label; it.appendChild(t);
        it.addEventListener('click', () => { closeSheet(); if (typeof switchPanel === 'function') switchPanel(panel); });
        sheet.appendChild(it);
      });
      document.body.appendChild(sheet);
      btn.classList.add('open');
      setTimeout(() => document.addEventListener('click', (e) => { if (sheet && !sheet.contains(e.target) && !btn.contains(e.target)) closeSheet(); }, { once: true }), 0);
    };
    TABS.forEach(tab => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'tabbar-btn'; b.dataset.tab = tab.key;
      const ic = railIcon(tab.panel || tab.items[0][0]); if (ic) b.appendChild(ic);
      const l = document.createElement('span'); l.textContent = tab.label; b.appendChild(l);
      b.addEventListener('click', () => {
        if (tab.panel) { closeSheet(); if (typeof switchPanel === 'function') switchPanel(tab.panel); const sb = document.querySelector('.sidebar'); if (sb && tab.panel !== 'chat') sb.classList.remove('mobile-open'); }
        else if (sheet && b.classList.contains('open')) closeSheet(); else openSheet(tab, b);
      });
      bar.appendChild(b);
    });
    document.body.appendChild(bar);
    const main = document.querySelector('main.main');
    const syncActive = () => {
      const active = [...document.querySelectorAll('.rail-btn.nav-tab.active')].map(x => x.dataset.panel)[0] || 'chat';
      bar.querySelectorAll('.tabbar-btn').forEach(b => {
        const tab = TABS.find(t => t.key === b.dataset.tab);
        const on = tab.panel ? tab.panel === active : tab.items.some(([p]) => p === active);
        b.classList.toggle('active', on);
      });
    };
    if (main) new MutationObserver(syncActive).observe(main, { attributes: true, attributeFilter: ['class'] });
    syncActive();
  }

  function init() {
    mountTabbar();
    mountWorkspacePanel();
    mountGlobalCaches();
    mountBootHolds();
    mountEmptyMemo();
    mountBootSnapshots();
    // Sidebar skeleton from the first frame; the real list replaces it.
    if (typeof showSessionListSkeleton === 'function' && !document.querySelector('#sessionList .session-item') && !document.getElementById('sessionListBoot')) {
      try { showSessionListSkeleton(); } catch (e) { /* cosmetic */ }
    }
    mountSourceMenu();
    const chatPanel = document.getElementById('panelChat');
    if (chatPanel) new MutationObserver(() => mountSourceMenu()).observe(chatPanel, { childList: true, subtree: true });
    mountModelPalette();
    mountRailBrand();
    mountSidebarSearch();
    mountChatHeader();
    Object.entries(HUB).forEach(([name, cfg]) => mountPage(name, cfg));
    const main = document.querySelector('main.main');
    if (main) new MutationObserver(syncShell).observe(main, { attributes: true, attributeFilter: ['class'] });
    syncShell();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  // Transitions are suppressed while the page boots (see index.html); release
  // once the first paint has settled so nothing slides into place on load.
  // Release only once the app's own boot has settled (S._bootReady), with a
  // hard cap; a load-event timer would expire before the session resolves and
  // let the empty-state hero shift animate.
  const started = Date.now();
  const release = () => {
    document.documentElement.classList.remove('booting');
    document.documentElement.classList.remove('boot-session');
    document.documentElement.classList.remove('title-pending');
    // Safety net: snapshots are inert only until the app paints; never leave them inert.
    holds.splice(0).forEach(fn => { try { fn(); } catch (e) { /* cosmetic */ } });
    const overlay = document.getElementById('sessionListBoot');
    if (overlay) overlay.remove();
    document.documentElement.classList.remove('has-sidebar-snapshot');
    ['sessionList', 'msgInner'].forEach(id => { const el = document.getElementById(id); if (el) delete el.dataset.bootSnapshot; });
    const pending = window.__hermesPendingSid;
    if (pending) { window.__hermesPendingSid = null; if (typeof loadSession === 'function') { try { loadSession(pending); } catch (e) { /* app decides */ } } }
    syncEmptyMemo();
  };
  const poll = () => {
    // S is a top-level `let` in boot.js: reachable by name, not via window.
    let ready = false;
    try { ready = (typeof S !== 'undefined') && !!S && !!S._bootReady; } catch (e) { ready = false; }
    if (ready || Date.now() - started > 12000) { setTimeout(release, 450); return; }
    setTimeout(poll, 60);
  };
  poll();
})();
