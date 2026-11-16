import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import layout from './generated/layout.json';
import gl from './generated/gl.json';
import seqData from './generated/seq.json';
import {
  C,
  FONT,
  fontFaceCss,
  Grain,
  Vignette,
  PageBg,
  TextureCrop,
  Caption,
  Kicker,
  Wordmark,
  Dust,
  Sheen,
  Scrim,
  seg,
  easeOut,
  easeInOut,
} from './lib/ui';
import { clamp, lerp } from './lib/time';

const eob = (t: number) => {
  const c = 1.15;
  const u = t - 1;
  return 1 + (c + 1) * u * u * u + c * u * u;
};

const SHOTS = {
  open: { from: 0, dur: 168 },
  hero: { from: 168, dur: 200 },
  card1: { from: 368, dur: 90 },
  lyrics: { from: 458, dur: 144 },
  eq: { from: 602, dur: 138 },
  cross: { from: 740, dur: 132 },
  desktop: { from: 872, dur: 132 },
  redis: { from: 1004, dur: 114 },
  card2: { from: 1118, dur: 90 },
  outro: { from: 1208, dur: 232 },
};

const page = (name: string) => staticFile('pages/' + name + '.png');

const findEl = (pageName: string, cls: string, index = 0) => {
  const list = (layout.elements as any[]).filter((e) => e.page === pageName && e.class === cls);
  return list[index] || null;
};

const PlaySeq: React.FC<{
  name: string;
  frame: number;
  style?: React.CSSProperties;
  slow?: number;
}> = ({ name, frame, style, slow = 1 }) => {
  const data = (seqData as any)[name];
  if (!data || !data.times || data.times.length === 0) return null;
  const times = data.times as { t: number }[];
  const t = (frame / 60) / slow;
  let i = 0;
  for (let k = 0; k < times.length; k++) if (times[k].t <= t) i = k;
  const next = Math.min(i + 1, times.length - 1);
  const span = Math.max(0.001, times[next].t - times[i].t);
  const local = clamp((t - times[i].t) / span);
  const fade = clamp((local - 0.55) / 0.45);
  const file = (idx: number) => staticFile(data.dir + String(idx).padStart(4, '0') + '.jpg');
  return (
    <div style={{ position: 'absolute', ...style }}>
      <Img src={file(i)} style={{ position: 'absolute', width: '100%', height: '100%', objectFit: 'cover' }} />
      {next !== i && fade > 0 ? (
        <Img
          src={file(next)}
          style={{ position: 'absolute', width: '100%', height: '100%', objectFit: 'cover', opacity: fade }}
        />
      ) : null}
    </div>
  );
};

const Opening: React.FC = () => {
  const f = useCurrentFrame();
  const drift = Math.sin(f * 0.006) * 18;
  const beam = seg(f, 0, 90);
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg }}>
      <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
        <div
          style={{
            position: 'absolute',
            inset: -80,
            background:
              'radial-gradient(55% 70% at 30% 32%, rgba(48,84,170,0.42), rgba(6,8,14,0) 62%), radial-gradient(52% 62% at 74% 64%, rgba(112,70,190,0.25), rgba(6,8,14,0) 64%), radial-gradient(40% 50% at 52% 90%, rgba(244,201,160,0.06), rgba(6,8,14,0) 70%)',
            transform: 'translate(' + drift + 'px,' + -drift * 0.5 + 'px) scale(1.06)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: '42%',
            top: '-30%',
            width: '22%',
            height: '170%',
            background: 'linear-gradient(90deg, rgba(127,178,255,0), rgba(127,178,255,0.10), rgba(127,178,255,0))',
            transform: 'rotate(16deg) translateX(' + (-240 + beam * 520) + 'px)',
            filter: 'blur(6px)',
            opacity: 0.9,
          }}
        />
      </div>
      <Wordmark frame={f} start={26} size={118} />
      <div
        style={{
          position: 'absolute',
          top: 596,
          left: 0,
          right: 0,
          textAlign: 'center',
          fontFamily: FONT,
          fontSize: 30,
          letterSpacing: '0.46em',
          color: C.sub,
          opacity: easeOut(seg(f, 96, 124)) * 0.9,
        }}
      >
        WINDOWS · NETEASE CLOUDMUSIC
      </div>
      <div
        style={{
          position: 'absolute',
          top: 690,
          left: 0,
          right: 0,
          textAlign: 'center',
          fontFamily: FONT,
          fontSize: 24,
          letterSpacing: '0.3em',
          color: C.mute,
          opacity: easeOut(seg(f, 112, 140)),
        }}
      >
        A LISTENING WORKSTATION
      </div>
      <Dust frame={f} count={22} seed={11} />
      <Grain opacity={0.06} />
      <Vignette strength={0.55} />
    </AbsoluteFill>
  );
};

