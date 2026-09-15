//#region node_modules/workbox-core/_version.js
try {
	self["workbox:core:7.4.0"] && _();
} catch {}
var e = (e, ...t) => {
	let n = e;
	return t.length > 0 && (n += ` :: ${JSON.stringify(t)}`), n;
}, t = class extends Error {
	constructor(t, n) {
		let r = e(t, n);
		super(r), this.name = t, this.details = n;
	}
}, n = {
	googleAnalytics: "googleAnalytics",
	precache: "precache-v2",
	prefix: "workbox",
	runtime: "runtime",
	suffix: typeof registration < "u" ? registration.scope : ""
}, r = (e) => [
	n.prefix,
	e,
	n.suffix
].filter((e) => e && e.length > 0).join("-"), i = (e) => {
	for (let t of Object.keys(n)) e(t);
}, a = {
	updateDetails: (e) => {
		i((t) => {
			typeof e[t] == "string" && (n[t] = e[t]);
		});
	},
	getGoogleAnalyticsName: (e) => e || r(n.googleAnalytics),
	getPrecacheName: (e) => e || r(n.precache),
	getPrefix: () => n.prefix,
	getRuntimeName: (e) => e || r(n.runtime),
	getSuffix: () => n.suffix
};
//#endregion
//#region node_modules/workbox-core/_private/waitUntil.js
function o(e, t) {
	let n = t();
	return e.waitUntil(n), n;
}
//#endregion
//#region node_modules/workbox-precaching/_version.js
try {
	self["workbox:precaching:7.4.0"] && _();
} catch {}
//#endregion
//#region node_modules/workbox-precaching/utils/createCacheKey.js
var s = "__WB_REVISION__";
function c(e) {
	if (!e) throw new t("add-to-cache-list-unexpected-type", { entry: e });
	if (typeof e == "string") {
		let t = new URL(e, location.href);
		return {
			cacheKey: t.href,
			url: t.href
		};
	}
	let { revision: n, url: r } = e;
	if (!r) throw new t("add-to-cache-list-unexpected-type", { entry: e });
	if (!n) {
		let e = new URL(r, location.href);
		return {
			cacheKey: e.href,
			url: e.href
		};
	}
	let i = new URL(r, location.href), a = new URL(r, location.href);
	return i.searchParams.set(s, n), {
		cacheKey: i.href,
		url: a.href
	};
}
//#endregion
//#region node_modules/workbox-precaching/utils/PrecacheInstallReportPlugin.js
var l = class {
	constructor() {
		this.updatedURLs = [], this.notUpdatedURLs = [], this.handlerWillStart = async ({ request: e, state: t }) => {
			t && (t.originalRequest = e);
		}, this.cachedResponseWillBeUsed = async ({ event: e, state: t, cachedResponse: n }) => {
			if (e.type === "install" && t && t.originalRequest && t.originalRequest instanceof Request) {
				let e = t.originalRequest.url;
				n ? this.notUpdatedURLs.push(e) : this.updatedURLs.push(e);
			}
			return n;
		};
	}
}, u = class {
	constructor({ precacheController: e }) {
		this.cacheKeyWillBeUsed = async ({ request: e, params: t }) => {
			let n = t?.cacheKey || this._precacheController.getCacheKeyForURL(e.url);
			return n ? new Request(n, { headers: e.headers }) : e;
		}, this._precacheController = e;
	}
}, d;
function f() {
	if (d === void 0) {
		let e = new Response("");
		if ("body" in e) try {
			new Response(e.body), d = !0;
		} catch {
			d = !1;
		}
		d = !1;
	}
	return d;
}
//#endregion
//#region node_modules/workbox-core/copyResponse.js
async function p(e, n) {
	let r = null;
	if (e.url && (r = new URL(e.url).origin), r !== self.location.origin) throw new t("cross-origin-copy-response", { origin: r });
	let i = e.clone(), a = {
		headers: new Headers(i.headers),
		status: i.status,
		statusText: i.statusText
	}, o = n ? n(a) : a, s = f() ? i.body : await i.blob();
	return new Response(s, o);
}
//#endregion
//#region node_modules/workbox-core/_private/getFriendlyURL.js
var m = (e) => new URL(String(e), location.href).href.replace(RegExp(`^${location.origin}`), "");
//#endregion
//#region node_modules/workbox-core/_private/cacheMatchIgnoreParams.js
function h(e, t) {
	let n = new URL(e);
	for (let e of t) n.searchParams.delete(e);
	return n.href;
}
async function g(e, t, n, r) {
	let i = h(t.url, n);
	if (t.url === i) return e.match(t, r);
	let a = Object.assign(Object.assign({}, r), { ignoreSearch: !0 }), o = await e.keys(t, a);
	for (let t of o) if (i === h(t.url, n)) return e.match(t, r);
}
//#endregion
//#region node_modules/workbox-core/_private/Deferred.js
var v = class {
	constructor() {
		this.promise = new Promise((e, t) => {
			this.resolve = e, this.reject = t;
		});
	}
}, y = /* @__PURE__ */ new Set();
//#endregion
//#region node_modules/workbox-core/_private/executeQuotaErrorCallbacks.js
async function b() {
	for (let e of y) await e();
}
//#endregion
//#region node_modules/workbox-core/_private/timeout.js
function x(e) {
	return new Promise((t) => setTimeout(t, e));
}
//#endregion
//#region node_modules/workbox-strategies/_version.js
try {
	self["workbox:strategies:7.4.0"] && _();
} catch {}
//#endregion
//#region node_modules/workbox-strategies/StrategyHandler.js
function S(e) {
	return typeof e == "string" ? new Request(e) : e;
}
var C = class {
	constructor(e, t) {
		this._cacheKeys = {}, Object.assign(this, t), this.event = t.event, this._strategy = e, this._handlerDeferred = new v(), this._extendLifetimePromises = [], this._plugins = [...e.plugins], this._pluginStateMap = /* @__PURE__ */ new Map();
		for (let e of this._plugins) this._pluginStateMap.set(e, {});
		this.event.waitUntil(this._handlerDeferred.promise);
	}
	async fetch(e) {
		let { event: n } = this, r = S(e);
		if (r.mode === "navigate" && n instanceof FetchEvent && n.preloadResponse) {
			let e = await n.preloadResponse;
			if (e) return e;
		}
		let i = this.hasCallback("fetchDidFail") ? r.clone() : null;
		try {
			for (let e of this.iterateCallbacks("requestWillFetch")) r = await e({
				request: r.clone(),
				event: n
			});
		} catch (e) {
			if (e instanceof Error) throw new t("plugin-error-request-will-fetch", { thrownErrorMessage: e.message });
		}
		let a = r.clone();
		try {
			let e;
			e = await fetch(r, r.mode === "navigate" ? void 0 : this._strategy.fetchOptions);
			for (let t of this.iterateCallbacks("fetchDidSucceed")) e = await t({
				event: n,
				request: a,
				response: e
			});
			return e;
		} catch (e) {
			throw i && await this.runCallbacks("fetchDidFail", {
				error: e,
				event: n,
				originalRequest: i.clone(),
				request: a.clone()
			}), e;
		}
	}
	async fetchAndCachePut(e) {
		let t = await this.fetch(e), n = t.clone();
		return this.waitUntil(this.cachePut(e, n)), t;
	}
	async cacheMatch(e) {
		let t = S(e), n, { cacheName: r, matchOptions: i } = this._strategy, a = await this.getCacheKey(t, "read"), o = Object.assign(Object.assign({}, i), { cacheName: r });
		n = await caches.match(a, o);
		for (let e of this.iterateCallbacks("cachedResponseWillBeUsed")) n = await e({
			cacheName: r,
			matchOptions: i,
			cachedResponse: n,
			request: a,
			event: this.event
		}) || void 0;
		return n;
	}
	async cachePut(e, n) {
		let r = S(e);
		await x(0);
		let i = await this.getCacheKey(r, "write");
		if (!n) throw new t("cache-put-with-no-response", { url: m(i.url) });
		let a = await this._ensureResponseSafeToCache(n);
		if (!a) return !1;
		let { cacheName: o, matchOptions: s } = this._strategy, c = await self.caches.open(o), l = this.hasCallback("cacheDidUpdate"), u = l ? await g(c, i.clone(), ["__WB_REVISION__"], s) : null;
		try {
			await c.put(i, l ? a.clone() : a);
		} catch (e) {
			if (e instanceof Error) throw e.name === "QuotaExceededError" && await b(), e;
		}
		for (let e of this.iterateCallbacks("cacheDidUpdate")) await e({
			cacheName: o,
			oldResponse: u,
			newResponse: a.clone(),
			request: i,
			event: this.event
		});
		return !0;
	}
	async getCacheKey(e, t) {
		let n = `${e.url} | ${t}`;
		if (!this._cacheKeys[n]) {
			let r = e;
			for (let e of this.iterateCallbacks("cacheKeyWillBeUsed")) r = S(await e({
				mode: t,
				request: r,
				event: this.event,
				params: this.params
			}));
			this._cacheKeys[n] = r;
		}
		return this._cacheKeys[n];
	}
	hasCallback(e) {
		for (let t of this._strategy.plugins) if (e in t) return !0;
		return !1;
	}
	async runCallbacks(e, t) {
		for (let n of this.iterateCallbacks(e)) await n(t);
	}
	*iterateCallbacks(e) {
		for (let t of this._strategy.plugins) if (typeof t[e] == "function") {
			let n = this._pluginStateMap.get(t);
			yield (r) => {
				let i = Object.assign(Object.assign({}, r), { state: n });
				return t[e](i);
			};
		}
	}
	waitUntil(e) {
		return this._extendLifetimePromises.push(e), e;
	}
	async doneWaiting() {
		for (; this._extendLifetimePromises.length;) {
			let e = this._extendLifetimePromises.splice(0), t = (await Promise.allSettled(e)).find((e) => e.status === "rejected");
			if (t) throw t.reason;
		}
	}
	destroy() {
		this._handlerDeferred.resolve(null);
	}
	async _ensureResponseSafeToCache(e) {
		let t = e, n = !1;
		for (let e of this.iterateCallbacks("cacheWillUpdate")) if (t = await e({
			request: this.request,
			response: t,
			event: this.event
		}) || void 0, n = !0, !t) break;
		return n || t && t.status !== 200 && (t = void 0), t;
	}
}, w = class {
	constructor(e = {}) {
		this.cacheName = a.getRuntimeName(e.cacheName), this.plugins = e.plugins || [], this.fetchOptions = e.fetchOptions, this.matchOptions = e.matchOptions;
	}
	handle(e) {
		let [t] = this.handleAll(e);
		return t;
	}
	handleAll(e) {
		e instanceof FetchEvent && (e = {
			event: e,
			request: e.request
		});
		let t = e.event, n = typeof e.request == "string" ? new Request(e.request) : e.request, r = "params" in e ? e.params : void 0, i = new C(this, {
			event: t,
			request: n,
			params: r
		}), a = this._getResponse(i, n, t);
		return [a, this._awaitComplete(a, i, n, t)];
	}
	async _getResponse(e, n, r) {
		await e.runCallbacks("handlerWillStart", {
			event: r,
			request: n
		});
		let i;
		try {
			if (i = await this._handle(n, e), !i || i.type === "error") throw new t("no-response", { url: n.url });
		} catch (t) {
			if (t instanceof Error) {
				for (let a of e.iterateCallbacks("handlerDidError")) if (i = await a({
					error: t,
					event: r,
					request: n
				}), i) break;
			}
			if (!i) throw t;
		}
		for (let t of e.iterateCallbacks("handlerWillRespond")) i = await t({
			event: r,
			request: n,
			response: i
		});
		return i;
	}
	async _awaitComplete(e, t, n, r) {
		let i, a;
		try {
			i = await e;
		} catch {}
		try {
			await t.runCallbacks("handlerDidRespond", {
				event: r,
				request: n,
				response: i
			}), await t.doneWaiting();
		} catch (e) {
			e instanceof Error && (a = e);
		}
		if (await t.runCallbacks("handlerDidComplete", {
			event: r,
			request: n,
			response: i,
			error: a
		}), t.destroy(), a) throw a;
	}
}, T = class e extends w {
	constructor(t = {}) {
		t.cacheName = a.getPrecacheName(t.cacheName), super(t), this._fallbackToNetwork = t.fallbackToNetwork !== !1, this.plugins.push(e.copyRedirectedCacheableResponsesPlugin);
	}
	async _handle(e, t) {
		return await t.cacheMatch(e) || (t.event && t.event.type === "install" ? await this._handleInstall(e, t) : await this._handleFetch(e, t));
	}
	async _handleFetch(e, n) {
		let r, i = n.params || {};
		if (this._fallbackToNetwork) {
			let t = i.integrity, a = e.integrity, o = !a || a === t;
			r = await n.fetch(new Request(e, { integrity: e.mode === "no-cors" ? void 0 : a || t })), t && o && e.mode !== "no-cors" && (this._useDefaultCacheabilityPluginIfNeeded(), await n.cachePut(e, r.clone()));
		} else throw new t("missing-precache-entry", {
			cacheName: this.cacheName,
			url: e.url
		});
		return r;
	}
	async _handleInstall(e, n) {
		this._useDefaultCacheabilityPluginIfNeeded();
		let r = await n.fetch(e);
		if (!await n.cachePut(e, r.clone())) throw new t("bad-precaching-response", {
			url: e.url,
			status: r.status
		});
		return r;
	}
	_useDefaultCacheabilityPluginIfNeeded() {
		let t = null, n = 0;
		for (let [r, i] of this.plugins.entries()) i !== e.copyRedirectedCacheableResponsesPlugin && (i === e.defaultPrecacheCacheabilityPlugin && (t = r), i.cacheWillUpdate && n++);
		n === 0 ? this.plugins.push(e.defaultPrecacheCacheabilityPlugin) : n > 1 && t !== null && this.plugins.splice(t, 1);
	}
};
T.defaultPrecacheCacheabilityPlugin = { async cacheWillUpdate({ response: e }) {
	return !e || e.status >= 400 ? null : e;
} }, T.copyRedirectedCacheableResponsesPlugin = { async cacheWillUpdate({ response: e }) {
	return e.redirected ? await p(e) : e;
} };
//#endregion
//#region node_modules/workbox-precaching/PrecacheController.js
var E = class {
	constructor({ cacheName: e, plugins: t = [], fallbackToNetwork: n = !0 } = {}) {
		this._urlsToCacheKeys = /* @__PURE__ */ new Map(), this._urlsToCacheModes = /* @__PURE__ */ new Map(), this._cacheKeysToIntegrities = /* @__PURE__ */ new Map(), this._strategy = new T({
			cacheName: a.getPrecacheName(e),
			plugins: [...t, new u({ precacheController: this })],
			fallbackToNetwork: n
		}), this.install = this.install.bind(this), this.activate = this.activate.bind(this);
	}
	get strategy() {
		return this._strategy;
	}
	precache(e) {
		this.addToCacheList(e), this._installAndActiveListenersAdded ||= (self.addEventListener("install", this.install), self.addEventListener("activate", this.activate), !0);
	}
	addToCacheList(e) {
		let n = [];
		for (let r of e) {
			typeof r == "string" ? n.push(r) : r && r.revision === void 0 && n.push(r.url);
			let { cacheKey: e, url: i } = c(r), a = typeof r != "string" && r.revision ? "reload" : "default";
			if (this._urlsToCacheKeys.has(i) && this._urlsToCacheKeys.get(i) !== e) throw new t("add-to-cache-list-conflicting-entries", {
				firstEntry: this._urlsToCacheKeys.get(i),
				secondEntry: e
			});
			if (typeof r != "string" && r.integrity) {
				if (this._cacheKeysToIntegrities.has(e) && this._cacheKeysToIntegrities.get(e) !== r.integrity) throw new t("add-to-cache-list-conflicting-integrities", { url: i });
				this._cacheKeysToIntegrities.set(e, r.integrity);
			}
			if (this._urlsToCacheKeys.set(i, e), this._urlsToCacheModes.set(i, a), n.length > 0) {
				let e = `Workbox is precaching URLs without revision info: ${n.join(", ")}\nThis is generally NOT safe. Learn more at https://bit.ly/wb-precache`;
				console.warn(e);
			}
		}
	}
	install(e) {
		return o(e, async () => {
			let t = new l();
			this.strategy.plugins.push(t);
			for (let [t, n] of this._urlsToCacheKeys) {
				let r = this._cacheKeysToIntegrities.get(n), i = this._urlsToCacheModes.get(t), a = new Request(t, {
					integrity: r,
					cache: i,
					credentials: "same-origin"
				});
				await Promise.all(this.strategy.handleAll({
					params: { cacheKey: n },
					request: a,
					event: e
				}));
			}
			let { updatedURLs: n, notUpdatedURLs: r } = t;
			return {
				updatedURLs: n,
				notUpdatedURLs: r
			};
		});
	}
	activate(e) {
		return o(e, async () => {
			let e = await self.caches.open(this.strategy.cacheName), t = await e.keys(), n = new Set(this._urlsToCacheKeys.values()), r = [];
			for (let i of t) n.has(i.url) || (await e.delete(i), r.push(i.url));
			return { deletedURLs: r };
		});
	}
	getURLsToCacheKeys() {
		return this._urlsToCacheKeys;
	}
	getCachedURLs() {
		return [...this._urlsToCacheKeys.keys()];
	}
	getCacheKeyForURL(e) {
		let t = new URL(e, location.href);
		return this._urlsToCacheKeys.get(t.href);
	}
	getIntegrityForCacheKey(e) {
		return this._cacheKeysToIntegrities.get(e);
	}
	async matchPrecache(e) {
		let t = e instanceof Request ? e.url : e, n = this.getCacheKeyForURL(t);
		if (n) return (await self.caches.open(this.strategy.cacheName)).match(n);
	}
	createHandlerBoundToURL(e) {
		let n = this.getCacheKeyForURL(e);
		if (!n) throw new t("non-precached-url", { url: e });
		return (t) => (t.request = new Request(e), t.params = Object.assign({ cacheKey: n }, t.params), this.strategy.handle(t));
	}
}, D, O = () => (D ||= new E(), D);
//#endregion
//#region node_modules/workbox-routing/_version.js
try {
	self["workbox:routing:7.4.0"] && _();
} catch {}
//#endregion
//#region node_modules/workbox-routing/utils/normalizeHandler.js
var k = (e) => e && typeof e == "object" ? e : { handle: e }, A = class {
	constructor(e, t, n = "GET") {
		this.handler = k(t), this.match = e, this.method = n;
	}
	setCatchHandler(e) {
		this.catchHandler = k(e);
	}
}, j = class extends A {
	constructor(e, t, n) {
		super(({ url: t }) => {
			let n = e.exec(t.href);
			if (n && (t.origin === location.origin || n.index === 0)) return n.slice(1);
		}, t, n);
	}
}, M = class {
	constructor() {
		this._routes = /* @__PURE__ */ new Map(), this._defaultHandlerMap = /* @__PURE__ */ new Map();
	}
	get routes() {
		return this._routes;
	}
	addFetchListener() {
		self.addEventListener("fetch", ((e) => {
			let { request: t } = e, n = this.handleRequest({
				request: t,
				event: e
			});
			n && e.respondWith(n);
		}));
	}
	addCacheListener() {
		self.addEventListener("message", ((e) => {
			if (e.data && e.data.type === "CACHE_URLS") {
				let { payload: t } = e.data, n = Promise.all(t.urlsToCache.map((t) => {
					typeof t == "string" && (t = [t]);
					let n = new Request(...t);
					return this.handleRequest({
						request: n,
						event: e
					});
				}));
				e.waitUntil(n), e.ports && e.ports[0] && n.then(() => e.ports[0].postMessage(!0));
			}
		}));
	}
	handleRequest({ request: e, event: t }) {
		let n = new URL(e.url, location.href);
		if (!n.protocol.startsWith("http")) return;
		let r = n.origin === location.origin, { params: i, route: a } = this.findMatchingRoute({
			event: t,
			request: e,
			sameOrigin: r,
			url: n
		}), o = a && a.handler, s = e.method;
		if (!o && this._defaultHandlerMap.has(s) && (o = this._defaultHandlerMap.get(s)), !o) return;
		let c;
		try {
			c = o.handle({
				url: n,
				request: e,
				event: t,
				params: i
			});
		} catch (e) {
			c = Promise.reject(e);
		}
		let l = a && a.catchHandler;
		return c instanceof Promise && (this._catchHandler || l) && (c = c.catch(async (r) => {
			if (l) try {
				return await l.handle({
					url: n,
					request: e,
					event: t,
					params: i
				});
			} catch (e) {
				e instanceof Error && (r = e);
			}
			if (this._catchHandler) return this._catchHandler.handle({
				url: n,
				request: e,
				event: t
			});
			throw r;
		})), c;
	}
	findMatchingRoute({ url: e, sameOrigin: t, request: n, event: r }) {
		let i = this._routes.get(n.method) || [];
		for (let a of i) {
			let i, o = a.match({
				url: e,
				sameOrigin: t,
				request: n,
				event: r
			});
			if (o) return i = o, (Array.isArray(i) && i.length === 0 || o.constructor === Object && Object.keys(o).length === 0 || typeof o == "boolean") && (i = void 0), {
				route: a,
				params: i
			};
		}
		return {};
	}
	setDefaultHandler(e, t = "GET") {
		this._defaultHandlerMap.set(t, k(e));
	}
	setCatchHandler(e) {
		this._catchHandler = k(e);
	}
	registerRoute(e) {
		this._routes.has(e.method) || this._routes.set(e.method, []), this._routes.get(e.method).push(e);
	}
	unregisterRoute(e) {
		if (!this._routes.has(e.method)) throw new t("unregister-route-but-not-found-with-method", { method: e.method });
		let n = this._routes.get(e.method).indexOf(e);
		if (n > -1) this._routes.get(e.method).splice(n, 1);
		else throw new t("unregister-route-route-not-registered");
	}
}, N, P = () => (N || (N = new M(), N.addFetchListener(), N.addCacheListener()), N);
//#endregion
//#region node_modules/workbox-routing/registerRoute.js
function F(e, n, r) {
	let i;
	if (typeof e == "string") {
		let t = new URL(e, location.href);
		i = new A(({ url: e }) => e.href === t.href, n, r);
	} else if (e instanceof RegExp) i = new j(e, n, r);
	else if (typeof e == "function") i = new A(e, n, r);
	else if (e instanceof A) i = e;
	else throw new t("unsupported-route-type", {
		moduleName: "workbox-routing",
		funcName: "registerRoute",
		paramName: "capture"
	});
	return P().registerRoute(i), i;
}
//#endregion
//#region node_modules/workbox-precaching/utils/removeIgnoredSearchParams.js
function I(e, t = []) {
	for (let n of [...e.searchParams.keys()]) t.some((e) => e.test(n)) && e.searchParams.delete(n);
	return e;
}
//#endregion
//#region node_modules/workbox-precaching/utils/generateURLVariations.js
function* L(e, { ignoreURLParametersMatching: t = [/^utm_/, /^fbclid$/], directoryIndex: n = "index.html", cleanURLs: r = !0, urlManipulation: i } = {}) {
	let a = new URL(e, location.href);
	a.hash = "", yield a.href;
	let o = I(a, t);
	if (yield o.href, n && o.pathname.endsWith("/")) {
		let e = new URL(o.href);
		e.pathname += n, yield e.href;
	}
	if (r) {
		let e = new URL(o.href);
		e.pathname += ".html", yield e.href;
	}
	if (i) {
		let e = i({ url: a });
		for (let t of e) yield t.href;
	}
}
//#endregion
//#region node_modules/workbox-precaching/PrecacheRoute.js
var R = class extends A {
	constructor(e, t) {
		super(({ request: n }) => {
			let r = e.getURLsToCacheKeys();
			for (let i of L(n.url, t)) {
				let t = r.get(i);
				if (t) return {
					cacheKey: t,
					integrity: e.getIntegrityForCacheKey(t)
				};
			}
		}, e.strategy);
	}
};
//#endregion
//#region node_modules/workbox-precaching/addRoute.js
function z(e) {
	F(new R(O(), e));
}
//#endregion
//#region node_modules/workbox-precaching/utils/deleteOutdatedCaches.js
var B = "-precache-", V = async (e, t = B) => {
	let n = (await self.caches.keys()).filter((n) => n.includes(t) && n.includes(self.registration.scope) && n !== e);
	return await Promise.all(n.map((e) => self.caches.delete(e))), n;
};
//#endregion
//#region node_modules/workbox-precaching/cleanupOutdatedCaches.js
function H() {
	self.addEventListener("activate", ((e) => {
		let t = a.getPrecacheName();
		e.waitUntil(V(t).then((e) => {}));
	}));
}
//#endregion
//#region node_modules/workbox-precaching/matchPrecache.js
function U(e) {
	return O().matchPrecache(e);
}
//#endregion
//#region node_modules/workbox-precaching/precache.js
function W(e) {
	O().precache(e);
}
//#endregion
//#region node_modules/workbox-precaching/precacheAndRoute.js
function G(e, t) {
	W(e), z(t);
}
//#endregion
//#region src/sw.ts
var K = "./index.html";
G([{"revision":"bd42baba274c8117ace3eb93dd0c7cbe","url":"./assets/_app-rnwy2yf6.js"},{"revision":"f9e361130bc9b4acaa5c2d69a4d8bc1f","url":"./assets/_app.ext._extensionId-di4p8ys2.js"},{"revision":"4ca56ee12c0b57c744308886d17ec172","url":"./assets/_app.index-i7xxeio6.js"},{"revision":"389c96fbeb02ac0b521fcdbc8131608e","url":"./assets/_app.insights-ng9kyvpo.js"},{"revision":"11fbdce46788c54ba405bc92974ca08d","url":"./assets/_app.kanban-nb1gxr24.js"},{"revision":"5045081757165f15d8eaf72d8a98b9e0","url":"./assets/_app.logs-e7hxg68s.js"},{"revision":"dfd2bc70532bbd7d4fc8ce0e8cc47988","url":"./assets/_app.memory-paqeghow.js"},{"revision":"5d73768cab0b80b070218e0deb9c8d48","url":"./assets/_app.profiles-bl1lbbg4.js"},{"revision":"90f512387cd33ea160f192a136992bee","url":"./assets/_app.session._sessionId-kbgcd55z.js"},{"revision":"a7cc7e05338be29594f524d8801fd007","url":"./assets/_app.settings-iibk155m.js"},{"revision":"00d57e8eb75e7906415ea64150ea8238","url":"./assets/_app.settings._section-i445ghsr.js"},{"revision":"93f4aaf6f5e63ee0b277a7ab774eba0d","url":"./assets/_app.skills-mig8wa6b.js"},{"revision":"26f26050b141232605afd5e404d44cf7","url":"./assets/_app.tasks-m197o5jg.js"},{"revision":"7797be899a3fc5016352ac0908699cd0","url":"./assets/_app.todos-gom3wm4o.js"},{"revision":"e554c5283cb7d624c053e9dfaf121b66","url":"./assets/_app.workspaces-lspgu3jd.js"},{"revision":"29c6d7d8fee378a71eab645218087b5c","url":"./assets/AppShell-mqnexog0.js"},{"revision":"03af57e0fbd13f628c0c2154272e93a4","url":"./assets/ChatPage-by418t8f.js"},{"revision":"ec4cfb1fc7e563b15fe0d8f578efd2b5","url":"./assets/copied-nyfhtc3a.js"},{"revision":"e94dc57493d44c2b1a8eb3314736e55c","url":"./assets/createLucideIcon-ctmyk2p0.js"},{"revision":"891bb55637134807aba6875dc50b487c","url":"./assets/Dialog-mtofjxe1.js"},{"revision":"759d0004164dd68e5b587a8e9bc6a2b6","url":"./assets/endpoints-godfc8fq.js"},{"revision":"39d34090dece3f9a3a7487b4c1027f0f","url":"./assets/Field-gul79ksr.js"},{"revision":"1f48062b2cbae6a4f2488fd65aa6d85e","url":"./assets/highlighted-body-KPVGNVTW-fl9lvx4s.js"},{"revision":"8884f690ae3f6acaa9584bff4d028d00","url":"./assets/HubRoute-fg0p5bjd.js"},{"revision":"4cef1eba950fc026df721dc37ef0b6fb","url":"./assets/index-ibutm57u.js"},{"revision":"86e216535c7aac4edaf7ad4932a85757","url":"./assets/index-no6lmu1w.css"},{"revision":"8d3488bf31abb88017e1f03f64d48c59","url":"./assets/jsx-runtime-oxv8l9pt.js"},{"revision":"4cc779b00a72f2c6981128dd1393be67","url":"./assets/lazyRouteComponent-ifihebll.js"},{"revision":"bdcfdf94526ba763bd299a4a9fc8845a","url":"./assets/link-l0rusqgb.js"},{"revision":"f3207f5e61abe96411a2c2806ce2db76","url":"./assets/login-g84l2yjh.js"},{"revision":"73bce5d93527835478b57f6901314853","url":"./assets/matchContext-oa079l0i.js"},{"revision":"32f9fe693d14729a9732240a034e1de1","url":"./assets/mermaid-HWGCJPDP-b1hc8sot.js"},{"revision":"fd7dd0c269d902acb5cb670fe3b478ad","url":"./assets/not-found-ccnnac39.js"},{"revision":"cc397d1bbc2309c2bbadb8d546795e0c","url":"./assets/onboarding-jt1dyusz.js"},{"revision":"1bfbb82b16f511f11efd961fe47a1a93","url":"./assets/passkeys-ktie3sr0.js"},{"revision":"3e4f1f48a48bd0f5501dcb45ced7358d","url":"./assets/root-mbklpuv4.js"},{"revision":"6ab3d6cbe8cd7fa426c318f1bdcd4773","url":"./assets/SessionListPanel-m0hqvc0o.js"},{"revision":"bad1d09c287a7ba33a4f4d2fc2576acd","url":"./assets/SettingsLayout-fsr8a742.js"},{"revision":"8f09e302947c71791848f6fe22a19ec7","url":"./assets/share._token-iuzm9cin.js"},{"revision":"1856dab1a2680e2cc6ac819d1c61aa10","url":"./assets/States-gv14sjam.js"},{"revision":"2a3d429063bc6397a399304f64f0373a","url":"./assets/Toaster-hlazlkon.js"},{"revision":"1cadbccf954748127ef6c10fc0f7224b","url":"./assets/useForm-j0xo39d7.js"},{"revision":"a65d0938b19188176788901a88dbde28","url":"./assets/useLocale-hkslo53u.js"},{"revision":"0edd52439ab105b38a75aea738a25078","url":"./assets/useSelector-olfbz03p.js"},{"revision":"bfeb869403ca0110dedc4a8f30f8cb62","url":"./index.html"},{"revision":"dfc02d3012147ec40b9a27d9a06effae","url":"./manifest.webmanifest"}]), H(), self.addEventListener("message", (e) => {
	let t = e.data;
	typeof t == "object" && t && t.type === "SKIP_WAITING" && self.skipWaiting();
}), self.addEventListener("activate", (e) => {
	e.waitUntil(self.clients.claim());
});
function q(e, t) {
	let n = e.pathname.startsWith(t.pathname) ? e.pathname.slice(t.pathname.length) : e.pathname;
	return n.startsWith("api/") || n === "health" || n.startsWith("extensions/") || n.startsWith("plugins/") || n.startsWith("dashboard-plugins/") || n === "sw.js" || !n.startsWith("static/") && n.includes("/static/");
}
self.addEventListener("fetch", (e) => {
	let t = e.request;
	if (t.method !== "GET") return;
	let n = new URL(t.url);
	n.origin === self.location.origin && (q(n, new URL(self.registration.scope)) || t.mode === "navigate" && e.respondWith((async () => {
		try {
			return await fetch(t);
		} catch {
			return await U(K) || new Response("Hermes is offline and no cached shell is available.", {
				status: 503,
				headers: { "Content-Type": "text/plain; charset=utf-8" }
			});
		}
	})()));
});
//#endregion
