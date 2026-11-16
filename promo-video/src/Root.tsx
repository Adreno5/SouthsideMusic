import { Composition } from 'remotion';
import { SouthsidePromo } from './SouthsidePromo';
import { Probe } from './Probe';

export const RemotionRoot = () => (
  <>
    <Composition
      id="SouthsidePromo"
      component={SouthsidePromo}
      durationInFrames={1440}
      fps={60}
      width={1920}
      height={1080}
      defaultProps={{ bgm: true }}
    />
    <Composition id="Probe" component={Probe} durationInFrames={60} fps={60} width={320} height={180} />
  </>
);
