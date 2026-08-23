// Shared realtime transport policy for the browser UI and X11 overlay.
//
// Keep network and DOM adapters outside this module. The injected clock and
// discovery loader make capability recovery, WebSocket probation, and cooldown
// behavior deterministic under Node's dependency-free test runner.
(function exposeRelayTVRealtime(root, factory){
  const api = factory();
  if(typeof module === 'object' && module.exports) module.exports = api;
  if(root) root.RelayTVRealtime = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function buildRelayTVRealtime(){
  'use strict';

  const WEBSOCKET_FAILURE_REASONS = new Set([
    'open_error',
    'protocol_error',
    'stale',
  ]);

  function positiveNumber(value, fallback){
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function createPolicy(options){
    const config = (options && typeof options === 'object') ? options : {};
    const now = typeof config.now === 'function' ? config.now : () => Date.now();
    const fallbackCapabilities = config.fallbackCapabilities || {
      protocol_version:0,
      websocket:{enabled:false},
      sse:{enabled:true},
    };
    const capabilityTtlMs = positiveNumber(config.capabilityTtlMs, 300000);
    const websocketCooldownMs = positiveNumber(config.websocketCooldownMs, 60000);

    let cachedCapabilities = null;
    let cachedCapabilitiesUntil = 0;
    let discoveryPromise = null;
    let websocketBlockedUntil = 0;

    function cacheCapabilities(capabilities){
      cachedCapabilities = capabilities;
      cachedCapabilitiesUntil = now() + capabilityTtlMs;
      return capabilities;
    }

    async function discover(loader, request){
      const settings = (request && typeof request === 'object') ? request : {};
      const force = settings.force === true;
      if(!force && cachedCapabilities && now() < cachedCapabilitiesUntil){
        return cachedCapabilities;
      }
      if(discoveryPromise) return await discoveryPromise;
      discoveryPromise = (async()=>{
        try{
          const capabilities = await loader();
          if(!capabilities || typeof capabilities !== 'object'){
            const error = new Error('invalid realtime capabilities');
            error.cacheFallback = true;
            throw error;
          }
          return cacheCapabilities(capabilities);
        }catch(error){
          // A real legacy response or understood-but-unsupported protocol may
          // be cached for one TTL. Timeouts, 5xx responses, and parse failures
          // are one-cycle fallbacks so the next recovery can discover again.
          if(error && (
            error.legacy === true
            || error.cacheFallback === true
            || Number(error.status) === 404
          )){
            return cacheCapabilities(fallbackCapabilities);
          }
          return fallbackCapabilities;
        }finally{
          discoveryPromise = null;
        }
      })();
      return await discoveryPromise;
    }

    function select(capabilities, request){
      const settings = (request && typeof request === 'object') ? request : {};
      const websocketAvailable = settings.websocketAvailable !== false;
      if(
        capabilities?.websocket?.enabled === true
        && websocketAvailable
        && now() >= websocketBlockedUntil
      ) return 'websocket';
      if(capabilities?.sse?.enabled !== false) return 'sse';
      return 'poll';
    }

    function createWebSocketAttempt(capabilities){
      const heartbeatSec = positiveNumber(capabilities?.heartbeat_sec, 5);
      return {
        proved:false,
        stable:false,
        stableAfterTs:0,
        stabilityMs:heartbeatSec * 2 * 1000,
      };
    }

    function acceptWebSocketEnvelope(attempt, envelope){
      if(
        !attempt
        || !envelope
        || Number(envelope.version) !== 1
        || typeof envelope.event !== 'string'
      ) return null;
      const payload = (
        envelope.data && typeof envelope.data === 'object'
      ) ? envelope.data : {};
      if(!attempt.proved){
        if(envelope.event !== 'hello' || Number(payload.protocol_version) !== 1) return null;
        attempt.proved = true;
        attempt.stableAfterTs = now() + attempt.stabilityMs;
      }
      if(attempt.proved && now() >= attempt.stableAfterTs) attempt.stable = true;
      return {
        event:envelope.event,
        payload,
        sequence:envelope.sequence,
      };
    }

    function retire(kind, reason, websocketAttempt){
      const transport = String(kind || '');
      const cause = String(reason || '');
      const failed = transport === 'websocket' && (
        WEBSOCKET_FAILURE_REASONS.has(cause)
        || (cause === 'closed' && !websocketAttempt?.stable)
      );
      if(failed){
        websocketBlockedUntil = Math.max(websocketBlockedUntil, now() + websocketCooldownMs);
      }
      return {failed, websocketBlockedUntil};
    }

    return {
      acceptWebSocketEnvelope,
      createWebSocketAttempt,
      discover,
      retire,
      select,
      state(){
        return {
          cachedCapabilities,
          cachedCapabilitiesUntil,
          websocketBlockedUntil,
        };
      },
    };
  }

  function createSequenceTracker(){
    let lastSequence = 0;

    function accept(eventName, sequence){
      const name = String(eventName || '').trim();
      if(name === 'hello') lastSequence = 0;
      const nextSequence = Number(sequence || 0);
      if(!(nextSequence > 0)) return {apply:true, gap:false, lastSequence};

      // Heartbeats may repeat the most recent application sequence. They
      // prove liveness but carry no application state to apply.
      if(name === 'ping'){
        const gap = lastSequence > 0 && nextSequence > (lastSequence + 1);
        return {apply:true, gap, lastSequence};
      }
      if(lastSequence > 0 && nextSequence <= lastSequence){
        return {apply:false, gap:false, lastSequence};
      }
      const gap = lastSequence > 0 && nextSequence > (lastSequence + 1);
      lastSequence = nextSequence;
      return {apply:true, gap, lastSequence};
    }

    return {
      accept,
      state(){ return {lastSequence}; },
    };
  }

  return {createPolicy, createSequenceTracker};
});
