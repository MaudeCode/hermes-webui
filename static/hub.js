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
        const sync = () => { const src = document.querySelector(srcSel); const v = src ? src.textContent.trim() : ''; b.textContent = v; b.hidden = !v; };
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
    const empty = document.getElementById('emptyState');
    const inner = document.getElementById('msgInner');
    if (empty) new MutationObserver(syncEmptyMemo).observe(empty, { attributes: true, attributeFilter: ['style', 'class'] });
    if (inner) new MutationObserver(syncEmptyMemo).observe(inner, { childList: true });
    window.addEventListener('popstate', syncEmptyMemo);
  }

  function init() {
    mountEmptyMemo();
    // Sidebar skeleton from the first frame; the real list replaces it.
    if (typeof showSessionListSkeleton === 'function' && !document.querySelector('#sessionList .session-item')) {
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
  const release = () => { document.documentElement.classList.remove('booting'); document.documentElement.classList.remove('boot-session'); syncEmptyMemo(); };
  const poll = () => {
    // S is a top-level `let` in boot.js: reachable by name, not via window.
    let ready = false;
    try { ready = (typeof S !== 'undefined') && !!S && !!S._bootReady; } catch (e) { ready = false; }
    if (ready || Date.now() - started > 12000) { setTimeout(release, 120); return; }
    setTimeout(poll, 60);
  };
  poll();
})();
