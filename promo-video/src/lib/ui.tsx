import React from 'react';
import { AbsoluteFill, Img, staticFile } from 'remotion';
import { clamp, mulberry32 } from './time';

export const C = {
  bg: '#070911',
  bg2: '#0b1020',
  ink: '#f2f6ff',
  sub: 'rgba(226,234,255,0.62)',
  mute: 'rgba(200,212,240,0.38)',
  accent: '#7fb2ff',
  accentDeep: '#2f5fd0',
  warm: '#f4c9a0',
  line: 'rgba(190,210,255,0.22)',
};

export const FONT = '"HarmonyOS SC", "HarmonyOS Sans SC", "Microsoft YaHei UI", sans-serif';

const fontUrl = staticFile('fonts/HARMONYOS_SANS_SC_REGULAR.ttf');
export const fontFaceCss = '@font-face { font-family: "HarmonyOS SC"; src: url("' + fontUrl + '") format("truetype"); }';

export const seg = (frame: number, from: number, to: number) =>
  clamp((frame - from) / Math.max(1e-6, to - from));

export const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);
export const easeInOut = (t: number) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

export const Grain: React.FC<{ opacity?: number; seed?: number }> = ({ opacity = 0.055, seed = 7 }) => (
  <AbsoluteFill
    style={{
      opacity,
      pointerEvents: 'none',
      backgroundImage: 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'240\' height=\'240\'><filter id=\'n\'><feTurbulence type=\'fractalNoise\' baseFrequency=\'0.9\' numOctaves=\'2\' stitchTiles=\'stitch\'/><feColorMatrix type=\'saturate\' values=\'0\'/></filter><rect width=\'240\' height=\'240\' filter=\'url(%23n)\' opacity=\'0.9\'/></svg>")',
      backgroundSize: '240px 240px',
      mixBlendMode: 'overlay',
      transform: 'translate(' + ((seed % 7) * 3) + 'px, ' + ((seed % 5) * 4) + 'px)',
    }}
  />
);

export const Vignette: React.FC<{ strength?: number }> = ({ strength = 0.55 }) => (
  <AbsoluteFill
    style={{
      pointerEvents: 'none',
      background: 'radial-gradient(120% 90% at 50% 42%, rgba(0,0,0,0) 42%, rgba(0,0,0,' + strength + ') 100%)',
    }}
  />
);

export const PageBg: React.FC<{ page: string; bright?: number; style?: React.CSSProperties }> = ({ page, bright = 1, style }) => (
  <Img
    src={staticFile('pages/' + page + '.png')}
    style={{ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, filter: bright === 1 ? undefined : 'brightness(' + bright + ')', ...style }}
  />
);

export const TextureCrop: React.FC<{ page: string; x: number; y: number; w: number; h: number; style?: React.CSSProperties }> = ({ page, x, y, w, h, style }) => (
  <div
    style={{
      width: w,
      height: h,
      backgroundImage: 'url(' + staticFile('pages/' + page + '.png') + ')',
      backgroundSize: '1920px 1080px',
      backgroundPosition: (-x) + 'px ' + (-y) + 'px',
      backgroundRepeat: 'no-repeat',
      ...style,
    }}
  />
);

export const Scrim: React.FC<{ opacity?: number; height?: number }> = ({ opacity = 0.55, height = 300 }) => (
  <div
    style={{
      position: 'absolute',
      left: 0,
      right: 0,
      bottom: 0,
      height,
      background: 'linear-gradient(to top, rgba(3,5,10,' + opacity + ') 0%, rgba(3,5,10,0) 100%)',
      pointerEvents: 'none',
    }}
  />
);

export const Caption: React.FC<{ text: string; frame: number; from: number; to: number; x?: number; y?: number; size?: number; align?: 'left' | 'center'; width?: number }> = ({ text, frame, from, to, x = 96, y = 958, size = 60, align = 'left', width = 1500 }) => {
  const inT = easeOut(seg(frame, from, from + 16));
  const outT = easeOut(seg(frame, to - 12, to));
  const opacity = inT * (1 - outT);
  const ty = (1 - inT) * 22 - outT * 12;
  return (
    <div
      style={{
        position: 'absolute',
        left: align === 'center' ? 0 : x,
        top: y,
        width: align === 'center' ? '100%' : width,
        textAlign: align,
        opacity,
        transform: 'translateY(' + Math.round(ty) + 'px)',
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 600,
        letterSpacing: '0.01em',
        color: C.ink,
        textShadow: '0 2px 18px rgba(0,0,0,0.65)',
        lineHeight: 1.25,
      }}
    >
      {text}
    </div>
  );
};