const Hero: React.FC = () => {
  const f = useCurrentFrame();
  const push = easeInOut(seg(f, 44, 156));
  const zoom = lerp(0.95, 1.5, push);
  const cx = lerp(960, 650, push);
  const cy = lerp(540, 460, push);
  const stops = [
    { t: 0, x: 118, y: 166 },
    { t: 46, x: 430, y: 310 },
    { t: 92, x: 610, y: 470 },
  ];
  let sx = stops[0].x;
  let sy = stops[0].y;
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    const t = easeInOut(seg(f, a.t, b.t));
    if (f <= b.t) {
      sx = lerp(a.x, b.x, t);
      sy = lerp(a.y, b.y, t);
      break;
    }
    sx = b.x;
    sy = b.y;
  }
  const pool = 460 + 170 * easeOut(seg(f, 88, 180));
  const mask =
    'radial-gradient(ellipse ' + pool + 'px ' + pool * 0.62 + 'px at ' + sx + 'px ' + sy + 'px, rgba(0,0,0,1) 0%, rgba(0,0,0,0.92) 38%, rgba(0,0,0,0.42) 62%, rgba(0,0,0,0) 78%)';
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, overflow: 'hidden' }}>
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: 1920,
          height: 1080,
          transformOrigin: '0 0',
          transform: 'translate(' + (960 - cx * zoom) + 'px,' + (540 - cy * zoom) + 'px) scale(' + zoom + ')',
        }}
      >
        <Img src={page('playing')} style={{ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, filter: 'brightness(0.52) saturate(0.85)' }} />
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: 1920,
            height: 1080,
            backgroundImage: 'url(' + page('playing') + ')',
            backgroundSize: '1920px 1080px',
            filter: 'brightness(1.02) saturate(1.04)',
            maskImage: mask,
            WebkitMaskImage: mask,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: 1920,
            height: 1080,
            background: 'radial-gradient(70% 60% at 50% 40%, rgba(22,38,80,0.18), rgba(3,5,10,0.55) 100%)',
            mixBlendMode: 'multiply',
          }}
        />
      </div>
      <Scrim opacity={0.62} height={360} />
      <Kicker text="NOW PLAYING" frame={f} from={20} />
      <Caption text="Wolves — Selena Gomez, Marshmello" frame={f} from={18} to={196} y={952} size={60} />
      <div
        style={{
          position: 'absolute',
          right: 110,
          top: 118,
          fontFamily: FONT,
          fontSize: 26,
          letterSpacing: '0.34em',
          color: C.sub,
          opacity: 0.7 * easeOut(seg(f, 60, 90)),
        }}
      >
        逐字歌词 · 频谱 · 音质
      </div>
      <Grain opacity={0.05} />
      <Vignette strength={0.6} />
    </AbsoluteFill>
  );
};

const CardLine: React.FC<{ text: string; index: number; frame: number; big?: boolean }> = ({ text, index, frame, big = false }) => {
  const start = 14 + index * 9;
  const t = easeOut(seg(frame, start, start + 24));
  return (
    <div
      style={{
        fontFamily: FONT,
        fontSize: big ? 96 : 78,
        fontWeight: 650,
        lineHeight: 1.16,
        letterSpacing: '0.01em',
        color: index === 0 ? C.ink : index === 1 ? 'rgba(226,236,255,0.86)' : 'rgba(200,214,245,0.66)',
        opacity: t,
        transform: 'translateY(' + Math.round((1 - t) * 42) + 'px)',
        filter: 'blur(' + ((1 - t) * 18) + 'px)',
      }}
    >
      {text}
    </div>
  );
};

