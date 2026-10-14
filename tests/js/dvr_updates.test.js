'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../app/relaytv_app/static/ui/app.js'), 'utf8');
function functionSource(name, nextName){
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf(`function ${nextName}(`, start + 1);
  assert.ok(start >= 0 && end > start, `${name} must exist`);
  return source.slice(start, end);
}
function fixture(){
  const renders = [];
  const context = vm.createContext({
    __lastStatus: {playing:true, dvr:{seekable:false, buffer_start:0, live_edge:0, delay_sec:0}},
    _uiEventMarkAlive(){},
    renderStatus(status){ renders.push(status); },
  });
  vm.runInContext(functionSource('_mergePlaybackStateIntoStatus', '_shouldRefreshFullStatus'), context);
  vm.runInContext(functionSource('_applyUiPlaybackEvent', '_applyUiStatusEvent'), context);
  return {context, renders};
}
const buffered = {seekable:true, buffer_start:20, live_edge:100, delay_sec:50};

test('fast polling updates DVR bounds and seekability alongside position', () => {
  const {context} = fixture();
  const base = context.__lastStatus;
  const result = context._mergePlaybackStateIntoStatus(base, {playing:true, position:50, dvr:buffered});
  assert.equal(result.dvr, buffered);
  assert.equal(result.position, 50);
  assert.equal(base.dvr.seekable, false);
});

test('realtime playback events render updated DVR delay and live state', () => {
  const {context, renders} = fixture();
  context._applyUiPlaybackEvent({playing:true, position:50, dvr:buffered});
  assert.equal(context.__lastStatus.dvr, buffered);
  assert.equal(renders.at(-1).dvr.delay_sec, 50);
  const live = {...buffered, delay_sec:0, is_at_live:true};
  context._applyUiPlaybackEvent({playing:true, position:100, dvr:live});
  assert.equal(renders.at(-1).dvr, live);
});

test('explicit null clears DVR when changing to on-demand playback', () => {
  const {context, renders} = fixture();
  context._applyUiPlaybackEvent({playing:true, dvr:buffered});
  context._applyUiPlaybackEvent({playing:true, position:0, dvr:null});
  assert.equal(context.__lastStatus.dvr, null);
  assert.equal(renders.at(-1).dvr, null);
});

test('partial updates without DVR preserve the last known buffer', () => {
  const {context} = fixture();
  context._applyUiPlaybackEvent({playing:true, dvr:buffered});
  context._applyUiPlaybackEvent({playing:true, position:60});
  assert.equal(context.__lastStatus.dvr, buffered);
});

test('now card toggles hasDvr and isDvrRewound classes for seekable live DVR', () => {
  const context = vm.createContext({});
  vm.runInContext(functionSource('_updateNowCardClasses', 'renderStatus'), context);
  function mockCard(){
    const classes = new Set();
    return {
      classList: {
        toggle(name, val){ if (val) classes.add(name); else classes.delete(name); },
        contains(name){ return classes.has(name); },
      }
    };
  }

  const card = mockCard();

  // Live at edge with seekable DVR: hasDvr is true, isDvrRewound is false
  context._updateNowCardClasses(card, {
    hasNow: true,
    paused: false,
    liveNow: true,
    dvr: {seekable: true, is_at_live: true},
    dvrAtLive: true,
    ended: false,
  });
  assert.equal(card.classList.contains('isLive'), true);
  assert.equal(card.classList.contains('hasDvr'), true);
  assert.equal(card.classList.contains('isDvrRewound'), false);

  // Live rewound with seekable DVR: both hasDvr and isDvrRewound are true
  context._updateNowCardClasses(card, {
    hasNow: true,
    paused: false,
    liveNow: true,
    dvr: {seekable: true, is_at_live: false},
    dvrAtLive: false,
    ended: false,
  });
  assert.equal(card.classList.contains('isLive'), true);
  assert.equal(card.classList.contains('hasDvr'), true);
  assert.equal(card.classList.contains('isDvrRewound'), true);

  // Live stream without seekable DVR: hasDvr is false, isDvrRewound is false
  context._updateNowCardClasses(card, {
    hasNow: true,
    paused: false,
    liveNow: true,
    dvr: {seekable: false},
    dvrAtLive: true,
    ended: false,
  });
  assert.equal(card.classList.contains('isLive'), true);
  assert.equal(card.classList.contains('hasDvr'), false);
  assert.equal(card.classList.contains('isDvrRewound'), false);

  // VOD playback: isLive and hasDvr are false
  context._updateNowCardClasses(card, {
    hasNow: true,
    paused: false,
    liveNow: false,
    dvr: null,
    dvrAtLive: true,
    ended: false,
  });
  assert.equal(card.classList.contains('isLive'), false);
  assert.equal(card.classList.contains('hasDvr'), false);
  assert.equal(card.classList.contains('isDvrRewound'), false);

  // Idle state: isIdle is true, hasDvr is false
  context._updateNowCardClasses(card, {
    hasNow: false,
    paused: false,
    liveNow: false,
    dvr: null,
    dvrAtLive: true,
    ended: false,
  });
  assert.equal(card.classList.contains('isIdle'), true);
  assert.equal(card.classList.contains('hasDvr'), false);
});