export const Kicker: React.FC<{ text: string; frame: number; from: number; x?: number; y?: number; color?: string }> = ({ text, frame, from, x = 96, y = 892, color = C.accent }) => {
  const t = easeOut(seg(frame, from, from + 14));
  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        fontFamily: FONT,
        fontSize: 28,
        fontWeight: 600,
        letterSpacing: '0.28em',
        color,
        opacity: 0.9 * t,
        transform: 'translateY(' + Math.round((1 - t) * 10) + 'px)',
      }}
    >
      {text}
    </div>
  );
};

export const Wordmark: React.FC<{ frame: number; start: number; size?: number; letterpress?: boolean; showRule?: boolean }> = ({ frame, start, size = 112, letterpress = false, showRule = true }) => {
  const letters = 'SouthsideMusic'.split('');
  const rule = easeOut(seg(frame, start + 58, start + 100));
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, top: 424, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <div style={{ display: 'flex', justifyContent: 'center', fontFamily: FONT, fontSize: size, fontWeight: 700, letterSpacing: '0.005em', color: C.ink, lineHeight: 1 }}>
        {letters.map((ch, i) => {
          const t = easeOut(seg(frame, start + 2 + i * 2.2, start + 30 + i * 2.2));
          return (
            <span
              key={i}
              style={{
                display: 'inline-block',
                opacity: t,
                transform: 'translateY(' + Math.round((1 - t) * 26) + 'px)',
                filter: 'blur(' + ((1 - t) * 16) + 'px)',
                textShadow: letterpress ? '0 2px 0 rgba(0,0,0,0.55), 0 10px 34px rgba(80,130,255,0.18)' : '0 6px 40px rgba(90,140,255,0.22)',
              }}
            >
              {ch}
            </span>
          );
        })}
      </div>
      {showRule ? (
        <div style={{ marginTop: 34, width: 720 * rule, height: 1, background: 'linear-gradient(90deg, rgba(127,178,255,0), rgba(127,178,255,0.9), rgba(127,178,255,0))' }} />
      ) : null}
    </div>
  );
};

export const Dust: React.FC<{ frame: number; count?: number; seed?: number }> = ({ frame, count = 26, seed = 3 }) => {
  const rand = mulberry32(seed);
  const items = React.useMemo(
    () =>
      Array.from({ length: count }, () => ({
        x: rand() * 1920,
        y: rand() * 1080,
        r: 0.8 + rand() * 2.2,
        s: 0.2 + rand() * 0.8,
        p: rand() * 6.28,
        o: 0.06 + rand() * 0.22,
      })),
    [count, seed]
  );
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      {items.map((d, i) => {
        const y = (d.y - frame * d.s + 1080 * 4) % 1080;
        const x = d.x + Math.sin(frame * 0.01 + d.p) * 12;
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x,
              top: y,
              width: d.r,
              height: d.r,
              borderRadius: '50%',
              background: 'rgba(190,215,255,' + d.o + ')',
              filter: 'blur(0.6px)',
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

export const Sheen: React.FC<{ frame: number; from: number; duration?: number; angle?: number }> = ({ frame, from, duration = 24, angle = 18 }) => {
  const t = seg(frame, from, from + duration);
  if (t <= 0 || t >= 1) return null;
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      <div
        style={{
          position: 'absolute',
          top: '-30%',
          left: (-40 + t * 180) + '%',
          width: '28%',
          height: '160%',
          background: 'linear-gradient(90deg, rgba(255,255,255,0), rgba(180,215,255,0.35), rgba(255,255,255,0))',
          transform: 'rotate(' + angle + 'deg)',
          mixBlendMode: 'screen',
        }}
      />
    </AbsoluteFill>
  );
};