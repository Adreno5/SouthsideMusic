// Minimal DOM + WAAPI stub that executes the real client.js source.
//
// Goal: prove the swarm branch and the original branch do what we claim, in both
// modes, without a browser. The plugin is a single self-registering module, so
// the stub only has to be faithful about the handful of platform details it
// touches: element tree, MutationObserver delivery, getBoundingClientRect,
// Element.animate, localStorage, closed shadow roots, and requestAnimationFrame.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(path.join(here, '..', 'client.js'), 'utf8');

// ------------------------------------------------------------------ fake DOM

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.nodeName = this.tagName;
    this.childNodes = [];
    this.parentNode = null;
    this.style = {};
    this._attrs = new Map();
    this._classes = new Set();
    this._shadow = null;
    this._animations = [];
    this._rect = { left: 0, top: 0, width: 100, height: 40 };
    this._innerHTML = '';
  }
  get children() { return this.childNodes; }
  get childElementCount() {
    return this.childNodes.filter((n) => n && n.nodeType === 1).length;
  }
  get firstElementChild() {
    return this.childNodes.find((n) => n && n.nodeType === 1) ?? null;
  }
  get firstChild() { return this.childNodes[0] ?? null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] ?? null; }
  get parentElement() { const p = this.parentNode; return p && p.tagName ? p : null; }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n === doc;
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) {
    this._innerHTML = String(v);
    // Two shapes of markup matter here. The layer builds its skeleton by
    // assigning a block that contains `class="cursors"`, and each cursor builds
    // a single svg that later code reaches through `firstChild`. Everything else
    // (svg path text, badge text) is only ever read back as a string.
    if (this._innerHTML.includes('class="cursors"')) {
      this.childNodes = [];
      for (const name of ['cursors', 'overlay', 'badges']) {
        const child = new El(name === 'overlay' ? 'svg' : 'div');
        child.className = name;
        child.parentNode = this;
        this.childNodes.push(child);
      }
      return;
    }
    if (this._innerHTML.trimStart().startsWith('<svg')) {
      const svg = new SvgEl('svg');
      svg.parentNode = this;
      this.childNodes = [svg];
    }
  }
  setAttribute(n, v) { this._attrs.set(String(n), String(v)); }
  getAttribute(n) { return this._attrs.has(String(n)) ? this._attrs.get(String(n)) : null; }
  hasAttribute(n) { return this._attrs.has(String(n)); }
  removeAttribute(n) { this._attrs.delete(String(n)); }
  get dataset() {
    const attrs = this._attrs;
    return new Proxy({}, {
      get(_t, key) {
        return attrs.get('data-' + String(key).replace(/[A-Z]/g, (c) => '-' + c.toLowerCase()));
      },
      set(_t, key, value) {
        attrs.set('data-' + String(key).replace(/[A-Z]/g, (c) => '-' + c.toLowerCase()), String(value));
        return true;
      },
    });
  }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    const at = ref === null || ref === undefined ? -1 : this.childNodes.indexOf(ref);
    if (at < 0) this.childNodes.push(child);
    else this.childNodes.splice(at, 0, child);
    return child;
  }
  removeChild(child) {
    const at = this.childNodes.indexOf(child);
    if (at >= 0) this.childNodes.splice(at, 1);
    child.parentNode = null;
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(other) {
    let n = other;
    while (n) { if (n === this) return true; n = n.parentNode; }
    return false;
  }
  // Only the two selectors the plugin actually asks about: the skip attribute
  // and the svg ancestor check.
  closest(selector) {
    const text = String(selector);
    let node = this;
    while (node && node.tagName) {
      if (text === 'svg') {
        if (node.tagName === 'SVG') return node;
      } else if (text.startsWith('[') && text.endsWith(']')) {
        const attr = text.slice(1, -1);
        if (node._attrs.has(attr)) return node;
      }
      node = node.parentNode;
    }
    return null;
  }
  querySelector(selector) {
    const want = String(selector).replace(/^\./, '');
    const matches = (node) => node.tagName && node._classes.has(want);
    const walk = (node) => {
      // Text nodes in this fake DOM are bare { nodeType, nodeValue } objects, so
      // anything without children has to end the descent instead of throwing.
      if (!node.childNodes) return null;
      for (const child of node.childNodes) {
        if (matches(child)) return child;
        const found = walk(child);
        if (found) return found;
      }
      return null;
    };
    return walk(this);
  }
  querySelectorAll(selector) {
    const want = String(selector).replace(/^\./, '');
    const out = [];
    const walk = (node) => {
      if (!node.childNodes) return;
      for (const child of node.childNodes) {
        if (child.tagName && child._classes.has(want)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  getElementById(id) {
    let found = null;
    const walk = (node) => {
      if (!node.childNodes || found !== null) return;
      for (const child of node.childNodes) {
        if (child.tagName && child._attrs.id === id) {
          found = child;
          return;
        }
        walk(child);
      }
    };
    walk(this);
    return found;
  }
  getBoundingClientRect() {
    const r = this._rect;
    return {
      left: r.left,
      top: r.top,
      width: r.width,
      height: r.height,
      right: r.left + r.width,
      bottom: r.top + r.height,
      x: r.left,
      y: r.top,
    };
  }
  addEventListener(type, fn) {
    if (!this._listeners) this._listeners = new Map();
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    const list = this._listeners?.get(type) ?? [];
    const at = list.indexOf(fn);
    if (at >= 0) list.splice(at, 1);
  }
  attachShadow() {
    const root = new El('shadow-root');
    this._shadow = root;
    root.parentNode = this;
    return root;
  }
  // A closed root is invisible to page code, which is the whole point of using
  // one; the stub keeps the real handle on `_shadow` for assertions only.
  get shadowRoot() { return null; }
  getAnimations() { return this._animations.slice(); }
  animate(keyframes, options) {
    const anim = {
      id: '',
      keyframes,
      options,
      _cancelled: false,
      _settled: false,
      _endClock: clock + (options.delay ?? 0) + (options.duration ?? 0),
      finished: null,
      cancel() {
        if (this._cancelled) return;
        this._cancelled = true;
        const at = owner._animations.indexOf(this);
        if (at >= 0) owner._animations.splice(at, 1);
      },
    };
    const owner = this;
    anim.finished = new Promise((resolve, reject) => {
      anim._resolve = resolve;
      anim._reject = reject;
    });
    anim.finished.catch(() => {});
    this._animations.push(anim);
    allAnimations.push(anim);
    return anim;
  }
}

// SVG nodes must not be mistaken for animatable HTML: the plugin skips them by
// prototype, so the stub has to give them their own class.
class SvgEl extends El {}

const doc = new El('html');
doc.documentElement = doc;
doc.body = new El('body');
doc.appendChild(doc.body);
doc.createElement = (tag) => new El(tag);
const body = doc.body;
const head = new El('head');
doc.appendChild(head);

let clock = 0;
let allAnimations = [];
let rafQueue = [];
let observers = [];
let rafId = 0;
let timers = [];
const listeners = new Map();

const COMPUTED = {
  display: 'block',
  visibility: 'visible',
  overflowX: 'visible',
  overflowY: 'visible',
  transform: 'none',
  filter: 'none',
  perspective: 'none',
  contain: 'none',
  backgroundImage: 'none',
  lineHeight: '20px',
  fontSize: '14px',
};

const storage = new Map();

function installGlobals() {
  globalThis.Node = { ELEMENT_NODE: 1, TEXT_NODE: 3, DOCUMENT_NODE: 9 };
  globalThis.Element = El;
  globalThis.HTMLElement = El;
  globalThis.SVGElement = SvgEl;
  globalThis.document = doc;
  globalThis.window = {
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener: (type, fn) => {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener: (type, fn) => {
      const list = listeners.get(type) ?? [];
      const at = list.indexOf(fn);
      if (at >= 0) list.splice(at, 1);
    },
    getComputedStyle: (el) => ({ ...COMPUTED, ...(el._computed ?? {}) }),
    matchMedia: () => ({ matches: false }),
    setInterval: (fn, ms) => {
      timers.push({ fn, ms, repeat: true });
      return timers.length;
    },
    clearInterval: () => {},
    setTimeout: (fn, ms) => {
      timers.push({ fn, ms, repeat: false });
      return timers.length;
    },
    clearTimeout: () => {},
    requestAnimationFrame: (fn) => {
      rafId += 1;
      rafQueue.push(fn);
      return rafId;
    },
    cancelAnimationFrame: () => {},
  };
  globalThis.getComputedStyle = globalThis.window.getComputedStyle;
  globalThis.matchMedia = globalThis.window.matchMedia;
  globalThis.requestAnimationFrame = globalThis.window.requestAnimationFrame;
  globalThis.cancelAnimationFrame = globalThis.window.cancelAnimationFrame;
  globalThis.performance = { now: () => clock };
  globalThis.localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  };
  globalThis.MutationObserver = class {
    constructor(callback) { this.callback = callback; this._targets = []; }
    observe(target) { this._targets.push(target); observers.push(this); }
    disconnect() {
      const at = observers.indexOf(this);
      if (at >= 0) observers.splice(at, 1);
    }
    takeRecords() { return []; }
  };
}

installGlobals();

// ------------------------------------------------------------- React stub

const reactStub = {
  createElement: (type, props, ...children) => ({
    type,
    props: props ?? {},
    children: children.flat(Infinity),
  }),
  useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
  useRef: (initial) => ({ current: initial }),
  useCallback: (fn) => fn,
  useEffect: () => {},
  useMemo: (fn) => fn(),
  Fragment: 'fragment',
};

// ------------------------------------------------------------------- boot

let module = null;

function loadModule() {
  if (module !== null) return module;
  // The plugin registers itself on `window`, and `window` here is a plain object
  // rather than globalThis, so the loader hook has to exist on both.
  const loader = {
    load: ({ factory }) => {
      module = factory(() => reactStub);
    },
  };
  globalThis.__ModuleLoader__ = loader;
  globalThis.window.__ModuleLoader__ = loader;
  // The plugin swallows every failure it can (a broken animation must never
  // break the page), which hides real defects from this harness. When DSS_DEBUG
  // is set, rewrite each bare catch so the thrown value is printed before it is
  // swallowed. Only the test build of the source is rewritten; the shipped file
  // is never touched.
  const source =
    process.env.DSS_DEBUG === '1'
      ? SOURCE.replace(
          /\} catch \{/g,
          '} catch (__dssError) { if (globalThis.__dssDebug) console.log("DSS_CATCH " + (__dssError && __dssError.stack ? __dssError.stack : __dssError));',
        )
      : SOURCE;
  globalThis.__dssDebug = process.env.DSS_DEBUG === '1';
  // eslint-disable-next-line no-new-func
  new Function(source)();
  return module;
}

function boot(settings) {
  storage.clear();
  if (settings !== null && settings !== undefined) {
    storage.set('dsh-step-slide-in:curve', JSON.stringify(settings));
  }
  allAnimations = [];
  rafQueue = [];
  observers = [];
  timers = [];
  injected.length = 0;
  registered.length = 0;
  clock = 0;
  listeners.clear();
  body.childNodes = [];
  const mod = loadModule();
  const ctx = {
    effect: (fn) => { const cleanup = fn(); return cleanup; },
    locale: {
      register: () => {},
      bind: () => (key) => key,
    },
    slots: {
      // The plugin defers its registration through inject(), so the stub has to
      // run the factory the way the real slot host would.
      inject: (_name, fn) => { injected.push(fn); fn(); },
      register: (meta, component) => { registered.push({ meta, component }); },
    },
  };
  mod.apply(ctx);
  return mod;
}

const injected = [];
const registered = [];

function flush(records) {
  for (const observer of observers.slice()) {
    try {
      observer.callback(records, observer);
    } catch (error) {
      console.log('THROWN ' + (error && error.stack ? error.stack : error));
    }
  }
}

async function pump(frames = 1, step = 16) {
  for (let i = 0; i < frames; i += 1) {
    clock += step;
    for (const anim of allAnimations.slice()) {
      if (!anim._cancelled && !anim._settled && clock >= anim._endClock) {
        anim._settled = true;
        anim._resolve?.();
      }
    }
    const batch = rafQueue.splice(0, rafQueue.length);
    for (const fn of batch) fn(clock);
    await Promise.resolve();
  }
  await Promise.resolve();
}

// ---------------------------------------------------------------- scenario

function buildScene(count) {
  const container = new El('div');
  container._rect = { left: 0, top: 0, width: 600, height: 400 };
  body.appendChild(container);
  const kids = [];
  for (let i = 0; i < count; i += 1) {
    const kid = new El('div');
    kid._rect = { left: 20, top: 30 + i * 60, width: 300, height: 40 };
    container.appendChild(kid);
    kids.push(kid);
  }
  return { container, kids };
}

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  [' + detail + ']' : ''));
}

function idsOf(el) { return el.getAnimations().map((a) => a.id).filter(Boolean); }
function animById(el, id) { return el.getAnimations().find((a) => a.id === id) ?? null; }
function swarmHost() {
  return body.childNodes.find((n) => n.getAttribute('data-dss-skip') === '1');
}
function shapeOf(node) {
  // The cursor div holds a single svg child, and that child is what the plugin
  // rewrites when the shape changes, so the markup lives one level down.
  const own = node.innerHTML || '';
  const child = node.firstChild;
  const html = own + (child && child.innerHTML ? child.innerHTML : '');
  if (html.includes('M8.5 4.5')) return 'ibeam';
  if (html.includes('M17.4 10.6')) return 'pen';
  if (html.includes('M12 3.5 V20.5')) return 'cross';
  if (html.includes('M4.6 3.9')) return 'arrow';
  return '';
}

// ---------------------------------------------------------- 1. swarm off
{
  boot(null);
  const { container, kids } = buildScene(3);
  flush([{ type: 'childList', target: container, addedNodes: kids, removedNodes: [] }]);

  const ids = kids.map(idsOf);
  check('off: every child gets the original slide', ids.every((l) => l.includes('dss-enter-slide')));
  check('off: no swarm reveal anywhere', kids.every((k) => !idsOf(k).includes('dss-swarm-reveal')));

  const slide = animById(kids[0], 'dss-enter-slide');
  check('off: slide duration is 520', slide.options.duration === 520, String(slide.options.duration));
  check('off: slide fills backwards', slide.options.fill === 'backwards');
  check(
    'off: slide uses the chosen curve',
    slide.options.easing === 'cubic-bezier(0.22, 1, 0.36, 1)',
    slide.options.easing,
  );
  check('off: spread delay i=0', animById(kids[0], 'dss-enter-slide').options.delay === 0);
  check('off: spread delay i=1', animById(kids[1], 'dss-enter-slide').options.delay === 167);
  check('off: spread delay i=2', animById(kids[2], 'dss-enter-slide').options.delay === 333);
  check('off: root children also land', kids.every((k) => idsOf(k).includes('dss-enter-land')));
  check('off: landing duration is 980', animById(kids[0], 'dss-enter-land').options.duration === 980);
  check('off: no swarm layer created', swarmHost() === undefined);
  check('off: no inline style written', kids.every((k) => Object.keys(k.style).length === 0));

  const diag = globalThis.__dshStepSlideInDiag;
  check('off: diag reports version 1.9.0', diag.version === '1.9.0', diag.version);
  check('off: diag.swarm false', diag.swarm === false);
}

// ----------------------------------------------------------- 2. swarm on
{
  boot({ preset: 'smooth', custom: null, density: 'element', swarm: true });
  const { container, kids } = buildScene(3);
  flush([{ type: 'childList', target: container, addedNodes: kids, removedNodes: [] }]);

  check('on: no original slide created', kids.every((k) => !idsOf(k).includes('dss-enter-slide')));
  check('on: no original landing created', kids.every((k) => !idsOf(k).includes('dss-enter-land')));
  check('on: every child gets a swarm reveal', kids.every((k) => idsOf(k).includes('dss-swarm-reveal')));
  const reveal = animById(kids[0], 'dss-swarm-reveal');
  check('on: reveal duration is 280', reveal.options.duration === 280, String(reveal.options.duration));
  check('on: reveal fills backwards', reveal.options.fill === 'backwards');
  check('on: reveal is delayed past the drawing lead', reveal.options.delay === 380, String(reveal.options.delay));
  check('on: reveal starts hidden', reveal.keyframes[0].opacity === '0');

  const host = swarmHost();
  check('on: layer host exists on body', Boolean(host));
  check('on: layer host is fixed', host.style.cssText.includes('position:fixed'));
  check('on: layer host has a closed shadow root', host._shadow !== null && host.shadowRoot === null);
  const cursorLayer = host._shadow.querySelector('.cursors');
  check('on: cursor layer present in shadow', Boolean(cursorLayer));

  const diag = globalThis.__dshStepSlideInDiag;
  check('on: diag.swarm true', diag.swarm === true);
  check('on: diag.swarmMode running', diag.swarmMode === 'running', diag.swarmMode);
  check('on: diag.swarmUnits is 3', diag.swarmUnits === 3, String(diag.swarmUnits));

  await pump(4);
  check('on: cursors spawned', cursorLayer.childNodes.length > 0, String(cursorLayer.childNodes.length));
  const cursorCount = cursorLayer.childNodes.length;
  check('on: one crew plus escorts per unit', cursorCount === 3 * 3, String(cursorCount));
  check('on: diag.swarmCursors tracks them', diag.swarmCursors === cursorCount, String(diag.swarmCursors));

  const movedFrom = cursorLayer.childNodes.map((n) => n.style.transform);
  await pump(12);
  const movedTo = cursorLayer.childNodes.map((n) => n.style.transform);
  check('on: cursors actually move', movedFrom.some((v, i) => v !== movedTo[i]));

  const overlay = host._shadow.querySelector('.overlay');
  const badges = host._shadow.querySelector('.badges');
  let sawBox = false;
  let sawBadge = false;
  for (let i = 0; i < 200; i += 1) {
    await pump(1);
    if (overlay.innerHTML.includes('<rect')) sawBox = true;
    if (badges.innerHTML.includes('class="badge"')) sawBadge = true;
  }
  check('on: a selection box was drawn', sawBox);
  check('on: a size badge was drawn', sawBadge);
  check('on: every unit was drawn', diag.swarmRevealed === 3, String(diag.swarmRevealed));

  for (let i = 0; i < 120; i += 1) await pump(1);
  check('on: the layer is torn down when idle', swarmHost() === undefined);
  check('on: diag.swarmMode goes idle', diag.swarmMode === 'idle', diag.swarmMode);
  check('on: no inline style left on the page', kids.every((k) => Object.keys(k.style).length === 0));
}

// ------------------------------------- 3. exits still use the original ghost
for (const swarmOn of [false, true]) {
  const label = swarmOn ? 'on' : 'off';
  boot({ preset: 'smooth', custom: null, density: 'element', swarm: swarmOn });
  const { container, kids } = buildScene(1);
  flush([{ type: 'childList', target: container, addedNodes: kids, removedNodes: [] }]);
  // Let the entrance finish: the original never flies a node that is still
  // arriving, which is correct behaviour and not what this case tests.
  await pump(160);

  container.removeChild(kids[0]);
  // `nextSibling` has to be present and null: the plugin re-inserts the node at
  // its old position to measure it, and an absent field would throw there.
  flush([
    { type: 'childList', target: container, addedNodes: [], removedNodes: [kids[0]], nextSibling: null },
  ]);

  const ghost = animById(kids[0], 'dss-exit-ghost');
  check(label + ': exit still ghosts out', ghost !== null);
  check(label + ': ghost duration is 520', ghost.options.duration === 520, String(ghost.options.duration));
  check(label + ': ghost starts at rest', ghost.keyframes[0].translate === '0px 0px');
  check(label + ': ghost fills both', ghost.options.fill === 'both');
  check(
    label + ': ghost moves off-screen',
    ghost.keyframes[1].translate !== '0px 0px',
    ghost.keyframes[1].translate,
  );
}

// ------------------------------------------------------ 4. skip and reduced
{
  boot({ preset: 'smooth', custom: null, density: 'element', swarm: true });
  const host = new El('div');
  host.setAttribute('data-dss-skip', '1');
  host._rect = { left: 0, top: 0, width: 300, height: 40 };
  body.appendChild(host);
  const container = new El('div');
  container._rect = { left: 0, top: 0, width: 600, height: 400 };
  body.appendChild(container);
  container.appendChild(host);
  flush([{ type: 'childList', target: container, addedNodes: [host], removedNodes: [] }]);
  check('skip: host is not animated', host.getAnimations().length === 0, String(host.getAnimations().length));
  const diag = globalThis.__dshStepSlideInDiag;
  check('skip: host is not queued', diag.swarmUnits === 0, String(diag.swarmUnits));
}

{
  const savedMatchMedia = globalThis.matchMedia;
  globalThis.matchMedia = () => ({ matches: true });
  boot({ preset: 'smooth', custom: null, density: 'element', swarm: true });
  const { container, kids } = buildScene(2);
  flush([{ type: 'childList', target: container, addedNodes: kids, removedNodes: [] }]);
  check('reduced: nothing animates', kids.every((k) => k.getAnimations().length === 0));
  check('reduced: no layer built', swarmHost() === undefined);
  globalThis.matchMedia = savedMatchMedia;
}

// ------------------------------------------------------------ 5. settings UI
function walk(node, visit) {
  if (node === null || node === undefined || typeof node !== 'object') return;
  if (Array.isArray(node)) {
    for (const item of node) walk(item, visit);
    return;
  }
  visit(node);
  for (const child of node.children ?? []) walk(child, visit);
}

function findChips(node) {
  const out = [];
  walk(node, (n) => {
    if (n.props && n.props.role === 'radio' && n.props.className === 'dss-curve') out.push(n);
  });
  return out;
}

{
  boot({ preset: 'smooth', custom: null, density: 'element', swarm: false });
  check('ui: settings row registered', registered.length === 1, String(registered.length));
  const component = registered[0].component;
  const tree = component({});
  let swarmRow = null;
  walk(tree, (node) => {
    if (node.props && node.props.className === 'dss-swarm') swarmRow = node;
  });
  check('ui: swarm row is rendered', swarmRow !== null);

  const swarmChips = swarmRow === null ? [] : findChips(swarmRow);
  check('ui: swarm row has exactly two chips', swarmChips.length === 2, String(swarmChips.length));
  check('ui: ten radio chips in total', findChips(tree).length === 10, String(findChips(tree).length));

  const offChip = swarmChips[0];
  const onChip = swarmChips[1];
  check(
    'ui: swarm off is checked by default',
    offChip.props['aria-checked'] === true && onChip.props['aria-checked'] === false,
  );

  onChip.props.onClick();
  const saved = JSON.parse(storage.get('dsh-step-slide-in:curve'));
  check('ui: clicking on persists swarm true', saved.swarm === true);
  check('ui: clicking on keeps the other settings', saved.preset === 'smooth' && saved.density === 'element');

  const { container, kids } = buildScene(2);
  flush([{ type: 'childList', target: container, addedNodes: kids, removedNodes: [] }]);
  check('ui: next batch uses swarm without a reload', kids.every((k) => idsOf(k).includes('dss-swarm-reveal')));
}

// ------------------------------------------- 6. four beats, dispatched by kind
{
  boot({ preset: 'smooth', custom: null, density: 'element', swarm: true });

  const container = new El('div');
  container._rect = { left: 0, top: 0, width: 900, height: 700 };
  body.appendChild(container);

  // One of each kind, so the dispatch has to run four different beats at once.
  const picture = new El('img');
  picture._rect = { left: 40, top: 40, width: 200, height: 150 };
  const paragraph = new El('p');
  paragraph._rect = { left: 40, top: 220, width: 320, height: 60 };
  paragraph.childNodes.push({ nodeType: 3, nodeValue: 'Hello there, this is a line of text.' });
  const rule = new El('hr');
  rule._rect = { left: 40, top: 320, width: 400, height: 2 };
  const box = new El('div');
  box._rect = { left: 40, top: 380, width: 240, height: 120 };
  const kids = [picture, paragraph, rule, box];
  for (const kid of kids) container.appendChild(kid);

  flush([{ type: 'childList', target: container, addedNodes: kids, removedNodes: [] }]);

  const host = swarmHost();
  check('modes: layer built', Boolean(host));
  const cursorLayer = host._shadow.querySelector('.cursors');
  const overlay = host._shadow.querySelector('.overlay');

  const seen = new Set();
  let boxAtPicture = false;
  let boxAtParagraph = false;
  for (let i = 0; i < 400; i += 1) {
    await pump(1);
    for (const node of cursorLayer.childNodes) {
      const shape = shapeOf(node);
      if (shape !== '') seen.add(shape);
    }
    const first = /<rect x="([-\d.]+)" y="([-\d.]+)" width="([-\d.]+)"/.exec(overlay.innerHTML);
    if (first !== null) {
      const x = Number(first[1]);
      const y = Number(first[2]);
      if (Math.abs(x - 40) < 3 && Math.abs(y - 40) < 3) boxAtPicture = true;
      if (Math.abs(x - 40) < 3 && Math.abs(y - 220) < 3) boxAtParagraph = true;
    }
  }

  check('modes: crosshair used', seen.has('cross'), [...seen].join(','));
  check('modes: caret used for text', seen.has('ibeam'), [...seen].join(','));
  check('modes: pen used for the rule', seen.has('pen'), [...seen].join(','));
  check('modes: arrow used', seen.has('arrow'), [...seen].join(','));
  check('modes: picture got a dragged selection', boxAtPicture);
  check('modes: text got a selection over its first line', boxAtParagraph);

  const diag = globalThis.__dshStepSlideInDiag;
  check('modes: every unit was drawn', diag.swarmRevealed === 4, String(diag.swarmRevealed));
  check('modes: nothing degraded', diag.swarmDegraded === 0, String(diag.swarmDegraded));
  check('modes: no inline style on the page elements', kids.every((k) => Object.keys(k.style).length === 0));
}

// ------------------------------------- 7. cursor and outline follow a moving box
{
  boot({ preset: 'smooth', custom: null, density: 'element', swarm: true });

  const container = new El('div');
  container._rect = { left: 0, top: 0, width: 900, height: 700 };
  body.appendChild(container);
  const moving = new El('div');
  moving._rect = { left: 100, top: 100, width: 300, height: 120 };
  container.appendChild(moving);

  flush([{ type: 'childList', target: container, addedNodes: [moving], removedNodes: [] }]);

  const host = swarmHost();
  const overlay = host._shadow.querySelector('.overlay');

  let drew = false;
  for (let i = 0; i < 200 && !drew; i += 1) {
    await pump(1);
    drew = overlay.innerHTML.includes('<rect');
  }
  check('follow: a selection was drawn first', drew);

  const before = overlay.innerHTML;
  moving._rect = { left: 420, top: 300, width: 300, height: 120 };
  await pump(3);
  const after = overlay.innerHTML;

  check('follow: the outline moved with the element', before !== after);
  const moved = /<rect x="([-\d.]+)" y="([-\d.]+)"/.exec(after);
  check(
    'follow: the outline sits at the new corner',
    moved !== null && Math.abs(Number(moved[1]) - 420) < 3 && Math.abs(Number(moved[2]) - 300) < 3,
    moved === null ? 'no rect' : moved[1] + ',' + moved[2],
  );
}

// ------------------------------------------------------------------ report
const failed = results.filter((r) => !r.ok);
console.log('\n' + (results.length - failed.length) + '/' + results.length + ' checks passed');
if (failed.length > 0) {
  console.log('FAILURES:');
  for (const f of failed) console.log('  - ' + f.name + (f.detail ? ' [' + f.detail + ']' : ''));
  process.exitCode = 1;
}
