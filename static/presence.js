// HWEB-97: report genuine human activity to the Talaria publisher.
// A tab holds a server-timed 90s lease while it is visible, focused, and
// receiving trusted keyboard, pointer/touch, or wheel input. Page load,
// focus alone, mouse movement, programmatic scroll, streaming, SSE traffic
// and timers never renew it. Hidden/blur/pagehide revoke this tab's lease.
//
// Ordering is decided server-side by a per-tab monotonic sequence: every
// update carries a strictly increasing seq and the server ignores any update
// whose seq is not newer than the last one it applied for that tab. So a
// revocation can be dispatched immediately during pagehide/blur (no client
// serialization to wait on) while a still-in-flight lower-seq renewal that
// lands afterward cannot resurrect the lease. Expiry (90s) is authoritative
// if a revocation is dropped entirely.
(function(){
  'use strict';
  if(typeof document==='undefined'||typeof window==='undefined'||typeof fetch!=='function') return;
  var THROTTLE_MS=15000;
  var TIMEOUT_MS=4000;
  var tabId=(function(){
    try{ if(window.crypto&&window.crypto.randomUUID) return window.crypto.randomUUID().replace(/-/g,''); }catch(_){}
    var bytes=new Uint8Array(16);
    try{ window.crypto.getRandomValues(bytes); }catch(_){ for(var i=0;i<16;i++) bytes[i]=Math.floor(Math.random()*256); }
    return Array.prototype.map.call(bytes,function(b){return ('0'+b.toString(16)).slice(-2);}).join('');
  })();
  var held=false, lastSent=0, seq=0, inflight=null, suspended=false;

  function post(active, keepalive){
    seq+=1;
    var opts={
      method:'POST',
      credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tab_id:tabId, active:active, seq:seq}),
      keepalive:!!keepalive
    };
    // Bound the request so a stalled renewal never leaves settle() (and the
    // write awaiting it) pending forever. AbortController + a timer works even
    // even where the one-shot timeout-signal helper is unavailable.
    var controller=null, timer=null;
    try{ controller=(typeof AbortController!=='undefined')?new AbortController():null; }catch(_){ controller=null; }
    if(controller){
      opts.signal=controller.signal;
      timer=setTimeout(function(){ try{controller.abort();}catch(_){} }, TIMEOUT_MS);
    }
    var url;
    try{ url=new URL('api/talaria/presence',document.baseURI||location.href).href; }catch(_){ url='api/talaria/presence'; }
    var settleDone=function(){ if(timer){ clearTimeout(timer); timer=null; } };
    return fetch(url,opts).then(settleDone,settleDone);
  }
  function qualifies(e){
    if(!e||e.isTrusted!==true) return false;
    if(document.visibilityState!=='visible') return false;
    if(typeof document.hasFocus==='function'&&!document.hasFocus()) return false;
    return true;
  }
  function onInput(e){
    // Renewals are suspended for the duration of a scope-changing operation
    // (profile switch, sign out) so a trusted event mid-flight cannot recreate
    // a lease under the outgoing profile/session cookie.
    if(suspended) return;
    if(!qualifies(e)) return;
    var now=Date.now();
    if(held&&now-lastSent<THROTTLE_MS) return;
    held=true; lastSent=now;
    var request=post(true,false);
    inflight=request.then(function(){ if(inflight===request) inflight=null; });
  }
  function release(){
    if(!held) return;
    held=false; lastSent=0;
    // Fire immediately (not chained behind a pending renewal) so the keepalive
    // request is initiated during the lifecycle event; seq ordering stops any
    // in-flight lower-seq renewal from winning at the server.
    post(false,true);
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
    // Resolves once the most recent renewal has been answered (or timed out).
    settle:function(){ return inflight||Promise.resolve(); },
    // Called before an action that changes or ends the authenticated profile
    // scope (profile switch, sign out): revoke the current lease and clear the
    // throttle. The revoke is tracked in `inflight`, so settle() — awaited by
    // api() before the switch/logout write — guarantees the revoke reaches the
    // server while the session is still valid and before the cookie flips.
    reset:function(){
      suspended=true;
      lastSent=0;
      if(!held) return Promise.resolve();
      held=false;
      var request=post(false,true);
      inflight=request.then(function(){ if(inflight===request) inflight=null; });
      return inflight;
    },
    // End the suspension started by reset() once the scope-changing operation
    // has settled, so the destination profile can renew normally again.
    resume:function(){ suspended=false; },
    // Re-establish the lease when a profile switch fails and the tab stays on
    // the original profile: the switch click was genuine input, so a visible,
    // focused tab should not be left without a lease after reset() revoked it.
    // Always ends the suspension, even when it declines to renew, so a failure
    // while hidden can never strand renewals off.
    renew:function(){
      suspended=false;
      if(document.visibilityState!=='visible') return Promise.resolve();
      if(typeof document.hasFocus==='function'&&!document.hasFocus()) return Promise.resolve();
      held=true; lastSent=Date.now();
      var request=post(true,false);
      inflight=request.then(function(){ if(inflight===request) inflight=null; });
      return inflight;
    }
  };
})();