const TextCard: React.FC<{ lines: string[]; kicker: string }> = ({ lines, kicker }) => {
  const f = useCurrentFrame();
  const rule = easeOut(seg(f, 8, 44));
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg }}>
      <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(60% 80% at 28% 38%, rgba(44,70,140,0.22), rgba(6,8,14,0) 62%)' }} />
      <div style={{ position: 'absolute', left: 180, top: 320, display: 'flex', flexDirection: 'column', gap: 26 }}>
        {lines.map((text, i) => (
          <CardLine key={i} text={text} index={i} frame={f} big={i === 0} />
        ))}
        <div style={{ marginTop: 34, width: 360 * rule, height: 2, background: 'linear-gradient(90deg, '+C.accent+', rgba(127,178,255,0))' }} />
      </div>
      <div
        style={{
          position: 'absolute',
          left: 184,
          top: 232,
          fontFamily: FONT,
          fontSize: 24,
          letterSpacing: '0.42em',
          color: C.accent,
          opacity: 0.8 * easeOut(seg(f, 0, 18)),
        }}
      >
        {kicker}
      </div>
      <Grain opacity={0.05} />
      <Vignette strength={0.62} />
      <Dust frame={f} count={16} seed={21} />
    </AbsoluteFill>
  );
};

const EQ_RECT = (gl as any).eqRect || { x: 355, y: 239, w: 1530, h: 200 };

const LyricsShot: React.FC = () => {
  const f = useCurrentFrame();
  const push = easeInOut(seg(f, 0, 144));
  const k = lerp(1.38, 1.5, push);
  const px = 340;
  const py = 520;
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, overflow: 'hidden' }}>
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: -8,
            width: 1920,
            height: 1101,
            transformOrigin: '0 0',
            transform: 'translate(' + (960 - px * k) + 'px,' + (540 - py * k) + 'px) scale(' + k + ')',
          }}
        >
          <PlaySeq name="lyrics" frame={f} style={{ left: 0, top: 0, width: 1920, height: 1101, filter: 'brightness(1.34) contrast(1.05)' }} />
        </div>
      </div>
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(90deg, rgba(4,6,12,0.55), rgba(4,6,12,0.05) 30%, rgba(4,6,12,0) 60%, rgba(4,6,12,0.45))' }} />
      <Scrim opacity={0.62} height={340} />
      <Kicker text="YRC LYRICS" frame={f} from={12} />
      <Caption text="逐字歌词 · 翻译同步" frame={f} from={10} to={140} />
      <Grain opacity={0.05} />
      <Vignette strength={0.55} />
    </AbsoluteFill>
  );
};

