import { AbsoluteFill, Audio, staticFile } from 'remotion';

export const Probe = () => (
  <AbsoluteFill style={{ backgroundColor: '#000' }}>
    <Audio src={staticFile('audio/sfx/impact-deep-hit.mp3')} volume={1} />
  </AbsoluteFill>
);
