window.__ModuleLoader__.load({
  id: 'dsh-step-slide-in',
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });

    const React = require('react');
    const h = React.createElement;

    const NS = 'step-slide-in';
    const STORAGE_KEY = 'dsh-step-slide-in:curve';
    // Flight time of the approach. The slight landing over-scale is reached
    // exactly when the box arrives, so the settle spring continues from it.
    const DURATION_MS = 520;
    // How much bigger the box is just before it lands: small on purpose, it
    // reads as "a little closer to the eye", not as a zoom.
    const LAND_SCALE = 1.05;
    // The settle spring that plays after landing.
    const SETTLE_MS = 460;
    const SETTLE_STEPS = 18;
    const SETTLE_DECAY = 5;
    const SETTLE_CYCLES = 1.25;
    // The whole wave of queued starts finishes within this window: the gap
    // between two starts is 0.5s / (animations waiting in the queue), in seconds.
    const START_SPREAD_MS = 500;
    // The entrance starts just outside the viewport; this margin keeps the whole
    // box off-screen even when the measured position was stale.
    const OFFSCREEN_MARGIN = 32;
    // Safety valve for pathological batches; every element still animates below it.
    const MAX_QUEUE = 4000;
    // Bulk teardown (view replacement, conversation switch) is not an exit.
    const EXIT_MAX_ROOTS = 24;
    const EXIT_MAX_DESCENDANTS = 400;
    // A removed box taller than this is a whole view, not a collapsing section.
    const EXIT_MAX_HEIGHT_RATIO = 1.5;
    const PRESETS = [
      { id: 'smooth', points: [0.22, 1, 0.36, 1] },
      { id: 'standard', points: [0.4, 0, 0.2, 1] },
      { id: 'spring', points: [0.34, 1.56, 0.64, 1] },
      { id: 'linear', points: [0, 0, 1, 1] },
    ];
    const FALLBACK_POINTS = PRESETS[0].points;
    // Which DOM level carries the entrance animation (exits always use the removed box).
    const DENSITIES = ['element', 'parent', 'grandparent'];

    // Damped spring sampled into keyframes, so a settle allocates nothing per
    // element and always starts exactly at the landing over-scale.
    const SETTLE_FRAMES = (() => {
      const frames = [];
      for (let index = 0; index <= SETTLE_STEPS; index += 1) {
        const t = index / SETTLE_STEPS;
        const swing =
          index === SETTLE_STEPS
            ? 0
            : Math.exp(-SETTLE_DECAY * t) * Math.cos(2 * Math.PI * SETTLE_CYCLES * t);
        const scale = 1 + (LAND_SCALE - 1) * swing;
        frames.push({ scale: String(Math.round(scale * 1e4) / 1e4), offset: t });
      }
      return frames;
    })();

    // Structural chrome never carries a visible box worth animating.
    const SKIP_TAGS = new Set([
      'HEAD',
      'SCRIPT',
      'STYLE',
      'LINK',
      'META',
      'TEMPLATE',
      'NOSCRIPT',
      'TITLE',
      'BASE',
      'HTML',
      'BODY',
    ]);
    // Only these display values produce a transformable box that also has a real
    // height. Plain inline text boxes cannot be translated or height-animated.
    const ANIMATABLE_DISPLAYS = new Set([
      'block',
      'flex',
      'grid',
      'inline-block',
      'inline-flex',
      'inline-grid',
      'inline-table',
      'list-item',
      'flow-root',
      'table',
    ]);
    // Text-level removals are the most frequent ones and never worth a re-insert.
    // Dropping them by tag before touching the DOM is also cheap insurance.
    const INLINE_EXIT_TAGS = new Set([
      'SPAN',
      'A',
      'B',
      'STRONG',
      'EM',
      'I',
      'SMALL',
      'CODE',
      'SAMP',
      'KBD',
      'MARK',
      'ABBR',
      'CITE',
      'Q',
      'SUB',
      'SUP',
      'U',
      'S',
      'DEL',
      'INS',
      'TIME',
      'LABEL',
      'BR',
      'WBR',
      'BDI',
      'BDO',
      'DATA',
      'DFN',
      'VAR',
      'RUBY',
      'RT',
      'RP',
    ]);
    // A single mutation batch this large is a whole render (app boot, view swap),
    // not a handful of new rows; animating it would thrash layout.
    const MAX_ENTRANCE_BATCH = 1200;
    // A container with more children than this is sampled, not measured node by
    // node, when its visual centre is computed.
    const GROUP_SCAN_MAX = 160;

    const VIEW = 148;
    const PAD = 14;
    const PLOT = VIEW - PAD * 2;
    const Y_MIN = -0.6;
    const Y_MAX = 1.6;

    const DICTS = {
      zh: {
        title: '元素入场动画',
        description:
          '新元素沿「上级容器元素群视觉中心 → 自身视觉中心」的连线飞入原位，落定前微微放大、再弹性回弹落定；动画只走合成层，不改变布局。',
        curve: '缓动曲线',
        preset: '预设',
        custom: '自定义',
        hint: '拖动两个控制点调整曲线，方向键可微调。',
        handle1: '控制点 1',
        handle2: '控制点 2',
        smooth: '平滑',
        standard: '标准',
        spring: '回弹',
        linear: '匀速',
        density: '动画密度',
        densityElement: '元素',
        densityParent: '父容器',
        densityGrandparent: '父级父容器',
        densityHint:
          '「元素」每个新元素各自入场；「父容器」改为让它的父容器入场；「父级父容器」再上一层。出场始终由被删元素自己飞出。',
        swarm: 'Swarm Cursors 模式',
        swarmOff: '关闭',
        swarmOn: '开启',
        swarmHint:
          '开启后，新元素改由一群光标按下、拖出选框后显形；出场仍沿用原版飞出。关闭时入场动画与原来完全一致。',
      },
      en: {
        title: 'Element entrance',
        description:
          'New elements fly in along the line from the visual centre of their parent container through their own, grow slightly just before landing, then settle with a spring. Compositor properties only: the layout never moves.',
        curve: 'Easing curve',
        preset: 'Presets',
        custom: 'Custom',
        hint: 'Drag the two control points; arrow keys nudge them.',
        handle1: 'Control point 1',
        handle2: 'Control point 2',
        smooth: 'Smooth',
        standard: 'Standard',
        spring: 'Spring',
        linear: 'Linear',
        density: 'Animation density',
        densityElement: 'Element',
        densityParent: 'Parent',
        densityGrandparent: 'Grandparent',
        densityHint:
          'Element animates every new box; Parent animates its container instead; Grandparent goes one level higher. Exits always fly out the removed box itself.',
        swarm: 'Swarm Cursors',
        swarmOff: 'Off',
        swarmOn: 'On',
        swarmHint:
          'When on, a swarm of cursors clicks and drags out each new box before it appears. Exits still fly out as before; off keeps the entrance exactly as it was.',
      },
    };

    const ROW_CSS =
      '.dss-row{display:flex;flex-direction:column;padding:16px 0;' +
      'border-bottom:.5px solid var(--dsw-alias-border-l2)}' +
      '.dss-head{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap}' +
      '.dss-text{display:flex;flex-direction:column;gap:4px;flex:1 1 240px;' +
      'min-width:0;padding-right:48px}' +
      '.dss-title{color:var(--dsw-alias-label-primary);font-size:14px;' +
      'font-weight:400;line-height:22px}' +
      '.dss-desc{color:var(--dsw-alias-label-secondary);font-size:12px;' +
      'font-weight:400;line-height:18px}' +
      '.dss-presets{display:flex;align-items:center;gap:8px;flex:none;margin-left:auto}' +
      '.dss-preset-label{color:var(--dsw-alias-label-secondary);font-size:12px;line-height:28px}' +
      '.dss-curves{display:flex;align-items:center;gap:6px;flex-wrap:wrap}' +
      '.dss-curve{height:28px;padding:0 12px;border-radius:999px;' +
      'border:.5px solid var(--dsw-alias-border-l2);background:transparent;' +
      'color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;' +
      'line-height:26px;cursor:pointer;white-space:nowrap;' +
      'transition:color .1s,background-color .1s,border-color .1s}' +
      '.dss-curve:hover{color:var(--dsw-alias-label-primary)}' +
      '.dss-curve[aria-checked="true"]{border-color:var(--dsw-alias-brand-primary);' +
      'background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}' +
      '.dss-curve:focus-visible{outline:2px solid var(--dsw-alias-brand-primary);' +
      'outline-offset:2px}' +
      '.dss-custom{display:flex;align-items:center;gap:16px;padding-top:14px}' +
      '.dss-plot{width:148px;height:148px;flex:none;border-radius:12px;' +
      'background:var(--dsw-alias-bg-layer-2);' +
      'border:.5px solid var(--dsw-alias-border-l1);touch-action:none;' +
      'cursor:crosshair;box-sizing:border-box}' +
      '.dss-plot-line{stroke:var(--dsw-alias-border-l2);stroke-width:1;' +
      'stroke-dasharray:3 4}' +
      '.dss-plot-guide{stroke:var(--dsw-alias-border-l2);stroke-width:1;' +
      'stroke-dasharray:2 3}' +
      '.dss-plot-curve{fill:none;stroke:var(--dsw-alias-brand-primary);' +
      'stroke-width:2;stroke-linecap:round}' +
      '.dss-plot-anchor{fill:var(--dsw-alias-label-secondary)}' +
      '.dss-handle{fill:var(--dsw-alias-bg-base);' +
      'stroke:var(--dsw-alias-brand-primary);stroke-width:2;cursor:grab;outline:none}' +
      '.dss-handle:active{cursor:grabbing}' +
      '.dss-handle:focus-visible{stroke-width:3}' +
      '.dss-custom-info{display:flex;flex-direction:column;gap:6px;min-width:0}' +
      '.dss-custom-value{font-family:"Cascadia Mono",Consolas,monospace;' +
      'font-size:12px;line-height:18px;color:var(--dsw-alias-label-primary);' +
      'overflow-wrap:anywhere}' +
      '.dss-custom-hint{font-size:12px;line-height:18px;' +
      'color:var(--dsw-alias-label-secondary)}' +
      '.dss-density{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-top:16px}' +
      '.dss-density-hint{flex:1 1 100%;font-size:12px;line-height:18px;' +
      'color:var(--dsw-alias-label-secondary)}' +
      '.dss-swarm{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-top:16px}' +
      '.dss-swarm-hint{flex:1 1 100%;font-size:12px;line-height:18px;' +
      'color:var(--dsw-alias-label-secondary)}';

    function clamp(value, min, max) {
      return Math.min(Math.max(value, min), max);
    }

    function normalizePoints(value) {
      if (!Array.isArray(value) || value.length !== 4) return null;
      const points = value.map(Number);
      if (points.some((number) => !Number.isFinite(number))) return null;
      return [
        clamp(points[0], 0, 1),
        clamp(points[1], Y_MIN, Y_MAX),
        clamp(points[2], 0, 1),
        clamp(points[3], Y_MIN, Y_MAX),
      ];
    }

    function readSetting() {
      try {
        const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
        if (typeof raw === 'string' && raw !== '') {
          if (raw.charAt(0) === '{') {
            const parsed = JSON.parse(raw);
            const custom = normalizePoints(parsed.custom);
            const preset = PRESETS.some((item) => item.id === parsed.preset)
              ? parsed.preset
              : 'custom';
            const density = DENSITIES.includes(parsed.density) ? parsed.density : DENSITIES[0];
            // Strictly true: a stored string or number is a corrupted value, and
            // an old payload without the key must keep the original entrance.
            const swarm = parsed.swarm === true;
            if (preset === 'custom' && custom === null) {
              return { preset: PRESETS[0].id, custom: null, density, swarm };
            }
            return { preset, custom, density, swarm };
          }
          if (PRESETS.some((item) => item.id === raw)) {
            return { preset: raw, custom: null, density: DENSITIES[0], swarm: false };
          }
        }
      } catch {}
      return { preset: PRESETS[0].id, custom: null, density: DENSITIES[0], swarm: false };
    }

    function storeSetting(setting) {
      try {
        globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(setting));
      } catch {}
    }

    function pointsOf(setting) {
      if (setting.preset === 'custom') return setting.custom ?? FALLBACK_POINTS;
      const preset = PRESETS.find((item) => item.id === setting.preset) ?? PRESETS[0];
      return preset.points;
    }

    function easeOf(setting) {
      const points = pointsOf(setting).map((number) => Math.round(number * 1000) / 1000);
      return 'cubic-bezier(' + points.join(', ') + ')';
    }

    function prefersReduced() {
      try {
        return (
          typeof matchMedia === 'function' &&
          matchMedia('(prefers-reduced-motion: reduce)').matches
        );
      } catch {
        return false;
      }
    }

    function now() {
      return typeof performance === 'object' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now();
    }

    /**
     * Unit vector of the flight line: from the group's visual centre through the
     * box's own visual centre. Without a group (a lone child, a degenerate
     * centre) the viewport centre is used, which keeps the spread.
     */
    function flightRay(left, top, width, height, centerX, centerY) {
      const viewWidth = Math.max(1, globalThis.innerWidth || 1);
      const viewHeight = Math.max(1, globalThis.innerHeight || 1);
      const originX = centerX === null || centerX === undefined ? viewWidth / 2 : centerX;
      const originY = centerY === null || centerY === undefined ? viewHeight / 2 : centerY;
      let dirX = left + width / 2 - originX;
      let dirY = top + height / 2 - originY;
      const length = Math.hypot(dirX, dirY);
      if (!(length > 8)) {
        // The box sits on its group's centre: no line to speak of; come from below.
        dirX = 0;
        dirY = 1;
      } else {
        dirX /= length;
        dirY /= length;
      }
      return { x: dirX, y: dirY };
    }

    /**
     * Translation that moves a box fully off the viewport along a unit vector
     * produced by flightRay.
     */
    function offscreenDelta(dirX, dirY, left, top, width, height) {
      const viewWidth = Math.max(1, globalThis.innerWidth || 1);
      const viewHeight = Math.max(1, globalThis.innerHeight || 1);
      let distance = Infinity;
      if (dirX > 0) distance = Math.min(distance, (viewWidth + OFFSCREEN_MARGIN - left) / dirX);
      else if (dirX < 0) distance = Math.min(distance, (left + width + OFFSCREEN_MARGIN) / -dirX);
      if (dirY > 0) distance = Math.min(distance, (viewHeight + OFFSCREEN_MARGIN - top) / dirY);
      else if (dirY < 0) distance = Math.min(distance, (top + height + OFFSCREEN_MARGIN) / -dirY);
      if (!Number.isFinite(distance) || distance < 0) distance = Math.max(viewWidth, viewHeight);
      return { x: Math.round(dirX * distance), y: Math.round(dirY * distance) };
    }

    // ---------------------------------------------------------------- swarm
    //
    // The optional entrance choreography: instead of the box flying in, a crew of
    // cursors draws it. The visuals are ported from the Swarm rebuild
    // (swarm-rebuild/swarm.js): the same arrow silhouette, the same white halo,
    // the same "cross cursor presses, drags a selection out, box becomes real"
    // beat. Only the drawing half is ported; the snapshot, blueprint, audio, HUD
    // and task scheduler of the original stay out.

    const SWARM_NAMES = ['Ada', 'Linus', 'Grace', 'Alan', 'Radia', 'Ken', 'Barbara', 'Edsger'];
    const SWARM_COLORS = [
      '#F24E1E',
      '#A259FF',
      '#1ABCFE',
      '#0ACF83',
      '#FF7262',
      '#FFC700',
      '#9747FF',
      '#0D99FF',
    ];
    // Silhouette of the arrow pointer, in a 28x28 box.
    const SWARM_ARROW_D =
      'M4.6 3.9 C4.2 3.1 4.9 2.4 5.7 2.8 L20.6 9.4 C21.5 9.8 21.4 11.1 20.4 11.3 ' +
      'L13.9 12.9 C13.4 13 13 13.4 12.9 13.9 L11.3 20.4 C11.1 21.4 9.8 21.5 9.4 20.6 Z';
    // The text caret, drawn as two serifs and a stem.
    const SWARM_IBEAM_D =
      'M8.5 4.5 H10.5 Q12 4.5 12 6 Q12 4.5 13.5 4.5 H15.5 M12 6 V18 ' +
      'M8.5 19.5 H10.5 Q12 19.5 12 18 Q12 19.5 13.5 19.5 H15.5';
    // The pen nib: body, tip and the little ink dot.
    const SWARM_PEN_BODY_D = 'M3 3 L13.1 6.3 L17.4 10.6 L10.6 17.4 L6.3 13.1 Z';
    const SWARM_PEN_TIP_D = 'M17.4 10.6 L20.6 13.8 L13.8 20.6 L10.6 17.4 Z';
    // Hotspot of each shape: the point that actually points at the target.
    const SWARM_HOT = {
      arrow: [5, 3.5],
      cross: [12, 12],
      ibeam: [12, 12],
      pen: [3, 3],
      'pen-add': [3, 3],
    };
    // A crew is the cursor that draws plus its escorts; a handful of crews read as
    // a swarm without turning a long batch into confetti.
    const SWARM_MAX_CREWS = 6;
    const SWARM_ESCORTS = 2;
    const SWARM_MAX_CURSORS = 24;
    // The box fades in on its own animation clock, so a throttled tab can never
    // leave content invisible; these only shape the reveal.
    const SWARM_REVEAL_MS = 280;
    const SWARM_REVEAL_LEAD_MS = 380;
    // The drawing must be finished this long before the box appears.
    const SWARM_CUSHION_MS = 140;
    // Below this much time a full draw cannot land, so the crew clicks instead.
    const SWARM_MIN_BUDGET_MS = 260;
    // Below this much time nothing readable can be drawn at all: the unit reveals
    // itself and the crew moves on.
    const SWARM_LATE_MS = 120;
    // How long an idle swarm lingers before the layer is torn down.
    const SWARM_LINGER_MS = 420;

    // The reference engine dispatches on an item's kind: rect and image get a
    // dragged selection, text gets a caret that types, line gets a pen that lands
    // point after point. A DOM node carries no such label, so it is derived from
    // what the node actually is. The order matters: a picture inside a link is
    // still a picture, and a paragraph holding an inline icon is still text.
    const SWARM_IMAGE_TAGS = new Set(['IMG', 'PICTURE', 'CANVAS', 'VIDEO', 'IFRAME']);
    const SWARM_LINE_TAGS = new Set(['HR']);
    const SWARM_MEDIA_ROLES = new Set(['img', 'presentation']);

    function swarmKindOf(el) {
      const tag = el.tagName;
      if (SWARM_IMAGE_TAGS.has(tag)) return 'image';
      if (SWARM_LINE_TAGS.has(tag)) return 'line';
      // An svg that announces itself as an image is an image; any other svg is
      // drawn like a shape, because its contents are paths, not glyphs.
      if (tag === 'SVG') return SWARM_MEDIA_ROLES.has(el.getAttribute('role')) ? 'image' : 'rect';
      // A background image on an otherwise empty box is how most icons and
      // avatars are built.
      try {
        const background = globalThis.getComputedStyle(el).backgroundImage;
        if (typeof background === 'string' && background !== 'none' && background !== '') {
          return 'image';
        }
      } catch {}
      // Text is a leaf that owns characters: no element children at all, and at
      // least one non-blank text node.
      let hasText = false;
      let hasElement = false;
      for (const child of el.childNodes) {
        if (child.nodeType === 3) {
          if (child.nodeValue !== null && child.nodeValue.trim() !== '') hasText = true;
        } else if (child.nodeType === 1) {
          hasElement = true;
          break;
        }
      }
      if (hasText && !hasElement) return 'text';
      // A container whose own text is the point of it (a one-line label that
      // wraps a link or a span) still reads as text.
      if (!hasElement) return 'rect';
      if (el.childElementCount === 1) {
        const only = el.firstElementChild;
        if (only !== null && only.childElementCount === 0 && SWARM_IMAGE_TAGS.has(only.tagName)) {
          return 'image';
        }
      }
      return 'rect';
    }

    // Everything the swarm needs, scoped to its own shadow root: no page rule can
    // reach in and no theme variable has to exist.
    const SWARM_LAYER_CSS =
      '.cursors,.badges,.overlay{position:absolute;left:0;top:0;width:100%;height:100%;' +
      'pointer-events:none;overflow:hidden}' +
      '.overlay{overflow:visible}' +
      '.cursor{position:absolute;left:0;top:0;will-change:transform}' +
      '.cursor svg{position:absolute;left:0;top:0;overflow:visible;' +
      'filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.28))}' +
      '.badge{position:absolute;transform:translateX(-50%);padding:1px 4px;' +
      'border-radius:2px;background:#0D99FF;color:#fff;white-space:nowrap;' +
      'font:600 11px/16px ui-sans-serif,system-ui,sans-serif}';

    function srand(min, max) {
      return min + Math.random() * (max - min);
    }

    // One decimal place: the transform strings are rebuilt every frame, so they
    // stay short enough to skip re-parsing on most ticks.
    function sf1(number) {
      return Math.round(number * 10) / 10;
    }

    function swarmEaseInOut(k) {
      return k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
    }

    function swarmEaseOut(k) {
      return 1 - Math.pow(1 - k, 3);
    }

    /** Markup for one cursor shape, drawn in a 28x28 viewBox. */
    function swarmCursorSVG(shape, color) {
      if (shape === 'cross') {
        // A white under-stroke keeps the thin cross readable over any content.
        return (
          '<path d="M12 3.5 V20.5 M3.5 12 H20.5" stroke="#fff" stroke-width="3.6" ' +
          'stroke-linecap="round"/><path d="M12 3.5 V20.5 M3.5 12 H20.5" stroke="' + color +
          '" stroke-width="1.5" stroke-linecap="round"/>'
        );
      }
      if (shape === 'ibeam') {
        return (
          '<path d="' + SWARM_IBEAM_D + '" fill="none" stroke="#fff" stroke-width="3.6" ' +
          'stroke-linecap="round"/><path d="' + SWARM_IBEAM_D + '" fill="none" stroke="' +
          color + '" stroke-width="1.5" stroke-linecap="round"/>'
        );
      }
      if (shape === 'pen' || shape === 'pen-add') {
        const st = 'stroke="#fff" stroke-width="1.5" stroke-linejoin="round"';
        let markup =
          '<path d="' + SWARM_PEN_TIP_D + '" fill="' + color + '" ' + st + '/>' +
          '<path d="' + SWARM_PEN_BODY_D + '" fill="' + color + '" ' + st + '/>' +
          '<path d="M3.6 3.6 L8.9 8.9" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/>' +
          '<circle cx="10.2" cy="10.2" r="1.75" fill="none" stroke="#fff" stroke-width="1.2"/>';
        if (shape === 'pen-add') {
          // The little plus that says "the next click lands a point".
          markup +=
            '<path d="M23 19.5 V26.5 M19.5 23 H26.5" stroke="#fff" stroke-width="3.4" ' +
            'stroke-linecap="round"/><path d="M23 19.5 V26.5 M19.5 23 H26.5" ' +
            'stroke="#1E1E1E" stroke-width="1.3" stroke-linecap="round"/>';
        }
        return markup;
      }
      return (
        '<path d="' + SWARM_ARROW_D + '" fill="' + color +
        '" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>'
      );
    }

    /**
     * One swarm agent. The physics constants are the original's: a quarter of the
     * agents are "fast" so the flock never moves as one body.
     */
    function swarmMakeCursor(id, x, y) {
      const fast = Math.random() < 0.25;
      return {
        id,
        x,
        y,
        name: SWARM_NAMES[id % SWARM_NAMES.length],
        color: SWARM_COLORS[id % SWARM_COLORS.length],
        shape: 'arrow',
        press: 0,
        gen: null,
        // Selection outline this cursor is currently drawing, or null.
        box: null,
        // A live reader for that outline: while the element can still move, the
        // outline is re-measured from it every frame instead of being frozen.
        boxOf: null,
        // Set by the runtime, not the factory: 'draw' for a crew leader, 'escort'
        // for a flock member, undefined once it is only flying out.
        mode: '',
        // Orbit centre for an escort; null while leaving.
        anchor: null,
        leaving: false,
        vx: 0,
        vy: 0,
        angle: 0,
        speed: fast ? srand(1000, 1500) : srand(300, 750),
        force: fast ? srand(3500, 6000) : srand(700, 1800),
        spin: fast ? srand(2.2, 3.4) : srand(0.8, 1.8),
        curl: srand(-1, 1),
        orbit: srand(70, 170),
        node: null,
        hotspotX: SWARM_HOT.arrow[0],
        hotspotY: SWARM_HOT.arrow[1],
        paintedShape: '',
        paintedColor: '',
      };
    }

    function apply(ctx) {
      ctx.effect(() => ctx.locale.register(NS, DICTS), 'dsh-step-slide-in: dictionaries');

      let currentSetting = readSetting();
      const t = ctx.locale.bind(NS);

      function BezierEditor({ points, onChange }) {
        const svgRef = React.useRef(null);
        const dragging = React.useRef(null);
        const [x1, y1, x2, y2] = points;

        const plotX = (x) => PAD + x * PLOT;
        const plotY = (y) => PAD + ((Y_MAX - y) / (Y_MAX - Y_MIN)) * PLOT;
        const start = [plotX(0), plotY(0)];
        const end = [plotX(1), plotY(1)];
        const first = [plotX(x1), plotY(y1)];
        const second = [plotX(x2), plotY(y2)];

        const move = (handle, clientX, clientY) => {
          const node = svgRef.current;
          if (node === null) return;
          const rect = node.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0) return;
          const scaleX = VIEW / rect.width;
          const scaleY = VIEW / rect.height;
          const x = clamp(((clientX - rect.left) * scaleX - PAD) / PLOT, 0, 1);
          const y = clamp(
            Y_MAX - (((clientY - rect.top) * scaleY - PAD) / PLOT) * (Y_MAX - Y_MIN),
            Y_MIN,
            Y_MAX,
          );
          const next = points.slice();
          const index = handle === 'p1' ? 0 : 2;
          next[index] = x;
          next[index + 1] = y;
          onChange(next);
        };

        const onPointerDown = (event) => {
          const handle = event.target?.dataset?.handle;
          if (handle !== 'p1' && handle !== 'p2') return;
          event.preventDefault();
          dragging.current = handle;
          event.currentTarget.setPointerCapture(event.pointerId);
          move(handle, event.clientX, event.clientY);
        };

        const onPointerMove = (event) => {
          if (dragging.current === null) return;
          move(dragging.current, event.clientX, event.clientY);
        };

        const onPointerUp = (event) => {
          if (dragging.current === null) return;
          dragging.current = null;
          try {
            event.currentTarget.releasePointerCapture(event.pointerId);
          } catch {}
        };

        const onKeyDown = (handle) => (event) => {
          const step = event.shiftKey ? 0.05 : 0.01;
          let dx = 0;
          let dy = 0;
          if (event.key === 'ArrowLeft') dx = -step;
          else if (event.key === 'ArrowRight') dx = step;
          else if (event.key === 'ArrowUp') dy = step;
          else if (event.key === 'ArrowDown') dy = -step;
          else return;
          event.preventDefault();
          const next = points.slice();
          const index = handle === 'p1' ? 0 : 2;
          next[index] = clamp(next[index] + dx, 0, 1);
          next[index + 1] = clamp(next[index + 1] + dy, Y_MIN, Y_MAX);
          onChange(next);
        };

        const path =
          'M ' +
          start[0] +
          ' ' +
          start[1] +
          ' C ' +
          first[0] +
          ' ' +
          first[1] +
          ' ' +
          second[0] +
          ' ' +
          second[1] +
          ' ' +
          end[0] +
          ' ' +
          end[1];

        const handle = (name, point, label) =>
          h('circle', {
            key: name,
            className: 'dss-handle',
            'data-handle': name,
            cx: point[0],
            cy: point[1],
            r: 7,
            tabIndex: 0,
            role: 'slider',
            'aria-label': label,
            'aria-valuemin': 0,
            'aria-valuemax': 1,
            'aria-valuenow': Math.round((name === 'p1' ? x1 : x2) * 100) / 100,
            'aria-valuetext':
              name === 'p1'
                ? 'x ' + x1.toFixed(2) + ', y ' + y1.toFixed(2)
                : 'x ' + x2.toFixed(2) + ', y ' + y2.toFixed(2),
            onKeyDown: onKeyDown(name),
          });

        return h(
          'div',
          { className: 'dss-custom' },
          h(
            'svg',
            {
              ref: svgRef,
              className: 'dss-plot',
              viewBox: '0 0 ' + VIEW + ' ' + VIEW,
              role: 'group',
              'aria-label': t('curve'),
              onPointerDown,
              onPointerMove,
              onPointerUp,
              onPointerCancel: onPointerUp,
              onLostPointerCapture: () => {
                dragging.current = null;
              },
            },
            h('line', {
              className: 'dss-plot-line',
              x1: start[0],
              y1: start[1],
              x2: end[0],
              y2: end[1],
            }),
            h('line', {
              className: 'dss-plot-guide',
              x1: start[0],
              y1: start[1],
              x2: first[0],
              y2: first[1],
            }),
            h('line', {
              className: 'dss-plot-guide',
              x1: end[0],
              y1: end[1],
              x2: second[0],
              y2: second[1],
            }),
            h('path', { className: 'dss-plot-curve', d: path }),
            h('circle', {
              className: 'dss-plot-anchor',
              cx: start[0],
              cy: start[1],
              r: 2.5,
            }),
            h('circle', { className: 'dss-plot-anchor', cx: end[0], cy: end[1], r: 2.5 }),
            handle('p1', first, t('handle1')),
            handle('p2', second, t('handle2')),
          ),
          h(
            'div',
            { className: 'dss-custom-info' },
            h(
              'div',
              { className: 'dss-custom-value' },
              easeOf({ preset: 'custom', custom: points }),
            ),
            h('div', { className: 'dss-custom-hint' }, t('hint')),
          ),
        );
      }

      function SettingsRow() {
        const [setting, setSetting] = React.useState(currentSetting);
        const settingRef = React.useRef(setting);

        const commit = (next) => {
          settingRef.current = next;
          currentSetting = next;
          storeSetting(next);
          setSetting(next);
        };

        const choose = (id) => {
          const current = settingRef.current;
          if (id === 'custom') {
            commit({ ...current, preset: 'custom', custom: current.custom ?? pointsOf(current).slice() });
            return;
          }
          commit({ ...current, preset: id });
        };

        const chip = (id, label) =>
          h(
            'button',
            {
              key: id,
              type: 'button',
              role: 'radio',
              'aria-checked': setting.preset === id,
              className: 'dss-curve',
              onClick: () => choose(id),
            },
            label,
          );

        const densityChip = (id) =>
          h(
            'button',
            {
              key: id,
              type: 'button',
              role: 'radio',
              'aria-checked': (setting.density ?? DENSITIES[0]) === id,
              className: 'dss-curve',
              onClick: () => commit({ ...settingRef.current, density: id }),
            },
            t('density' + id.charAt(0).toUpperCase() + id.slice(1)),
          );

        const swarmChip = (id) =>
          h(
            'button',
            {
              key: id,
              type: 'button',
              role: 'radio',
              'aria-checked': (setting.swarm === true) === (id === 'on'),
              className: 'dss-curve',
              onClick: () => commit({ ...settingRef.current, swarm: id === 'on' }),
            },
            t(id === 'on' ? 'swarmOn' : 'swarmOff'),
          );

        const customPoints = setting.custom ?? pointsOf(setting);

        return h(
          'div',
          { className: 'dss-row' },
          h('style', null, ROW_CSS),
          h(
            'div',
            { className: 'dss-head' },
            h(
              'div',
              { className: 'dss-text' },
              h('div', { className: 'dss-title' }, t('title')),
              h('div', { className: 'dss-desc' }, t('description')),
            ),
            h(
              'div',
              { className: 'dss-presets' },
              h('span', { className: 'dss-preset-label' }, t('preset')),
              h(
                'div',
                { className: 'dss-curves', role: 'radiogroup', 'aria-label': t('curve') },
                PRESETS.map((item) => chip(item.id, t(item.id))),
                chip('custom', t('custom')),
              ),
            ),
          ),
          setting.preset === 'custom'
            ? h(BezierEditor, {
                points: customPoints,
                onChange: (points) => commit({ ...settingRef.current, preset: 'custom', custom: points }),
              })
            : null,
          h(
            'div',
            { className: 'dss-density' },
            h('span', { className: 'dss-preset-label' }, t('density')),
            h(
              'div',
              { className: 'dss-curves', role: 'radiogroup', 'aria-label': t('density') },
              DENSITIES.map((id) => densityChip(id)),
            ),
            h('div', { className: 'dss-density-hint' }, t('densityHint')),
          ),
          h(
            'div',
            { className: 'dss-swarm' },
            h('span', { className: 'dss-preset-label' }, t('swarm')),
            h(
              'div',
              { className: 'dss-curves', role: 'radiogroup', 'aria-label': t('swarm') },
              swarmChip('off'),
              swarmChip('on'),
            ),
            h('div', { className: 'dss-swarm-hint' }, t('swarmHint')),
          ),
        );
      }

      ctx.slots.inject('settings.general.item', () =>
        ctx.slots.register(
          { name: 'settings.general.item', id: 'step-slide-in', order: 14 },
          SettingsRow,
        ),
      );

      ctx.effect(() => {
        if (typeof document === 'undefined' || document.body === null) return;
        const body = document.body;
        // The page can evaluate this client module more than once (a hot reload or
        // a second registration). The newest instance takes the page over; the
        // previous one only drops its observers and releases its guards, because
        // nothing below writes a persistent inline style.
        const previousDispose = globalThis.__dshStepSlideInDispose;
        if (typeof previousDispose === 'function') {
          try {
            previousDispose();
          } catch {}
        }

        const stats = { queued: 0, started: 0, finished: 0 };
        try {
          globalThis.__dshStepSlideInStats = stats;
        } catch {}
        // Live diagnostics (Shift+Alt+D).
        const diag = {
          version: '1.9.0',
          cached: 0,
          removals: 0,
          roots: 0,
          ghosts: 0,
          mode: '',
          density: 'element',
          recovered: 0,
          errors: 0,
          swarm: false,
          swarmMode: '',
          swarmCursors: 0,
          swarmUnits: 0,
          swarmDegraded: 0,
          swarmRevealed: 0,
        };
        try {
          globalThis.__dshStepSlideInDiag = diag;
        } catch {}

        // The approach and the settle share one animation: the box reaches its
        // landing over-scale at this offset, then the spring samples follow.
        const ANIMATION_MS = DURATION_MS + SETTLE_MS;
        const LAND_OFFSET = DURATION_MS / ANIMATION_MS;

        // A flying (off-screen) transform extends the scrollable overflow of every
        // ancestor. While a descendant is in the air, pin those scroll containers
        // so a scrollbar never pops in or out.
        const scrollGuards = new Map();
        // Guards still held, and the moment they are released anyway.
        const guardRecords = new Map();
        // Resting layout box of every element currently flying, so a group centre
        // is computed from the composition instead of a mid-flight position.
        const flying = new WeakMap();
        // Nodes playing an exit must not re-enter the entrance path, and their own
        // removal must not trigger another exit.
        const exitingNodes = new WeakSet();
        const exitFinishers = new Set();
        // Every received removal is handled at most once. Without this, a node
        // re-inserted for one synchronous measurement and then dropped would
        // create a fresh removal record and loop in the mutation microtask.
        const attemptedExits = new WeakSet();
        // Settled viewport rects of conversation units. React deletes a whole view
        // without a chance to measure it first, and re-inserting those rows would
        // interleave them with the new conversation, so a bulk exit replays them
        // from this cache on a fixed-position ghost layer.
        // Shared across plugin reloads: the page outlives this module.
        const rectCache =
          globalThis.__dshStepSlideInRects ?? (globalThis.__dshStepSlideInRects = new WeakMap());
        let cacheTimer = null;
        let lastCacheAt = 0;
        let cachedScroller = null;
        let cachedScrollTop = null;
        let disposed = false;

        // The curve string is rebuilt once per setting change, not per element.
        let easingKey = '';
        let easingValue = '';
        const easingNow = () => {
          const setting = currentSetting;
          const key = setting.preset + '|' + String(setting.custom ?? '');
          if (key !== easingKey) {
            easingKey = key;
            easingValue = easeOf(setting);
          }
          return easingValue;
        };

        // ------------------------------------------------------ swarm layer
        //
        // The swarm lives in a closed shadow root hanging off the body, so the
        // page's own selectors can never reach it and the MutationObserver above
        // cannot see its churn. The host carries data-dss-skip, which is what
        // keeps the cursors themselves out of both the entrance and the exit path.
        //
        // Pacing note: the original scales its clock by a "tempo" derived from
        // queue pressure. Here the moment a box becomes visible is a wall-clock
        // deadline owned by its reveal animation, so every drawing phase is timed
        // from the remaining budget instead. That is why no tempo appears below.

        const swarm = {
          host: null,
          root: null,
          cursorLayer: null,
          overlay: null,
          badges: null,
          cursors: [],
          crews: [],
          escorts: [],
          queue: [],
          raf: 0,
          last: 0,
          time: 0,
          nextId: 0,
          lingerUntil: 0,
          running: false,
          lastOverlay: '',
          lastBadges: '',
        };

        // Drops the layer DOM only. The queue and the crews are owned by the
        // caller, so a rebuild here can never wipe work that is already queued.
        const swarmTeardownLayer = () => {
          if (swarm.raf !== 0) {
            window.cancelAnimationFrame(swarm.raf);
            swarm.raf = 0;
          }
          swarm.cursors.length = 0;
          swarm.crews.length = 0;
          swarm.escorts.length = 0;
          swarm.lastOverlay = '';
          swarm.lastBadges = '';
          const host = swarm.host;
          swarm.host = null;
          swarm.root = null;
          swarm.cursorLayer = null;
          swarm.overlay = null;
          swarm.badges = null;
          if (host !== null) {
            try {
              host.remove();
            } catch {}
          }
        };

        const swarmEnsureLayer = () => {
          if (swarm.host !== null && swarm.host.isConnected) return true;
          swarmTeardownLayer();
          try {
            if (typeof document.body?.attachShadow !== 'function') return false;
            const host = document.createElement('div');
            host.setAttribute('data-dss-skip', '1');
            host.setAttribute('aria-hidden', 'true');
            host.style.cssText =
              'position:fixed;left:0;top:0;width:100%;height:100%;' +
              'pointer-events:none;overflow:hidden;contain:layout style;' +
              'z-index:2147482200;';
            const root = host.attachShadow({ mode: 'closed' });
            root.innerHTML =
              '<style>' +
              SWARM_LAYER_CSS +
              '</style><div class="cursors"></div>' +
              '<svg class="overlay" xmlns="http://www.w3.org/2000/svg"></svg>' +
              '<div class="badges"></div>';
            document.body.appendChild(host);
            swarm.host = host;
            swarm.root = root;
            swarm.cursorLayer = root.querySelector('.cursors');
            swarm.overlay = root.querySelector('.overlay');
            swarm.badges = root.querySelector('.badges');
            return true;
          } catch {
            return false;
          }
        };

        const swarmShutdown = () => {
          swarm.running = false;
          swarm.queue.length = 0;
          swarm.last = 0;
          swarm.lingerUntil = 0;
          swarmTeardownLayer();
        };

        const swarmRemoveCursor = (cursor) => {
          const index = swarm.cursors.indexOf(cursor);
          if (index >= 0) swarm.cursors.splice(index, 1);
          const crewIndex = swarm.crews.indexOf(cursor);
          if (crewIndex >= 0) swarm.crews.splice(crewIndex, 1);
          const escortIndex = swarm.escorts.indexOf(cursor);
          if (escortIndex >= 0) swarm.escorts.splice(escortIndex, 1);
          if (cursor.node !== null) {
            try {
              cursor.node.remove();
            } catch {}
            cursor.node = null;
          }
        };

        const swarmSpawnCursor = () => {
          const viewWidth = Math.max(1, globalThis.innerWidth || 1);
          const viewHeight = Math.max(1, globalThis.innerHeight || 1);
          const fromLeft = Math.random() < 0.5;
          const cursor = swarmMakeCursor(
            swarm.nextId,
            fromLeft ? -30 : viewWidth + 30,
            srand(0.15, 0.85) * viewHeight,
          );
          swarm.nextId += 1;
          swarm.cursors.push(cursor);
          return cursor;
        };

        // One div per cursor, repainted in place. The svg markup is rebuilt only
        // when the shape or colour actually changes.
        const swarmPaint = (cursor) => {
          if (cursor.node === null || !cursor.node.isConnected) {
            const node = document.createElement('div');
            node.className = 'cursor';
            node.innerHTML = '<svg width="28" height="28" viewBox="0 0 28 28"></svg>';
            cursor.node = node;
            cursor.paintedShape = '';
            if (swarm.cursorLayer !== null) swarm.cursorLayer.appendChild(node);
          }
          const svg = cursor.node.firstChild;
          if (cursor.paintedShape !== cursor.shape || cursor.paintedColor !== cursor.color) {
            cursor.paintedShape = cursor.shape;
            cursor.paintedColor = cursor.color;
            svg.innerHTML = swarmCursorSVG(cursor.shape, cursor.color);
            const hotspot = SWARM_HOT[cursor.shape] ?? SWARM_HOT.arrow;
            cursor.hotspotX = hotspot[0];
            cursor.hotspotY = hotspot[1];
            svg.style.transformOrigin = cursor.hotspotX + 'px ' + cursor.hotspotY + 'px';
          }
          cursor.node.style.transform =
            'translate(' + sf1(cursor.x) + 'px,' + sf1(cursor.y) + 'px)';
          const press = 1 - cursor.press * 0.12;
          svg.style.transform =
            'translate(' +
            -cursor.hotspotX +
            'px,' +
            -cursor.hotspotY +
            'px) rotate(' +
            sf1(cursor.angle) +
            'deg) scale(' +
            press.toFixed(3) +
            ')';
        };

        // Selection outlines, corner handles and the size badge, built as one
        // string per frame and written only when it changed.
        const swarmPaintOverlay = () => {
          let shapes = '';
          let badges = '';
          for (const cursor of swarm.cursors) {
            // Re-read before painting: an outline that was placed on a box which
            // has since moved must move with it, not stay where it was drawn.
            if (cursor.boxOf !== null && cursor.boxOf !== undefined) cursor.box = cursor.boxOf();
            const box = cursor.box;
            if (box === null || box === undefined) continue;
            const x = sf1(box.x);
            const y = sf1(box.y);
            const w = sf1(Math.max(0, box.w));
            const h = sf1(Math.max(0, box.h));
            shapes +=
              '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h +
              '" fill="none" stroke="#0D99FF" stroke-width="1"/>';
            const corners = [
              [x, y],
              [x + w, y],
              [x, y + h],
              [x + w, y + h],
            ];
            for (const corner of corners) {
              shapes +=
                '<rect x="' + sf1(corner[0] - 3.5) + '" y="' + sf1(corner[1] - 3.5) +
                '" width="7" height="7" fill="#fff" stroke="#0D99FF" stroke-width="1"/>';
            }
            if (w >= 8 && h >= 8) {
              badges +=
                '<div class="badge" style="left:' + sf1(box.x + box.w / 2) + 'px;top:' +
                sf1(box.y + box.h + 8) + 'px">' + Math.round(box.w) + ' \u00d7 ' +
                Math.round(box.h) + '</div>';
            }
          }
          if (shapes !== swarm.lastOverlay && swarm.overlay !== null) {
            swarm.overlay.innerHTML = shapes;
            swarm.lastOverlay = shapes;
          }
          if (badges !== swarm.lastBadges && swarm.badges !== null) {
            swarm.badges.innerHTML = badges;
            swarm.lastBadges = badges;
          }
        };

        // -------------------------------------------------- swarm primitives

        function* swarmWait(cursor, seconds) {
          let elapsed = 0;
          while (elapsed < seconds) elapsed += yield;
        }

        /** Ported verbatim apart from the clock: `speed` now comes from a budget. */
        function* swarmMoveTo(cursor, target, options) {
          const opts = options ?? {};
          const get = typeof target === 'function' ? target : () => target;
          const startX = cursor.x;
          const startY = cursor.y;
          const first = get();
          const dist = Math.hypot(first.x - startX, first.y - startY);
          if (dist < 0.5) {
            cursor.x = first.x;
            cursor.y = first.y;
            if (opts.onStep) opts.onStep();
            return;
          }
          const natural = clamp(0.12 + Math.sqrt(dist) * 0.021, 0.14, 0.9);
          const duration = natural / (opts.speed || 1);
          const bend =
            (opts.bend ?? (Math.random() < 0.5 ? -1 : 1) * srand(0.04, 0.14)) *
            Math.min(dist, 600);
          let elapsed = 0;
          while (elapsed < duration) {
            elapsed += yield;
            const k = Math.min(1, elapsed / duration);
            const eased = opts.drag || k < 0.15 ? swarmEaseInOut(k) : swarmEaseOut(k);
            const point = get();
            const dx = point.x - startX;
            const dy = point.y - startY;
            const length = Math.hypot(dx, dy) || 1;
            const off = Math.sin(Math.PI * eased) * bend;
            cursor.x = startX + dx * eased + (-dy / length) * off;
            cursor.y = startY + dy * eased + (dx / length) * off;
            if (opts.onStep) opts.onStep();
          }
          const point = get();
          cursor.x = point.x;
          cursor.y = point.y;
          if (opts.onStep) opts.onStep();
        }

        function* swarmLeave(cursor) {
          const viewWidth = Math.max(1, globalThis.innerWidth || 1);
          const toLeft = cursor.x < viewWidth / 2;
          yield* swarmMoveTo(
            cursor,
            () => ({ x: toLeft ? -60 : viewWidth + 60, y: cursor.y + srand(-40, 40) }),
            { speed: 1.6 },
          );
        }

        const swarmTakeUnit = () => {
          while (swarm.queue.length > 0) {
            const unit = swarm.queue.shift();
            if (unit.el === null || !unit.el.isConnected) continue;
            // Past the point where any drawing can land before the box appears:
            // drawing around something already visible would read as a mistake, so
            // the unit simply reveals itself on schedule.
            if (unit.revealAt - now() < SWARM_LATE_MS) {
              diag.swarmDegraded += 1;
              continue;
            }
            return unit;
          }
          return null;
        };

        // ------------------------------------------- swarm live geometry

        /**
         * Where the unit's element is *right now*. A box can move while the crew
         * is still flying to it — a scroll, a sibling growing above it, a pane
         * resizing — and a cursor aimed at a stale rectangle would draw its
         * selection somewhere the element no longer is. Reading the live box each
         * step costs one layout read per step, which is what the entrance already
         * spends on measuring, and it keeps the cursor, the outline and the badge
         * glued to the element for the whole beat.
         */
        const swarmUnitRect = (unit) => {
          const el = unit.el;
          if (el !== null && el.isConnected) {
            try {
              const rect = el.getBoundingClientRect();
              if (rect.width >= 1 && rect.height >= 1) {
                return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
              }
            } catch {}
          }
          return { left: unit.left, top: unit.top, width: unit.width, height: unit.height };
        };

        /** A target that is re-read every step, so a moving element drags the cursor. */
        const swarmAnchor = (unit, edge) => () => {
          const rect = swarmUnitRect(unit);
          const width = Math.max(1, rect.width);
          const height = Math.max(1, rect.height);
          if (edge === 'start') return { x: rect.left + 1, y: rect.top + 1 };
          if (edge === 'end') return { x: rect.left + width, y: rect.top + height };
          return { x: rect.left + width / 2, y: rect.top + height / 2 };
        };

        /** The speed multiplier that makes one move last roughly `budget` seconds. */
        const swarmPace = (cursor, point, budget) => {
          const dist = Math.hypot(point.x - cursor.x, point.y - cursor.y);
          const natural = clamp(0.12 + Math.sqrt(dist) * 0.021, 0.14, 0.9);
          return Math.max(0.5, natural / Math.max(0.05, budget));
        };

        /** One line of text, for walking a caret down a paragraph. */
        const swarmLineStep = (el) => {
          try {
            const style = globalThis.getComputedStyle(el);
            const height = parseFloat(style.lineHeight);
            if (Number.isFinite(height) && height > 0) return height;
            const size = parseFloat(style.fontSize);
            if (Number.isFinite(size) && size > 0) return size * 1.4;
          } catch {}
          return 16;
        };

        /**
         * Hold the cursor against the element for `seconds`, re-pinning it every
         * frame. This is the difference between a selection that is being held and
         * one that was merely drawn once and then abandoned when the box moved.
         */
        function* swarmHoldAt(cursor, unit, seconds, edge) {
          let elapsed = 0;
          while (elapsed < seconds) {
            elapsed += yield;
            const point = swarmAnchor(unit, edge)();
            cursor.x = point.x;
            cursor.y = point.y;
          }
        }

        // The whole beat, ported from the reference drawBox: crosshair arrives at
        // the top-left corner, presses, drags the selection out, the outline lands
        // exactly on the box, and only then does the content appear inside it.
        // Images hold their selection longer than plain shapes, as in the original.
        function* swarmDrawBox(cursor, unit, deadline, kind) {
          const budget = Math.max(0.1, (deadline - now()) / 1000);
          const approachBudget = budget * 0.32;
          const dragBudget = budget * 0.5;
          const holdBudget = Math.max(0.05, budget * (kind === 'image' ? 0.22 : 0.15));

          cursor.shape = 'cross';
          const start = swarmAnchor(unit, 'start')();
          yield* swarmMoveTo(cursor, swarmAnchor(unit, 'start'), {
            speed: swarmPace(cursor, start, approachBudget),
          });
          if (deadline - now() < 70) {
            cursor.shape = 'arrow';
            cursor.box = null;
            cursor.boxOf = null;
            return;
          }

          // The press lands on the live corner, and the drag writes the outline
          // from that same live corner, so a box that shifts mid-drag is tracked
          // instead of being left behind.
          cursor.press = 1;
          const trackDrag = () => {
            const rect = swarmUnitRect(unit);
            cursor.box = {
              x: rect.left,
              y: rect.top,
              w: Math.max(0, cursor.x - rect.left),
              h: Math.max(0, cursor.y - rect.top),
            };
          };
          trackDrag();
          const end = swarmAnchor(unit, 'end')();
          yield* swarmMoveTo(cursor, swarmAnchor(unit, 'end'), {
            speed: swarmPace(cursor, end, dragBudget),
            drag: true,
            bend: 0,
            onStep: trackDrag,
          });
          // The outline becomes a live read of the element, not a frozen copy:
          // from here on every frame re-measures it.
          cursor.boxOf = () => {
            const rect = swarmUnitRect(unit);
            return {
              x: rect.left,
              y: rect.top,
              w: Math.max(1, rect.width),
              h: Math.max(1, rect.height),
            };
          };
          cursor.box = cursor.boxOf();
          yield* swarmHoldAt(cursor, unit, holdBudget, 'end');
          cursor.box = null;
          cursor.boxOf = null;
          cursor.press = 0;
          cursor.shape = 'arrow';
          diag.swarmRevealed += 1;
        }

        // Not enough time for the full beat: one crosshair lands on the box, the
        // outline flashes, and the reveal happens on schedule regardless.
        function* swarmQuickClick(cursor, unit) {
          const budget = Math.max(0.05, (unit.revealAt - now()) / 1000);
          const center = swarmAnchor(unit, 'center')();
          cursor.shape = 'cross';
          yield* swarmMoveTo(cursor, swarmAnchor(unit, 'center'), {
            speed: Math.max(0.8, swarmPace(cursor, center, budget)),
          });
          cursor.press = 1;
          cursor.boxOf = () => {
            const rect = swarmUnitRect(unit);
            return {
              x: rect.left,
              y: rect.top,
              w: Math.max(1, rect.width),
              h: Math.max(1, rect.height),
            };
          };
          cursor.box = cursor.boxOf();
          yield* swarmHoldAt(cursor, unit, 0.12, 'center');
          cursor.box = null;
          cursor.boxOf = null;
          cursor.press = 0;
          cursor.shape = 'arrow';
        }

        /**
         * Text, ported from the reference drawText: the crosshair lands on the
         * block, drags a selection over the first line, the pointer becomes a caret
         * and the caret walks the text as if it were being typed, then the selection
         * is restored and released.
         *
         * The characters are not really written: rewriting content would break the
         * promise that this plugin never touches the page's DOM. The element fades
         * in on its own animation clock while the caret performs the typing.
         */
        function* swarmDrawText(cursor, unit, deadline) {
          const budget = Math.max(0.1, (deadline - now()) / 1000);
          const approachBudget = budget * 0.24;
          const dragBudget = budget * 0.18;
          const typeBudget = budget * 0.44;
          const holdBudget = Math.max(0.05, budget * 0.12);

          cursor.shape = 'cross';
          const start = swarmAnchor(unit, 'start')();
          yield* swarmMoveTo(cursor, swarmAnchor(unit, 'start'), {
            speed: swarmPace(cursor, start, approachBudget),
          });
          if (deadline - now() < 70) {
            cursor.shape = 'arrow';
            cursor.box = null;
            cursor.boxOf = null;
            return;
          }

          // A short block is clicked; a wide one gets a selection dragged across
          // its first line, which is what the original does too.
          const first = swarmUnitRect(unit);
          const lineHeight = Math.min(Math.max(1, first.height), swarmLineStep(unit.el));
          if (first.width < 140 && first.height < lineHeight * 1.6) {
            cursor.press = 1;
            yield* swarmWait(cursor, 0.06);
            cursor.press = 0;
          } else {
            cursor.press = 1;
            const trackDrag = () => {
              const rect = swarmUnitRect(unit);
              cursor.box = {
                x: rect.left,
                y: rect.top,
                w: Math.max(0, cursor.x - rect.left),
                h: Math.max(0, cursor.y - rect.top),
              };
            };
            const lineEnd = () => {
              const rect = swarmUnitRect(unit);
              return {
                x: rect.left + Math.max(1, rect.width),
                y: rect.top + Math.min(Math.max(1, rect.height), swarmLineStep(unit.el)),
              };
            };
            trackDrag();
            yield* swarmMoveTo(cursor, lineEnd, {
              speed: swarmPace(cursor, lineEnd(), dragBudget),
              drag: true,
              bend: 0,
              onStep: trackDrag,
            });
            cursor.press = 0;
          }

          // The caret: it walks left to right across each line, pausing a beat per
          // column, exactly like the reference's per-character typing cadence.
          cursor.shape = 'ibeam';
          cursor.boxOf = () => {
            const rect = swarmUnitRect(unit);
            return {
              x: rect.left,
              y: rect.top,
              w: Math.max(1, rect.width),
              h: Math.max(1, rect.height),
            };
          };
          const sample = swarmUnitRect(unit);
          const step = swarmLineStep(unit.el);
          const lines = clamp(Math.round(sample.height / Math.max(1, step)), 1, 4);
          const perLine = clamp(Math.round(Math.sqrt(Math.max(1, sample.width)) * 1.2), 3, 14);
          const total = lines * perLine;
          const beat = Math.max(0.012, typeBudget / total);
          for (let line = 0; line < lines; line += 1) {
            for (let column = 0; column <= perLine; column += 1) {
              const rect = swarmUnitRect(unit);
              const fraction = column / perLine;
              cursor.x = rect.left + Math.max(1, rect.width) * fraction;
              cursor.y =
                rect.top +
                Math.min(Math.max(1, rect.height), step) * (line + 0.6);
              yield* swarmWait(cursor, beat);
            }
          }

          cursor.shape = 'arrow';
          cursor.box = cursor.boxOf();
          yield* swarmHoldAt(cursor, unit, holdBudget, 'center');
          cursor.box = null;
          cursor.boxOf = null;
          cursor.press = 0;
          diag.swarmRevealed += 1;
        }

        /**
         * Lines and rules, ported from the reference drawLine: the pointer becomes
         * a pen, lands the first point, then walks the rule point by point, clicking
         * each one down before moving on.
         */
        function* swarmDrawLine(cursor, unit, deadline) {
          const budget = Math.max(0.1, (deadline - now()) / 1000);
          const approachBudget = budget * 0.3;
          const walkBudget = budget * 0.55;
          const holdBudget = Math.max(0.05, budget * 0.15);
          const point = (fraction) => () => {
            const rect = swarmUnitRect(unit);
            return {
              x: rect.left + Math.max(1, rect.width) * fraction,
              y: rect.top + Math.max(1, rect.height) / 2,
            };
          };

          cursor.shape = 'pen-add';
          yield* swarmMoveTo(cursor, point(0), {
            speed: swarmPace(cursor, point(0)(), approachBudget),
          });
          cursor.press = 1;
          yield* swarmWait(cursor, 0.06);
          cursor.press = 0;
          if (deadline - now() < 70) {
            cursor.shape = 'arrow';
            return;
          }

          cursor.shape = 'pen';
          const stops = 4;
          for (let index = 1; index <= stops; index += 1) {
            yield* swarmMoveTo(cursor, point(index / stops), {
              speed: 2.2,
              bend: srand(-0.02, 0.02),
            });
            cursor.press = 1;
            yield* swarmWait(cursor, Math.max(0.03, walkBudget / (stops * 3)));
            cursor.press = 0;
          }
          cursor.shape = 'arrow';
          yield* swarmHoldAt(cursor, unit, holdBudget, 'center');
          cursor.box = null;
          cursor.boxOf = null;
          diag.swarmRevealed += 1;
        }

        /**
         * Dispatch on the unit's kind, the way the reference engine dispatches on an
         * item's kind. A unit that cannot be drawn in time still reveals itself.
         */
        function* swarmCrew(cursor, firstUnit) {
          let unit = firstUnit;
          for (;;) {
            if (unit === null) break;
            const budget = unit.revealAt - now();
            const deadline = unit.revealAt - SWARM_CUSHION_MS;
            if (budget < SWARM_MIN_BUDGET_MS) {
              diag.swarmDegraded += 1;
              yield* swarmQuickClick(cursor, unit);
            } else if (unit.kind === 'text') {
              yield* swarmDrawText(cursor, unit, deadline);
            } else if (unit.kind === 'line') {
              yield* swarmDrawLine(cursor, unit, deadline);
            } else {
              yield* swarmDrawBox(cursor, unit, deadline, unit.kind);
            }
            unit = swarmTakeUnit();
          }
          cursor.shape = 'arrow';
          cursor.box = null;
          cursor.boxOf = null;
          cursor.press = 0;
          cursor.leaving = true;
          yield* swarmLeave(cursor);
        }

        // Boids orbit, ported from the reference follow(). Escorts are physics
        // only: no generator, no per-cursor state machine.
        const swarmUpdateEscorts = (dt) => {
          const list = swarm.escorts;
          if (list.length === 0) return;
          const time = swarm.time;
          const radius = 135 * 0.55 * (1 + 0.18 * Math.sin(time * 0.55));
          const swirl = Math.tanh(Math.cos(time * 0.11) * 3);
          const want = [];
          for (const cursor of list) {
            const anchor = cursor.anchor;
            const originX = anchor === null || anchor === undefined ? 0 : anchor.x;
            const originY = anchor === null || anchor === undefined ? 0 : anchor.y;
            const vx = cursor.x - originX;
            const vy = cursor.y - originY;
            const d = Math.hypot(vx, vy) || 0.01;
            const nx = vx / d;
            const ny = vy / d;
            const angle = Math.atan2(vy, vx);
            const off = (cursor.orbit - 120) * 0.55 * 0.55;
            const rr = radius * (1 + 0.3 * Math.sin(3 * angle + time * 0.7)) + off;
            const tangent = 300 * (0.7 + cursor.spin * 0.2) * swirl;
            const radial = clamp((rr - d) * 4, -2600, 1400);
            const far = Math.min(1, Math.max(0, (d - rr) / 600));
            want.push([
              -ny * tangent + nx * radial - ny * cursor.curl * 400 * far,
              nx * tangent + ny * radial + nx * cursor.curl * 400 * far,
            ]);
          }
          for (let i = 0; i < list.length; i += 1) {
            const cursor = list[i];
            let alignX = 0;
            let alignY = 0;
            let weight = 0;
            for (let j = 0; j < list.length; j += 1) {
              if (j === i) continue;
              const other = list[j];
              const d = Math.hypot(other.x - cursor.x, other.y - cursor.y);
              if (d > 90) continue;
              const w = 1 - d / 90;
              alignX += other.vx * w;
              alignY += other.vy * w;
              weight += w;
            }
            let fx = want[i][0];
            let fy = want[i][1];
            if (weight > 0) {
              fx = fx * 0.7 + (alignX / weight) * 0.3;
              fy = fy * 0.7 + (alignY / weight) * 0.3;
            }
            const anchor = cursor.anchor;
            const originX = anchor === null || anchor === undefined ? 0 : anchor.x;
            const originY = anchor === null || anchor === undefined ? 0 : anchor.y;
            const dist = Math.max(0, Math.hypot(cursor.x - originX, cursor.y - originY) - 180 * 0.55);
            const maxV = cursor.speed * (1.3 + Math.min(3.2, dist / 90));
            const length = Math.hypot(fx, fy);
            if (length > maxV) {
              fx *= maxV / length;
              fy *= maxV / length;
            }
            let gx = (fx - cursor.vx) * 6;
            let gy = (fy - cursor.vy) * 6;
            const maxF = cursor.force * (1.4 + Math.min(5, dist / 50));
            const magnitude = Math.hypot(gx, gy);
            if (magnitude > maxF) {
              gx *= maxF / magnitude;
              gy *= maxF / magnitude;
            }
            cursor.vx += gx * dt;
            cursor.vy += gy * dt;
            cursor.x += cursor.vx * dt;
            cursor.y += cursor.vy * dt;
          }
          for (const cursor of list) {
            if (Math.hypot(cursor.vx, cursor.vy) < 30) continue;
            const turn =
              ((((Math.atan2(cursor.vy, cursor.vx) * 180) / Math.PI + 128 - cursor.angle + 540) %
                360) +
                360) %
                360 -
              180;
            cursor.angle += turn * Math.min(1, dt * 8);
          }
        };

        const swarmSpawnEscorts = (anchor) => {
          for (let index = 0; index < SWARM_ESCORTS; index += 1) {
            if (swarm.cursors.length >= SWARM_MAX_CURSORS) return;
            const escort = swarmSpawnCursor();
            escort.mode = 'escort';
            escort.anchor = anchor;
            // Start on the orbit rather than off-screen: they exist only while a
            // crew is drawing, so there is no time for an entrance.
            const angle = srand(0, Math.PI * 2);
            const radius = srand(50, 130);
            escort.x = anchor.x + Math.cos(angle) * radius;
            escort.y = anchor.y + Math.sin(angle) * radius;
            escort.vx = Math.cos(angle) * 120;
            escort.vy = Math.sin(angle) * 120;
            swarm.escorts.push(escort);
          }
        };

        const swarmReleaseEscorts = (anchor) => {
          for (const escort of [...swarm.escorts]) {
            if (escort.anchor !== anchor) continue;
            // Out of the flock before it starts leaving: the physics pass would
            // otherwise steer it around a null anchor at the viewport origin.
            const index = swarm.escorts.indexOf(escort);
            if (index >= 0) swarm.escorts.splice(index, 1);
            escort.anchor = null;
            escort.leaving = true;
            escort.gen = swarmLeave(escort);
          }
        };

        function swarmFrame() {
          swarm.raf = 0;
          if (disposed || !swarm.running) return;
          const stamp = now();
          const dt = swarm.last === 0 ? 0.016 : Math.min(0.05, (stamp - swarm.last) / 1000);
          swarm.last = stamp;
          swarm.time += dt;

          // Claim the unit while spawning: a crew that has already taken one must
          // not race the others for the same box.
          while (swarm.crews.length < SWARM_MAX_CREWS) {
            const unit = swarmTakeUnit();
            if (unit === null) break;
            const cursor = swarmSpawnCursor();
            cursor.mode = 'draw';
            swarm.crews.push(cursor);
            swarmSpawnEscorts(cursor);
            cursor.gen = swarmCrew(cursor, unit);
          }

          for (const cursor of [...swarm.cursors]) {
            if (cursor.gen === null) continue;
            let done = false;
            try {
              done = cursor.gen.next(dt).done === true;
            } catch {
              done = true;
              diag.errors += 1;
            }
            if (!done) continue;
            cursor.gen = null;
            if (cursor.mode === 'draw') {
              swarmReleaseEscorts(cursor);
              const crewIndex = swarm.crews.indexOf(cursor);
              if (crewIndex >= 0) swarm.crews.splice(crewIndex, 1);
            }
            if (cursor.leaving) swarmRemoveCursor(cursor);
          }

          for (const cursor of swarm.cursors) {
            if (cursor.press > 0) cursor.press = Math.max(0, cursor.press - dt * 6);
          }
          swarmUpdateEscorts(dt);
          for (const cursor of swarm.cursors) swarmPaint(cursor);
          swarmPaintOverlay();

          diag.swarmCursors = swarm.cursors.length;
          diag.swarmUnits = swarm.queue.length;

          const idle =
            swarm.queue.length === 0 && swarm.crews.length === 0 && swarm.escorts.length === 0;
          if (idle) {
            if (swarm.lingerUntil === 0) swarm.lingerUntil = stamp + SWARM_LINGER_MS;
            else if (stamp >= swarm.lingerUntil) {
              diag.swarmMode = 'idle';
              swarmShutdown();
              return;
            }
          } else {
            swarm.lingerUntil = 0;
          }
          swarm.raf = window.requestAnimationFrame(swarmFrame);
        }

        const swarmStart = () => {
          if (swarm.running) return true;
          if (!swarmEnsureLayer()) return false;
          swarm.running = true;
          swarm.last = 0;
          swarm.time = 0;
          swarm.raf = window.requestAnimationFrame(swarmFrame);
          return true;
        };

        /**
         * The swarm entrance: the box fades in on its own animation clock, while a
         * crew draws a selection around where it will appear. The reveal owns the
         * visibility, so a throttled tab can never leave content hidden.
         */
        const swarmEnqueue = (units, step, startedAt, inspected) => {
          // Resolved before any animation exists: when the layer cannot be built
          // there is nothing to draw, and the caller falls back to the flight.
          if (!swarmEnsureLayer()) return false;
          const count = units.length;
          for (let index = 0; index < count; index += 1) {
            const unit = units[index];
            const el = unit.el;
            const revealDelay = Math.round(step * index) + SWARM_REVEAL_LEAD_MS;
            let reveal;
            try {
              reveal = el.animate(
                [
                  { opacity: '0', scale: '1.04' },
                  { opacity: '1', scale: '1' },
                ],
                {
                  duration: SWARM_REVEAL_MS,
                  delay: revealDelay,
                  easing: 'cubic-bezier(.2, .8, .2, 1)',
                  fill: 'backwards',
                },
              );
            } catch {
              continue;
            }
            reveal.id = 'dss-swarm-reveal';
            const guards = guardScrollAncestors(el, inspected);
            flying.set(el, {
              left: unit.left,
              top: unit.top,
              width: unit.width,
              height: unit.height,
            });
            guardRecords.set(el, {
              guards,
              until: startedAt + revealDelay + SWARM_REVEAL_MS + 500,
            });
            stats.queued += 1;
            stats.started += 1;
            swarm.queue.push({
              el,
              left: unit.left,
              top: unit.top,
              width: unit.width,
              height: unit.height,
              revealAt: startedAt + revealDelay,
              // Which beat this element gets. Resolved once, while the node is
              // still cheap to read, and never re-derived mid-draw.
              kind: swarmKindOf(el),
            });
            reveal.finished
              .catch(() => {})
              .then(() => {
                if (disposed) return;
                stats.finished += 1;
                releaseElement(el);
              });
          }
          if (swarm.queue.length === 0) return false;
          swarm.queue.sort((a, b) => a.revealAt - b.revealAt);
          // Reported here as well as per frame: the queue is worth seeing before
          // the first frame has had a chance to claim anything from it.
          diag.swarmUnits = swarm.queue.length;
          if (!swarmStart()) {
            // No layer, no drawing: undo the reveal animations and let the caller
            // fly the boxes in instead. Cancelling first matters — otherwise the
            // two entrances would fight over the same element's opacity.
            for (const unit of swarm.queue) {
              try {
                for (const animation of unit.el.getAnimations()) {
                  if (animation.id === 'dss-swarm-reveal') animation.cancel();
                }
              } catch {}
            }
            swarm.queue.length = 0;
            return false;
          }
          diag.swarm = true;
          diag.swarmMode = 'running';
          return true;
        };

        const swarmForgetUnit = (el) => {
          for (let index = swarm.queue.length - 1; index >= 0; index -= 1) {
            if (swarm.queue[index].el === el) swarm.queue.splice(index, 1);
          }
        };

        // Landing keyframes: the approach uses the chosen curve, the spring part is
        // sampled from a damped oscillation and interpolated linearly. It always
        // ends at scale 1 and carries no forward fill, so the box can never be left
        // scaled or hidden whatever happens to the page.
        const landingFrames = () => {
          const frames = [
            { scale: '1', offset: 0, easing: easingNow() },
            { scale: String(LAND_SCALE), offset: LAND_OFFSET },
          ];
          for (let index = 1; index < SETTLE_FRAMES.length; index += 1) {
            const frame = SETTLE_FRAMES[index];
            frames.push({
              scale: frame.scale,
              offset: LAND_OFFSET + (1 - LAND_OFFSET) * frame.offset,
            });
          }
          return frames;
        };

        /**
         * Visual centre of the elements in one container, weighted by area so a
         * large block counts more than a thin label. A flying box is measured at
         * its resting layout box, not at its current flown-out position. Returns
         * null when the container has fewer than two measurable elements, so there
         * is no group line to speak of.
         *
         * The result is cached per container for the whole batch: siblings share a
         * parent, so a hundred new rows measure their neighbours once.
         */
        const groupCenter = (parent, cache, skip) => {
          const cached = cache.get(parent);
          if (cached !== undefined) return cached;
          const children = parent.children;
          const total = children.length;
          const sampled = Math.min(total, GROUP_SCAN_MAX);
          const stride = total / Math.max(1, sampled);
          let sumX = 0;
          let sumY = 0;
          let sumArea = 0;
          let count = 0;
          for (let index = 0; index < sampled; index += 1) {
            const child = children[Math.floor(index * stride)];
            if (child === undefined || child === null || skip(child)) continue;
            const resting = flying.get(child);
            let left;
            let top;
            let width;
            let height;
            if (resting !== undefined) {
              left = resting.left;
              top = resting.top;
              width = resting.width;
              height = resting.height;
            } else {
              const rect = child.getBoundingClientRect();
              left = rect.left;
              top = rect.top;
              width = rect.width;
              height = rect.height;
            }
            const area = width * height;
            if (!(area >= 1)) continue;
            sumX += (left + width / 2) * area;
            sumY += (top + height / 2) * area;
            sumArea += area;
            count += 1;
          }
          const center =
            count < 2 || sumArea < 1 ? null : { x: sumX / sumArea, y: sumY / sumArea };
          cache.set(parent, center);
          return center;
        };

        const releaseScrollGuards = (guarded) => {
          if (guarded === undefined) return;
          for (const node of guarded) {
            const state = scrollGuards.get(node);
            if (state === undefined) continue;
            state.count -= 1;
            if (state.count > 0) continue;
            scrollGuards.delete(node);
            if (state.pinnedX !== '' && node.style.overflowX === state.pinnedX) {
              node.style.overflowX = state.savedX;
            }
            if (state.pinnedY !== '' && node.style.overflowY === state.pinnedY) {
              node.style.overflowY = state.savedY;
            }
          }
        };

        const releaseElement = (el) => {
          const record = guardRecords.get(el);
          if (record === undefined) return;
          guardRecords.delete(el);
          flying.delete(el);
          releaseScrollGuards(record.guards);
        };

        /**
         * Pin the scroll containers whose overflow a flying transform would change.
         * `inspected` caches each ancestor's computed overflow for the current
         * batch, so a shared ancestor is read once, not once per element.
         */
        const guardScrollAncestors = (el, inspected) => {
          const guarded = [];
          let node = el.parentElement;
          while (node !== null) {
            const existing = scrollGuards.get(node);
            if (existing !== undefined) {
              existing.count += 1;
              guarded.push(node);
              node = node.parentElement;
              continue;
            }
            let axes = inspected.get(node);
            if (axes === undefined) {
              const computed = getComputedStyle(node);
              axes = {
                scrollX: computed.overflowX === 'auto' || computed.overflowX === 'scroll',
                scrollY: computed.overflowY === 'auto' || computed.overflowY === 'scroll',
              };
              inspected.set(node, axes);
            }
            if (axes.scrollX || axes.scrollY) {
              const state = {
                count: 1,
                savedX: node.style.overflowX,
                savedY: node.style.overflowY,
                pinnedX: '',
                pinnedY: '',
              };
              // A transformed child extends the scrollable area, so hide the
              // horizontal axis outright; it normally has no bar to keep.
              if (axes.scrollX) {
                state.pinnedX = 'hidden';
                node.style.overflowX = state.pinnedX;
              }
              // A bar that is visible right now must stay visible during the flight.
              if (axes.scrollY && node.scrollHeight > node.clientHeight + 1) {
                state.pinnedY = 'scroll';
                node.style.overflowY = state.pinnedY;
              }
              scrollGuards.set(node, state);
              guarded.push(node);
            }
            node = node.parentElement;
          }
          return guarded;
        };

        // Which ancestor carries the entrance animation: 0 the element itself,
        // 1 its parent container, 2 the container above that. Exits always use the
        // removed box itself: ghosting a live ancestor would hide live siblings.
        const densityLevel = () => {
          const density = currentSetting.density;
          if (density === 'parent') return 1;
          if (density === 'grandparent') return 2;
          return 0;
        };

        const levelTarget = (el, level) => {
          let target = el;
          for (let step = 0; step < level; step += 1) {
            const parent = target.parentElement;
            if (parent === null || parent === body || parent === document.documentElement) break;
            target = parent;
          }
          return target;
        };

        const measurable = (el) => {
          if (!el.isConnected || SKIP_TAGS.has(el.tagName)) return null;
          if (el.closest('[data-dss-skip]') !== null) return null;
          if (el.closest('svg') !== null) return null;
          const computed = getComputedStyle(el);
          if (!ANIMATABLE_DISPLAYS.has(computed.display)) return null;
          if (computed.visibility === 'hidden') return null;
          const rect = el.getBoundingClientRect();
          if (rect.width < 1 || rect.height < 1) return null;
          return rect;
        };

        /**
         * Freeze one batch before paint. Every new box is already laid out exactly
         * where it will land, so none of the layout is touched: the flight is a
         * composited animation with a `backwards` fill, which parks the box
         * off-screen for its own delay and then hands it back at its natural
         * position. No inline style is ever written, so no dropped event, no error
         * and no second plugin instance can leave a box hidden.
         *
         * Which box carries it is the density setting: the new element itself, its
         * parent container, or the container above that. Targets are deduped per
         * batch and only the outermost container of a nested group flies, so a
         * container that receives several children animates once.
         *
         * The flight line is the one from the target's parent container's visual
         * centre through the target's own visual centre.
         */
        function enqueue(elements) {
          if (disposed || elements.length === 0) return;
          if (prefersReduced()) return;
          if (elements.length > MAX_ENTRANCE_BATCH) return;
          if (typeof Element.prototype.animate !== 'function') return;

          const level = densityLevel();
          const candidates = [];
          for (const el of elements) {
            if (candidates.length >= MAX_QUEUE) break;
            if (flying.has(el)) continue;
            const rect = measurable(el);
            if (rect === null) continue;
            candidates.push({ el, rect });
          }
          if (candidates.length === 0) return;

          const batch = new Set(candidates.map((candidate) => candidate.el));
          const units = [];
          if (level === 0) {
            for (const candidate of candidates) {
              units.push({
                el: candidate.el,
                left: candidate.rect.left,
                top: candidate.rect.top,
                width: candidate.rect.width,
                height: candidate.rect.height,
              });
            }
          } else {
            const targets = [];
            const seen = new Set();
            for (const candidate of candidates) {
              const target = levelTarget(candidate.el, level);
              if (seen.has(target) || flying.has(target)) continue;
              seen.add(target);
              targets.push(target);
            }
            // A container inside another target is covered by the outer flight.
            for (const target of targets) {
              let nested = false;
              for (const other of targets) {
                if (other !== target && other.contains(target)) {
                  nested = true;
                  break;
                }
              }
              if (nested) continue;
              const rect = measurable(target);
              if (rect === null) continue;
              units.push({
                el: target,
                left: rect.left,
                top: rect.top,
                width: rect.width,
                height: rect.height,
              });
            }
          }
          if (units.length === 0) return;

          const groupCache = new Map();
          const inspected = new Map();
          const count = units.length;
          const step = START_SPREAD_MS / count;
          const startedAt = now();
          diag.density = level === 0 ? 'element' : level === 1 ? 'parent' : 'grandparent';

          // Swarm mode swaps the flight for a drawn reveal. It only runs while the
          // setting is on, and a failed start falls through to the original path
          // below, so the default entrance stays exactly as it was.
          if (currentSetting.swarm === true && swarmEnqueue(units, step, startedAt, inspected)) {
            return;
          }

          // Reads first, writes last: the batch forces at most one layout flush.
          for (const unit of units) {
            const parent = unit.el.parentElement;
            const center = parent === null ? null : groupCenter(parent, groupCache, () => false);
            const ray = flightRay(unit.left, unit.top, unit.width, unit.height, center?.x, center?.y);
            const delta = offscreenDelta(
              ray.x,
              ray.y,
              unit.left,
              unit.top,
              Math.max(unit.width, 1),
              Math.max(unit.height, 1),
            );
            unit.dx = delta.x;
            unit.dy = delta.y;
          }

          const curve = easingNow();
          for (let index = 0; index < count; index += 1) {
            const unit = units[index];
            const el = unit.el;
            // The whole batch spreads its starts over START_SPREAD_MS.
            const delay = Math.round(step * index);
            const parked = unit.dx + 'px ' + unit.dy + 'px';
            let slide;
            try {
              slide = el.animate(
                [{ translate: parked }, { translate: '0px 0px' }],
                { duration: DURATION_MS, delay, easing: curve, fill: 'backwards' },
              );
            } catch {
              continue;
            }
            slide.id = 'dss-enter-slide';
            const animations = [slide];
            const partner = el.parentElement;
            const root = partner === null || !batch.has(partner);
            if (root) {
              try {
                const landing = el.animate(landingFrames(), {
                  duration: ANIMATION_MS,
                  delay,
                  easing: 'linear',
                  fill: 'backwards',
                });
                landing.id = 'dss-enter-land';
                animations.push(landing);
              } catch {}
            }
            const guards = guardScrollAncestors(el, inspected);
            flying.set(el, {
              left: unit.left,
              top: unit.top,
              width: unit.width,
              height: unit.height,
            });
            guardRecords.set(el, { guards, until: startedAt + delay + ANIMATION_MS + 500 });
            stats.queued += 1;
            stats.started += 1;
            // Release the scroll pin as soon as the flight is over. The promise
            // cannot keep the box hidden; it only owns the guard lifetime.
            Promise.all(
              animations.map((animation) => animation.finished.catch(() => {})),
            ).then(() => {
              if (disposed) return;
              stats.finished += 1;
              releaseElement(el);
            });
          }
        }

        const isExitingSubtree = (node) => {
          let current = node;
          while (current !== null) {
            if (exitingNodes.has(current)) return true;
            current = current.parentElement;
          }
          return false;
        };

        const dropExitNode = (el) => {
          exitingNodes.add(el);
          if (el.parentNode !== null) el.parentNode.removeChild(el);
        };

        const refreshRectCache = () => {
          if (disposed || document.hidden) return;
          const units = document.querySelectorAll(
            '[data-chat-anchor-key], [data-chat-call-id]',
          );
          const margin = 240;
          const viewportHeight = globalThis.innerHeight;
          let cachedCount = 0;
          for (const unit of units) {
            const rect = unit.getBoundingClientRect();
            if (rect.width < 1 || rect.height < 1) {
              rectCache.delete(unit);
              continue;
            }
            if (rect.bottom < -margin || rect.top > viewportHeight + margin) {
              rectCache.delete(unit);
              continue;
            }
            rectCache.set(unit, {
              left: rect.left,
              top: rect.top,
              width: rect.width,
              height: rect.height,
            });
            cachedCount += 1;
          }
          // Remember where the conversation was scrolled: a switch can happen
          // after scrolling without any other cache refresh.
          const scroller = document.querySelector('[data-conversation-scroll]');
          cachedScroller = scroller;
          cachedScrollTop = scroller === null ? null : scroller.scrollTop;
          diag.cached = cachedCount;
        };

        // Interaction-time refresh: a conversation switch is usually a click (or a
        // key chord). Capture the geometry synchronously before React handles it,
        // so a cached frame from an earlier idle moment cannot be used instead.
        const refreshCacheNow = () => {
          if (disposed || document.hidden) return;
          const stamp = Date.now();
          if (stamp - lastCacheAt < 100) return;
          lastCacheAt = stamp;
          refreshRectCache();
        };

        const scheduleRectCache = () => {
          if (disposed) return;
          if (cacheTimer !== null) window.clearTimeout(cacheTimer);
          cacheTimer = window.setTimeout(() => {
            cacheTimer = null;
            // Never snapshot a frame that is still flying.
            if (guardRecords.size > 0 || exitFinishers.size > 0) {
              scheduleRectCache();
              return;
            }
            refreshRectCache();
          }, 250);
        };

        const GHOST_MAX = 48;

        const pickGhostHost = () => {
          const content = document.querySelector('[data-conversation-content]');
          if (content === null) return body;
          const computed = getComputedStyle(content);
          // A transformed / contained host would make position:fixed relative to
          // itself; the constant-position body is the safe fallback.
          if (
            computed.transform !== 'none' ||
            computed.filter !== 'none' ||
            computed.perspective !== 'none' ||
            (computed.contain !== 'none' && computed.contain !== '')
          ) {
            return body;
          }
          return content;
        };

        /**
         * Freeze a removed box as a fixed-position copy of itself and let it fly
         * away, following the line from its group's visual centre out through its
         * own. The node is out of the document flow before the next paint, so
         * nothing else can move; it is deleted content, so any failure simply drops
         * it instead of leaving a layer behind.
         */
        const ghostExit = (el, rect, host, centerX, centerY) => {
          if (typeof el.animate !== 'function') {
            dropExitNode(el);
            return false;
          }
          // A box that is still flying in must not inherit that flight here.
          try {
            for (const animation of el.getAnimations()) {
              if (
                animation.id === 'dss-enter-slide' ||
                animation.id === 'dss-enter-land' ||
                animation.id === 'dss-swarm-reveal'
              ) {
                animation.cancel();
              }
            }
          } catch {}
          // A queued swarm drawing must not keep aiming at a box that is leaving.
          swarmForgetUnit(el);
          flying.delete(el);
          exitingNodes.add(el);
          for (const child of el.querySelectorAll('*')) exitingNodes.add(child);
          const style = el.style;
          style.position = 'fixed';
          style.left = rect.left + 'px';
          style.top = rect.top + 'px';
          style.width = rect.width + 'px';
          style.height = rect.height + 'px';
          style.margin = '0';
          style.boxSizing = 'border-box';
          style.pointerEvents = 'none';
          style.zIndex = '2147482000';
          try {
            host.appendChild(el);
          } catch {
            dropExitNode(el);
            return false;
          }
          const ray = flightRay(rect.left, rect.top, rect.width, rect.height, centerX, centerY);
          const delta = offscreenDelta(
            ray.x,
            ray.y,
            rect.left,
            rect.top,
            Math.max(rect.width, 1),
            Math.max(rect.height, 1),
          );
          let animation;
          try {
            animation = el.animate(
              [{ translate: '0px 0px' }, { translate: delta.x + 'px ' + delta.y + 'px' }],
              { duration: DURATION_MS, easing: easingNow(), fill: 'both' },
            );
          } catch {
            dropExitNode(el);
            return false;
          }
          animation.id = 'dss-exit-ghost';
          let done = false;
          const finalize = () => {
            if (done) return;
            done = true;
            exitFinishers.delete(record);
            try {
              animation.cancel();
            } catch {}
            if (el.parentNode !== null) el.parentNode.removeChild(el);
            scheduleRectCache();
          };
          const record = { finalize, until: now() + DURATION_MS + 500 };
          exitFinishers.add(record);
          animation.finished.then(finalize).catch(() => {});
          return true;
        };

        const hasConversationUnit = (el) =>
          el.hasAttribute('data-chat-anchor-key') ||
          el.hasAttribute('data-chat-call-id') ||
          el.querySelector('[data-chat-anchor-key], [data-chat-call-id]') !== null;

        /**
         * Bulk teardown (view replacement, conversation switch): the rows are
         * replayed from cached geometry, never re-inserted, because inserting
         * them back would interleave the old view with the new one. Their flight
         * centre is the visual centre of the removed rows themselves.
         */
        const startCachedGhosts = (roots, host) => {
          const picked = new Set();
          const picks = [];
          const viewWidth = globalThis.innerWidth;
          const viewHeight = globalThis.innerHeight;
          for (const candidate of roots) {
            if (picks.length >= GHOST_MAX) break;
            const el = candidate.node;
            if (el.isConnected || isExitingSubtree(el)) continue;
            if (picked.has(el)) continue;
            const base = rectCache.get(el);
            if (base === undefined) continue;
            if (base.width < 1 || base.height < 1) continue;
            if (base.height > viewHeight * EXIT_MAX_HEIGHT_RATIO) continue;
            // Cached rects may predate a scroll; shift them by how far the
            // conversation scroller moved since that frame.
            let rect = base;
            if (
              cachedScroller !== null &&
              cachedScroller.isConnected &&
              cachedScrollTop !== null
            ) {
              const shift = cachedScrollTop - cachedScroller.scrollTop;
              if (shift !== 0) rect = { ...base, top: base.top + shift };
            }
            if (
              rect.top + rect.height <= 0 ||
              rect.top >= viewHeight ||
              rect.left + rect.width <= 0 ||
              rect.left >= viewWidth
            ) {
              continue;
            }
            let ancestor = el.parentElement;
            let nested = false;
            while (ancestor !== null) {
              if (picked.has(ancestor)) {
                nested = true;
                break;
              }
              ancestor = ancestor.parentElement;
            }
            if (nested) continue;
            picked.add(el);
            picks.push({ el, rect });
          }
          diag.ghosts = picks.length;
          if (picks.length === 0) {
            diag.mode = 'ghost-empty';
            return;
          }
          let sumX = 0;
          let sumY = 0;
          let sumArea = 0;
          for (const pick of picks) {
            const area = pick.rect.width * pick.rect.height;
            sumX += (pick.rect.left + pick.rect.width / 2) * area;
            sumY += (pick.rect.top + pick.rect.height / 2) * area;
            sumArea += area;
          }
          const center =
            picks.length < 2 || sumArea < 1 ? null : { x: sumX / sumArea, y: sumY / sumArea };
          for (const pick of picks) ghostExit(pick.el, pick.rect, host, center?.x, center?.y);
        };

        /**
         * One observed removal: measure it without letting it stay in flow, then
         * hand it to the ghost layer.
         *
         * A cached rect means no DOM round trip at all. Otherwise the node is put
         * back for exactly one synchronous measurement and detached again before
         * the next paint, so its old slot never becomes visible.
         */
        const startGhost = (candidate, host, groupCache) => {
          const el = candidate.node;
          const parent = candidate.parent;
          if (attemptedExits.has(el)) return false;
          if (exitingNodes.has(el)) return false;
          if (flying.has(el)) return false;
          if (!(parent instanceof Element) || !parent.isConnected) return false;
          if (SKIP_TAGS.has(el.tagName) || el instanceof SVGElement) return false;
          if (el.closest('[data-dss-skip]') !== null) return false;
          if (INLINE_EXIT_TAGS.has(el.tagName)) return false;
          let ancestor = el.parentElement;
          while (ancestor !== null) {
            if (flying.has(ancestor)) return false;
            ancestor = ancestor.parentElement;
          }
          if (el.querySelectorAll('*').length > EXIT_MAX_DESCENDANTS) return false;
          attemptedExits.add(el);

          const viewHeight = globalThis.innerHeight;
          const cached = rectCache.get(el);
          if (cached !== undefined) {
            if (cached.width < 1 || cached.height < 1) return false;
            if (cached.height > viewHeight * EXIT_MAX_HEIGHT_RATIO) return false;
            let rect = cached;
            if (
              cachedScroller !== null &&
              cachedScroller.isConnected &&
              cachedScrollTop !== null
            ) {
              const shift = cachedScrollTop - cachedScroller.scrollTop;
              if (shift !== 0) rect = { ...cached, top: cached.top + shift };
            }
            const skip = new Set([el]);
            const center = groupCenter(parent, groupCache, (node) => skip.has(node));
            return ghostExit(el, rect, host, center?.x, center?.y);
          }

          const next =
            candidate.next !== null && candidate.next.parentNode === parent
              ? candidate.next
              : null;
          try {
            parent.insertBefore(el, next);
          } catch {
            dropExitNode(el);
            return false;
          }
          if (!el.isConnected) {
            dropExitNode(el);
            return false;
          }
          const computed = getComputedStyle(el);
          const measured = el.getBoundingClientRect();
          if (
            !ANIMATABLE_DISPLAYS.has(computed.display) ||
            computed.visibility === 'hidden' ||
            measured.width < 1 ||
            measured.height < 1 ||
            measured.height > viewHeight * EXIT_MAX_HEIGHT_RATIO
          ) {
            dropExitNode(el);
            return false;
          }
          const skip = new Set([el]);
          const center = groupCenter(parent, groupCache, (node) => skip.has(node));
          return ghostExit(
            el,
            {
              left: measured.left,
              top: measured.top,
              width: measured.width,
              height: measured.height,
            },
            host,
            center?.x,
            center?.y,
          );
        };

        const startExits = (candidates, replacing = false) => {
          if (disposed || prefersReduced() || candidates.length === 0) return;
          const candidateSet = new Set(candidates.map((candidate) => candidate.node));
          const roots = [];
          for (const candidate of candidates) {
            let parent = candidate.node.parentElement;
            let topmost = true;
            while (parent !== null) {
              if (candidateSet.has(parent)) {
                topmost = false;
                break;
              }
              parent = parent.parentElement;
            }
            if (topmost) roots.push(candidate);
          }
          if (roots.length === 0) return;
          diag.removals = candidates.length;
          diag.roots = roots.length;
          const host = pickGhostHost();
          // A commit that adds conversation units while removing them is a view
          // replacement (conversation switch): replay the cached geometry instead
          // of touching the live flow at all.
          const replacement =
            replacing === true && roots.some((candidate) => hasConversationUnit(candidate.node));
          if (roots.length > EXIT_MAX_ROOTS || replacement) {
            diag.mode = 'ghost-cached';
            startCachedGhosts(roots, host);
            return;
          }
          const groupCache = new Map();
          let started = 0;
          for (const candidate of roots) {
            if (started >= GHOST_MAX) break;
            if (startGhost(candidate, host, groupCache)) started += 1;
          }
          diag.ghosts = started;
          diag.mode = started === 0 ? 'none' : 'ghost';
        };

        const collect = (node, out, seen) => {
          if (seen.has(node)) return;
          seen.add(node);
          out.push(node);
          for (const child of node.querySelectorAll('*')) {
            if (seen.has(child)) continue;
            seen.add(child);
            out.push(child);
          }
        };

        const observer = new MutationObserver((records) => {
          try {
            const added = [];
            const seen = new Set();
            const removed = [];
            for (const record of records) {
              if (record.type !== 'childList') continue;
              for (const node of record.addedNodes) {
                if (node.nodeType !== Node.ELEMENT_NODE) continue;
                if (isExitingSubtree(node)) continue;
                collect(node, added, seen);
              }
              if (record.removedNodes.length > 0) {
                const parent = record.target;
                for (const node of record.removedNodes) {
                  if (node.nodeType !== Node.ELEMENT_NODE) continue;
                  // `seen` holds everything added in this same batch; such a node
                  // was never painted, so flying it out would flash unseen content.
                  if (node.isConnected || seen.has(node) || isExitingSubtree(node)) continue;
                  removed.push({ node, parent, next: record.nextSibling });
                }
              }
            }
            if (added.length > 0) enqueue(added);
            if (removed.length > 0) {
              const replacing = added.some((node) => hasConversationUnit(node));
              startExits(removed, replacing);
            }
          } catch {
            diag.errors += 1;
          }
        });
        // Shift+Alt+D toggles a small diagnostic overlay.
        const onDiagKey = (event) => {
          if (!event.shiftKey || !event.altKey || event.ctrlKey || event.metaKey) return;
          if (event.code !== 'KeyD') return;
          event.preventDefault();
          const existing = document.getElementById('dss-diag');
          if (existing !== null) {
            existing.remove();
            return;
          }
          const pre = document.createElement('pre');
          pre.id = 'dss-diag';
          pre.dataset.dssSkip = '1';
          diag.scroll = cachedScroller === null ? null : cachedScroller.scrollTop;
          diag.cachedScroll = cachedScrollTop;
          diag.flying = guardRecords.size;
          diag.ghostsLive = exitFinishers.size;
          pre.textContent = 'dss-step-slide-in ' + JSON.stringify(diag);
          pre.style.cssText =
            'position:fixed;left:12px;top:12px;z-index:2147483000;margin:0;padding:8px 10px;' +
            'max-width:90vw;max-height:40vh;overflow:auto;background:#111;color:#9f9;' +
            'font:11px/1.4 monospace;white-space:pre-wrap;pointer-events:none;border-radius:6px';
          body.appendChild(pre);
        };
        window.addEventListener('keydown', onDiagKey, true);

        observer.observe(body, { childList: true, subtree: true });
        body.addEventListener('scroll', scheduleRectCache, true);
        body.addEventListener('pointerdown', refreshCacheNow, true);
        body.addEventListener('mousedown', refreshCacheNow, true);
        body.addEventListener('keydown', refreshCacheNow, true);
        body.addEventListener('wheel', refreshCacheNow, true);
        // Belt and braces for switches that no local input event precedes.
        const idleCacheInterval = window.setInterval(() => {
          if (guardRecords.size === 0 && exitFinishers.size === 0) refreshRectCache();
        }, 800);
        // Safety net for the two things that do own real state: scroll guards and
        // ghost layers. Both are released at their deadline even if a browser
        // animation event is lost. Entering elements need none: their animation
        // hands them back at the natural position by itself.
        const sweep = window.setInterval(() => {
          if (disposed) return;
          const current = now();
          for (const [el, record] of [...guardRecords]) {
            if (record.until <= current) {
              diag.recovered += 1;
              releaseElement(el);
              // A drawing that outlived its reveal is a lost crew: drop the unit
              // so no cursor keeps chasing a box that is already visible.
              swarmForgetUnit(el);
            }
          }
          for (const record of [...exitFinishers]) {
            if (record.until <= current) {
              diag.recovered += 1;
              record.finalize();
            }
          }
        }, 1000);
        scheduleRectCache();

        const dispose = () => {
          disposed = true;
          swarmShutdown();
          observer.disconnect();
          body.removeEventListener('scroll', scheduleRectCache, true);
          body.removeEventListener('pointerdown', refreshCacheNow, true);
          body.removeEventListener('mousedown', refreshCacheNow, true);
          body.removeEventListener('keydown', refreshCacheNow, true);
          body.removeEventListener('wheel', refreshCacheNow, true);
          window.removeEventListener('keydown', onDiagKey, true);
          const diagNode = document.getElementById('dss-diag');
          if (diagNode !== null) diagNode.remove();
          window.clearInterval(idleCacheInterval);
          window.clearInterval(sweep);
          if (cacheTimer !== null) window.clearTimeout(cacheTimer);
          cacheTimer = null;
          for (const [el] of [...guardRecords]) releaseElement(el);
          guardRecords.clear();
          for (const record of [...exitFinishers]) record.finalize();
          exitFinishers.clear();
          for (const [node, state] of [...scrollGuards]) {
            if (state.pinnedX !== '' && node.style.overflowX === state.pinnedX) {
              node.style.overflowX = state.savedX;
            }
            if (state.pinnedY !== '' && node.style.overflowY === state.pinnedY) {
              node.style.overflowY = state.savedY;
            }
          }
          scrollGuards.clear();
        };
        globalThis.__dshStepSlideInDispose = dispose;
        return () => {
          if (globalThis.__dshStepSlideInDispose === dispose) {
            try {
              delete globalThis.__dshStepSlideInDispose;
            } catch {}
          }
          dispose();
        };
      }, 'dsh-step-slide-in: slide layer');
    }

    exports.name = 'dsh-step-slide-in';
    exports.inject = ['slots', 'locale'];
    exports.apply = apply;
    return module.exports;
  },
});