const EqShot: React.FC = () => {
  const f = useCurrentFrame();
  const push = easeInOut(seg(f, 0, 120));
  const rotY = lerp(8, 4, push);
  const rotX = lerp(3, 1.5, push);
  const scale = lerp(1.18, 1.32, push);
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', perspective: 1600 }}>
        <div
          style={{
            position: 'relative',
            width: 1920,
            height: 1080,
            transform: 'rotateY(' + rotY + 'deg) rotateX(' + rotX + 'deg) scale(' + scale + ')',
            transformOrigin: '50% 50%',
            borderRadius: 10,
            overflow: 'hidden',
            boxShadow: '0 60px 160px rgba(0,0,0,0.8)',
            background: '#07090f',
          }}
        >
          <Img src={page('settings_eq')} style={{ position: 'absolute', left: 0, top: -170, width: 1920, height: 1080, filter: 'brightness(0.85)' }} />
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: -170,
              width: 1920,
              height: 1080,
              backgroundImage: 'url(' + page('settings_eq') + ')',
              backgroundSize: '1920px 1080px',
              filter: 'brightness(1.45)',
              maskImage: 'radial-gradient(ellipse 760px 300px at 960px 260px, rgba(0,0,0,1) 35%, rgba(0,0,0,0) 75%)',
              WebkitMaskImage: 'radial-gradient(ellipse 760px 300px at 960px 260px, rgba(0,0,0,1) 35%, rgba(0,0,0,0) 75%)',
            }}
          />
          <Sheen frame={f} from={70} duration={30} />
          <div style={{ position: 'absolute', inset: 0, boxShadow: 'inset 0 0 140px 60px rgba(3,5,10,0.5)' }} />
          <div
            style={{
              position: 'absolute',
              top: '-20%',
              left: (-30 + easeInOut(seg(f, 74, 120)) * 150) + '%',
              width: '26%',
              height: '140%',
              background: 'linear-gradient(90deg, rgba(255,255,255,0), rgba(140,190,255,0.28), rgba(255,255,255,0))',
              transform: 'rotate(12deg)',
              mixBlendMode: 'screen',
            }}
          />
        </div>
      </div>
      <Scrim opacity={0.55} height={320} />
      <Kicker text="AUDIO ENGINE" frame={f} from={10} />
      <Caption text="EQ 曲线 · 响度归一化" frame={f} from={10} to={132} />
      <Grain opacity={0.045} />
      <Vignette strength={0.6} />
    </AbsoluteFill>
  );
};

const CrossShot: React.FC = () => {
  const f = useCurrentFrame();
  const draw = easeOut(seg(f, 8, 72));
  const reveal = easeInOut(seg(f, 52, 92));
  const dim = 1 - 0.62 * easeOut(seg(f, 52, 104));
  const pip = easeOut(seg(f, 86, 110));
  const line = 'M 96 866 H 372 Q 412 866 412 826 V 226 Q 412 186 452 186 H 1452 Q 1492 186 1492 226 V 826 Q 1492 866 1452 866 H 452';
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, overflow: 'hidden' }}>
      <Img src={page('playing')} style={{ position: 'absolute', width: 1920, height: 1080, opacity: dim }} />
      <div
        style={{
          position: 'absolute',
          left: 412,
          top: 186,
          width: 1080,
          height: 680,
          overflow: 'hidden',
          borderRadius: 22,
          clipPath: 'inset(0 ' + Math.round((1 - reveal) * 100) + '% 0 0 round 22px)',
          boxShadow: '0 30px 100px rgba(0,0,0,0.55)',
        }}
      >
        <div style={{ position: 'absolute', left: 0, top: 0, width: 1080, height: 680, overflow: 'hidden' }}>
          <div style={{ transform: 'scale(0.92)', transformOrigin: '0 0' }}>
            <TextureCrop page="playing_405599119" x={0} y={0} w={1174} h={739} />
          </div>
        </div>
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(90deg, rgba(3,5,10,0.35), rgba(3,5,10,0))' }} />
      </div>
      <svg width={1920} height={1080} style={{ position: 'absolute', left: 0, top: 0 }}>
        <path
          d={line}
          fill="none"
          stroke="rgba(143,192,255,0.95)"
          strokeWidth={6}
          strokeLinecap="square"
          pathLength={1}
          strokeDasharray={1}
          strokeDashoffset={1 - draw}
          style={{ filter: 'drop-shadow(0 0 18px rgba(127,178,255,0.55))' }}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          right: 108,
          bottom: 128,
          width: 460,
          height: 150,
          borderRadius: 12,
          overflow: 'hidden',
          boxShadow: '0 24px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(160,190,255,0.2)',
          opacity: pip,
          transform: 'translateY(' + Math.round((1 - pip) * 26) + 'px) rotate(-2deg)',
        }}
      >
        <div style={{ width: 1240, height: 404, transform: 'scale(0.371)', transformOrigin: '0 0' }}>
          <TextureCrop page="settings_crossfade" x={340} y={80} w={1240} h={404} />
        </div>
      </div>
      <Scrim opacity={0.55} height={320} />
      <Kicker text="SMART CROSSFADE" frame={f} from={10} />
      <Caption text="智能交叉淡化：BPM 与调性对齐，两首歌像一首" frame={f} from={12} to={128} size={58} />
      <Grain opacity={0.05} />
      <Vignette strength={0.58} />
    </AbsoluteFill>
  );
};

