// HWEB-97: report genuine human activity to the Talaria publisher.
// A tab holds a server-timed 90s lease while it is visible, focused, and
// receiving trusted keyboard, pointer/touch, or wheel input. Page load,
// focus alone, mouse movement, programmatic scroll, streaming, SSE traffic
// and timers never renew it. Hidden/blur/pagehide revoke this tab's lease;
// expiry on the server is authoritative when a revocation is lost.
(function(){
  'use strict';
  if(typeof document==='undefined'||typeof window==='undefined'||typeof fetch!=='function') return;
  var THROTTLE_MS=15000;
  var tabId=(function(){
    try{ if(window.crypto&&window.crypto.randomUUID) return window.crypto.randomUUID().replace(/-/g,''); }catch(_){}
    var bytes=new Uint8Array(16);
    try{ window.crypto.getRandomValues(bytes); }catch(_){ for(var i=0;i<16;i++) bytes[i]=Math.floor(Math.random()*256); }
    return Array.prototype.map.call(bytes,function(b){return ('0'+b.toString(16)).slice(-2);}).join('');
  })();
  var held=false, lastSent=0, chain=Promise.resolve();

  function post(active, keepalive){
    var opts={
      method:'POST',
      credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tab_id:tabId, active:active}),
      keepalive:!!keepalive
    };
    // Bound the renewal so a slow server never stalls the write waiting on settle().
    try{ if(typeof AbortSignal!=='undefined'&&AbortSignal.timeout) opts.signal=AbortSignal.timeout(4000); }catch(_){}
    var url;
    try{ url=new URL('api/talaria/presence',document.baseURI||location.href).href; }catch(_){ url='api/talaria/presence'; }
    return fetch(url,opts).then(function(){},function(){});
  }
  // Per-tab updates are serialized: a revocation is never sent while the
  // renewal before it is still in flight, so the server cannot apply them out
  // of order and resurrect a lease the tab just gave up.
  function send(active, keepalive){
    chain=chain.then(function(){ return post(active,keepalive); });
    return chain;
  }
  function qualifies(e){
    if(!e||e.isTrusted!==true) return false;
    if(document.visibilityState!=='visible') return false;
    if(typeof document.hasFocus==='function'&&!document.hasFocus()) return false;
    return true;
  }
  function onInput(e){
    if(!qualifies(e)) return;
    var now=Date.now();
    if(held&&now-lastSent<THROTTLE_MS) return;
    held=true; lastSent=now;
    send(true,false);
  }
  function release(){
    if(!held) return;
    held=false; lastSent=0;
    send(false,true);
  }
  var listen={capture:true,passive:true};
  document.addEventListener('keydown',onInput,listen);
  document.addEventListener('pointerdown',onInput,listen);
  document.addEventListener('wheel',onInput,listen);
  document.addEventListener('visibilitychange',function(){ if(document.visibilityState!=='visible') release(); });
  window.addEventListener('blur',release);
  window.addEventListener('pagehide',release);

  window.HermesPresence={
    tabId:tabId,
    // Resolves once every queued or in-flight update has been answered (or failed).
    settle:function(){ return chain; }
  };
})();