const QueueShot: React.FC = () => {
  const f = useCurrentFrame();
  const enter = easeOut(seg(f, 4, 40));
  const push = easeInOut(seg(f, 0, 110));
  const panelW = 760;
  const panelH = 950;
  const scale = lerp(1.0, 1.06, push);
  const tx = (1 - enter) * 320;
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, overflow: 'hidden' }}>
      <Img src={page('playing')} style={{ position: 'absolute', width: 1920, height: 1080, filter: 'blur(24px) brightness(0.22) saturate(0.8)', transform: 'scale(1.12)' }} />
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(110deg, rgba(4,7,14,0.85), rgba(4,7,14,0.45) 45%, rgba(4,7,14,0.75))' }} />
      <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', perspective: 1600 }}>
        <div
          style={{
            width: panelW,
            height: panelH,
            transform: 'translateX(' + tx + 'px) scale(' + scale + ') rotateY(-4deg) rotateX(2deg)',
            transformOrigin: '50% 50%',
            borderRadius: 22,
            overflow: 'hidden',
            background: '#0a0e18',
            boxShadow: '0 50px 140px rgba(0,0,0,0.7), 0 0 0 1px rgba(168,198,255,0.22), 0 0 90px rgba(90,140,255,0.18)',
          }}
        >
          <div style={{ width: 860, height: 1010, transform: 'scale(0.884)', transformOrigin: '0 0' }}>
            <TextureCrop page="playlist" x={1080} y={40} w={860} h={1010} />
          </div>
          <div style={{ position: 'absolute', inset: 0, boxShadow: 'inset 0 0 90px rgba(3,5,10,0.45)' }} />
        </div>
      </div>
      <Scrim opacity={0.6} height={340} />
      <Kicker text="PLAY QUEUE" frame={f} from={10} />
      <Caption text="播放队列：接下来放什么，你说了算" frame={f} from={10} to={128} size={58} />
      <Grain opacity={0.05} />
      <Vignette strength={0.6} />
    </AbsoluteFill>
  );
};

const RedisShot: React.FC = () => {
  const f = useCurrentFrame();
  const push = easeInOut(seg(f, 0, 88));
  const zoom = lerp(1.0, 1.42, push);
  const cy = lerp(540, 360, push);
  const cx = 960;
  const ring = easeOut(seg(f, 52, 92));
  const dateEl = findEl('rediscovery', 'CaptionLabel', 1) || { x: 1754, y: 218, w: 130, h: 58 };
  const rowEl = findEl('rediscovery', 'SubtitleLabel', 1) || { x: 344, y: 295, w: 772, h: 27 };
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, overflow: 'hidden' }}>
      <div
        style={{
          position: 'absolute',
          width: 1920,
          height: 1080,
          transformOrigin: '0 0',
          transform: 'translate(' + (960 - cx * zoom) + 'px,' + (540 - cy * zoom) + 'px) scale(' + zoom + ')',
        }}
      >
        <Img src={page('rediscovery')} style={{ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, filter: 'brightness(0.6) saturate(0.95)' }} />
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: 1920,
            height: 1080,
            backgroundImage: 'url(' + page('rediscovery') + ')',
            backgroundSize: '1920px 1080px',
            maskImage: 'radial-gradient(ellipse 760px 300px at ' + (rowEl.x + rowEl.w / 2) + 'px ' + (dateEl.y + 40) + 'px, rgba(0,0,0,1) 30%, rgba(0,0,0,0) 72%)',
            WebkitMaskImage: 'radial-gradient(ellipse 760px 300px at ' + (rowEl.x + rowEl.w / 2) + 'px ' + (dateEl.y + 40) + 'px, rgba(0,0,0,1) 30%, rgba(0,0,0,0) 72%)',
            filter: 'brightness(1.25)',
          }}
        />
        <svg width={1920} height={1080} style={{ position: 'absolute', left: 0, top: 0 }}>
          <rect
            x={dateEl.x - 10}
            y={dateEl.y - 10}
            width={dateEl.w + 20}
            height={dateEl.h + 20}
            rx={12}
            fill="none"
            stroke="rgba(143,192,255,0.9)"
            strokeWidth={2.4}
            pathLength={1}
            strokeDasharray={1}
            strokeDashoffset={1 - ring}
            style={{ filter: 'drop-shadow(0 0 14px rgba(127,178,255,0.5))' }}
          />
        </svg>
      </div>
      <Scrim opacity={0.6} height={340} />
      <Kicker text="REDISCOVER" frame={f} from={8} />
      <Caption text="重新发现：翻出你三个月没听的那首" frame={f} from={8} to={110} size={58} />
      <Grain opacity={0.05} />
      <Vignette strength={0.6} />
    </AbsoluteFill>
  );
};

type Card = {
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
  rot: number;
  delay: number;
  dir: [number, number];
  node: React.ReactNode;
};

const Outro: React.FC = () => {
  const f = useCurrentFrame();
  const crane = easeInOut(seg(f, 0, 44));
  const settle = easeOut(seg(f, 160, 210));
  const stageRot = lerp(4, 0, crane);
  const stageScale = lerp(1.06, 1.0, crane) + 0.025 * easeOut(seg(f, 40, 170));
  const cards: Card[] = [
    {
      key: 'cover',
      x: 236,
      y: 168,
      w: 300,
      h: 300,
      rot: -6,
      delay: 4,
      dir: [-520, -280],
      node: (
        <div style={{ width: 300, height: 300 }}>
          <div style={{ transform: 'scale(1.5)', transformOrigin: '0 0' }}>
            <TextureCrop page="playing" x={18} y={66} w={200} h={200} />
          </div>
        </div>
      ),
    },
    {
      key: 'lyrics',
      x: 1140,
      y: 150,
      w: 525,
      h: 210,
      rot: 4,
      delay: 10,
      dir: [520, -260],
      node: (
        <div style={{ width: 525, height: 210 }}>
          <div style={{ transform: 'scale(0.53)', transformOrigin: '0 0' }}>
            <TextureCrop page="playing" x={233} y={300} w={980} h={260} />
          </div>
        </div>
      ),
    },
    {
      key: 'eq',
      x: 168,
      y: 636,
      w: 430,
      h: (430 / 1582) * 420,
      rot: 3,
      delay: 16,
      dir: [-560, 240],
      node: (
        <div style={{ width: 430, height: (430 / 1582) * 420 }}>
          <div style={{ transform: 'scale(' + 430 / 1582 + ')', transformOrigin: '0 0' }}>
            <TextureCrop page="settings_eq" x={329} y={20} w={1582} h={420} />
          </div>
        </div>
      ),
    },
    {
      key: 'cross',
      x: 1330,
      y: 520,
      w: 434,
      h: 119,
      rot: -4,
      delay: 22,
      dir: [560, 260],
      node: (
        <div style={{ width: 434, height: 119 }}>
          <div style={{ transform: 'scale(0.35)', transformOrigin: '0 0' }}>
            <TextureCrop page="settings_crossfade" x={340} y={92} w={1240} h={340} />
          </div>
        </div>
      ),
    },
    {
      key: 'desktop',
      x: 690,
      y: 78,
      w: 430,
      h: 190,
      rot: 0,
      delay: 28,
      dir: [0, -260],
      node: (
        <div style={{ width: 430, height: 190 }}>
          <div style={{ transform: 'scale(0.5)', transformOrigin: '0 0' }}>
            <TextureCrop page="playlist" x={1108} y={57} w={860} h={380} />
          </div>
        </div>
      ),
    },
    {
      key: 'rediscover',
      x: 764,
      y: 902,
      w: 412,
      h: 56,
      rot: 2,
      delay: 34,
      dir: [0, 260],
      node: (
        <div style={{ width: 412, height: 56 }}>
          <div style={{ transform: 'scale(0.4)', transformOrigin: '0 0' }}>
            <TextureCrop page="rediscovery" x={240} y={212} w={1029} h={140} />
          </div>
        </div>
      ),
    },
  ];
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', inset: -60, background: 'radial-gradient(60% 70% at 50% 30%, rgba(44,78,168,0.28), rgba(6,8,14,0) 62%), radial-gradient(45% 60% at 52% 86%, rgba(244,201,160,0.08), rgba(6,8,14,0) 70%)' }} />
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '42%',
          width: 1400,
          height: 1400,
          marginLeft: -700,
          marginTop: -700,
          background: 'conic-gradient(from 0deg, rgba(127,178,255,0.0), rgba(127,178,255,0.06), rgba(127,178,255,0.0), rgba(244,201,160,0.04), rgba(127,178,255,0.0))',
          transform: 'rotate(' + f * 0.12 + 'deg)',
          filter: 'blur(2px)',
          opacity: 0.9,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: 1920,
          height: 1080,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          perspective: 1600,
        }}
      >
        <div
          style={{
            position: 'relative',
            width: 1920,
            height: 1080,
            transform: 'rotateX(' + stageRot + 'deg) scale(' + stageScale + ')',
            transformOrigin: '50% 50%',
          }}
        >
          {cards.map((c) => {
            const p = clamp(seg(f, c.delay, c.delay + 24));
            const e = eob(p);
            const op = easeOut(seg(f, c.delay, c.delay + 12));
            const dx = c.dir[0] * (1 - e);
            const dy = c.dir[1] * (1 - e);
            const s = lerp(0.9, 1, e);
            return (
              <div
                key={c.key}
                style={{
                  position: 'absolute',
                  left: c.x,
                  top: c.y,
                  width: c.w,
                  height: c.h,
                  opacity: op,
                  transform: 'translate(' + dx + 'px,' + dy + 'px) rotate(' + (c.rot + (1 - e) * c.rot * 2) + 'deg) scale(' + s + ')',
                  borderRadius: 16,
                  overflow: 'hidden',
                  boxShadow: '0 30px 70px rgba(0,0,0,0.55), 0 0 0 1px rgba(168,198,255,0.18)',
                  background: '#0a0d15',
                }}
              >
                {c.node}
                <div style={{ position: 'absolute', inset: 0, boxShadow: 'inset 0 0 40px rgba(3,5,10,0.35)' }} />
              </div>
            );
          })}
        </div>
      </div>
      <Wordmark frame={f} start={84} size={96} letterpress showRule />
      <div
        style={{
          position: 'absolute',
          top: 626,
          left: 0,
          right: 0,
          textAlign: 'center',
          fontFamily: FONT,
          fontSize: 36,
          fontWeight: 500,
          letterSpacing: '0.12em',
          color: 'rgba(232,240,255,0.86)',
          opacity: easeOut(seg(f, 140, 166)) * (1 - 0.0 * settle),
        }}
      >
        为 Windows 做的网易云客户端
      </div>
      <div
        style={{
          position: 'absolute',
          top: 690,
          left: 0,
          right: 0,
          textAlign: 'center',
          fontFamily: FONT,
          fontSize: 22,
          letterSpacing: '0.34em',
          color: C.mute,
          opacity: easeOut(seg(f, 148, 170)),
        }}
      >
        SOUTH SIDE MUSIC · MADE FOR THE HOURS NO ONE SEES
      </div>
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          height: 4,
          background: 'linear-gradient(90deg, rgba(127,178,255,0), rgba(127,178,255,0.85), rgba(244,201,160,0.6), rgba(127,178,255,0))',
          opacity: 0.7 * easeOut(seg(f, 150, 190)),
        }}
      />
      <Dust frame={f} count={30} seed={5} />
      <Grain opacity={0.05} />
      <Vignette strength={0.55} />
    </AbsoluteFill>
  );
};

type Sfx = { from: number; src: string; volume: number; durationInFrames?: number };

const SFX: Sfx[] = [
  { from: 0, src: 'audio/sfx/impact-deep-hit.mp3', volume: 0.38, durationInFrames: 120 },
  { from: 209, src: 'audio/sfx/whoosh-camera-pass.mp3', volume: 0.4, durationInFrames: 64 },
  { from: 328, src: 'audio/sfx/sparkle-glass-point.mp3', volume: 0.27, durationInFrames: 20 },
  { from: 471, src: 'audio/sfx/riser-dark-swell.mp3', volume: 0.45, durationInFrames: 180 },
  { from: 661, src: 'audio/sfx/switch-light-clunk.mp3', volume: 0.36, durationInFrames: 10 },
  { from: 751, src: 'audio/sfx/transition-soft-swish.mp3', volume: 1.15, durationInFrames: 76 },
  { from: 812, src: 'audio/sfx/paper-card-deal.mp3', volume: 0.4, durationInFrames: 47 },
  { from: 820, src: 'audio/sfx/air-suck-vacuum.mp3', volume: 0.5, durationInFrames: 150 },
  { from: 929, src: 'audio/sfx/impact-tight-slam.mp3', volume: 0.4, durationInFrames: 105 },
  { from: 1098, src: 'audio/sfx/paper-card-deal.mp3', volume: 0.35, durationInFrames: 47 },
  { from: 1215, src: 'audio/sfx/sparkle-glass-point.mp3', volume: 0.27, durationInFrames: 20 },
  { from: 1269, src: 'audio/sfx/impact-deep-hit.mp3', volume: 0.4, durationInFrames: 120 },
];

export const SouthsidePromo: React.FC<{ bgm?: boolean }> = ({ bgm = true }) => {
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg }}>
      <style>{fontFaceCss}</style>
      {bgm ? <Audio src={staticFile('audio/bgm/cat-walk-cut.wav')} volume={0.74} /> : null}
      <Sequence from={SHOTS.open.from} durationInFrames={SHOTS.open.dur}>
        <Opening />
      </Sequence>
      <Sequence from={SHOTS.hero.from} durationInFrames={SHOTS.hero.dur}>
        <Hero />
      </Sequence>
      <Sequence from={SHOTS.card1.from} durationInFrames={SHOTS.card1.dur}>
        <TextCard lines={['逐字歌词', '每个字的亮起', '都落在鼓点上']} kicker="LIVE LYRICS" />
      </Sequence>
      <Sequence from={SHOTS.lyrics.from} durationInFrames={SHOTS.lyrics.dur}>
        <LyricsShot />
      </Sequence>
      <Sequence from={SHOTS.eq.from} durationInFrames={SHOTS.eq.dur}>
        <EqShot />
      </Sequence>
      <Sequence from={SHOTS.cross.from} durationInFrames={SHOTS.cross.dur}>
        <CrossShot />
      </Sequence>
      <Sequence from={SHOTS.desktop.from} durationInFrames={SHOTS.desktop.dur}>
        <QueueShot />
      </Sequence>
      <Sequence from={SHOTS.redis.from} durationInFrames={SHOTS.redis.dur}>
        <RedisShot />
      </Sequence>
      <Sequence from={SHOTS.card2.from} durationInFrames={SHOTS.card2.dur}>
        <TextCard lines={['系统媒体键', '键盘上那颗播放键', '直接控制它']} kicker="WINDOWS INTEGRATION" />
      </Sequence>
      <Sequence from={SHOTS.outro.from} durationInFrames={SHOTS.outro.dur}>
        <Outro />
      </Sequence>
      {SFX.map((s, i) => (
        <Sequence key={i} from={s.from} durationInFrames={s.durationInFrames ?? 180}>
          <Audio src={staticFile(s.src)} volume={s.volume} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
