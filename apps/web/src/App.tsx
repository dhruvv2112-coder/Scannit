import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, useGLTF, Grid } from '@react-three/drei';
import { Suspense, useEffect, useRef, useState } from 'react';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { checkReferenceScale, type ScaleCheck } from './scale';

export const SAMPLE_URL = `${import.meta.env.BASE_URL}samples/sample-site.glb`;
const HOME_POS: [number, number, number] = [18, 14, 22];
const HOME_TARGET: [number, number, number] = [0, 2, 0];

interface Stats { fps: number; tris: number; calls: number; cam: number[]; dist: number }

declare global {
  interface Window { __viewer?: { ready: boolean; stats: Stats; scale: ScaleCheck | null; resets: number } }
}

function Site({ onScale }: { onScale: (s: ScaleCheck) => void }) {
  const { scene } = useGLTF(SAMPLE_URL);
  useEffect(() => onScale(checkReferenceScale(scene)), [scene, onScale]);
  return <primitive object={scene} />;
}

function Probe({ controls, onStats }: { controls: React.RefObject<OrbitControlsImpl | null>; onStats: (s: Stats) => void }) {
  const { gl, camera } = useThree();
  const acc = useRef({ t: 0, n: 0 });
  useFrame((_, dt) => {
    acc.current.t += dt; acc.current.n++;
    if (acc.current.t < 0.5) return;
    const target = controls.current?.target;
    onStats({
      fps: Math.round(acc.current.n / acc.current.t),
      tris: gl.info.render.triangles,
      calls: gl.info.render.calls,
      cam: camera.position.toArray().map((v) => +v.toFixed(2)),
      dist: target ? +camera.position.distanceTo(target).toFixed(2) : 0,
    });
    acc.current = { t: 0, n: 0 };
  });
  return null;
}

export default function App() {
  const controls = useRef<OrbitControlsImpl | null>(null);
  const [stats, setStats] = useState<Stats>({ fps: 0, tris: 0, calls: 0, cam: HOME_POS, dist: 0 });
  const [scale, setScale] = useState<ScaleCheck | null>(null);
  const resets = useRef(0);

  const resetCamera = () => {
    const c = controls.current; if (!c) return;
    c.object.position.set(...HOME_POS); c.target.set(...HOME_TARGET); c.update();
    resets.current++;
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const c = controls.current; if (!c) return;
      if (e.key === 'r' || e.key === 'R') resetCamera();
      if (e.key === '+' || e.key === '=') { c.dollyIn?.(1.2); c.update(); }
      if (e.key === '-') { c.dollyOut?.(1.2); c.update(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    window.__viewer = { ready: scale !== null, stats, scale, resets: resets.current };
  }, [stats, scale]);

  return (
    <div className="root">
      <Canvas camera={{ position: HOME_POS, fov: 50, near: 0.05, far: 5000 }} gl={{ antialias: true }} dpr={[1, 2]}>
        <color attach="background" args={['#14171c']} />
        <hemisphereLight args={['#dfe8f5', '#3a3428', 0.9]} />
        <directionalLight position={[30, 40, 20]} intensity={2.2} />
        <Grid args={[80, 80]} cellSize={1} sectionSize={10} infiniteGrid fadeDistance={120} cellColor="#2a3038" sectionColor="#47505c" position={[0, 0.001, 0]} />
        <Suspense fallback={null}><Site onScale={setScale} /></Suspense>
        <OrbitControls ref={controls} target={HOME_TARGET} makeDefault enableDamping={false} maxPolarAngle={Math.PI * 0.495} />
        <Probe controls={controls} onStats={setStats} />
      </Canvas>

      <aside className="hud" aria-live="polite" data-testid="hud">
        <h1>XR viewer · sample site</h1>
        <dl>
          <dt>Units</dt><dd>meters · +Y up</dd>
          <dt>Camera</dt><dd data-testid="cam">{stats.cam.join(', ')} m</dd>
          <dt>To target</dt><dd>{stats.dist} m</dd>
          <dt>FPS</dt><dd>{stats.fps}</dd>
          <dt>Triangles</dt><dd>{stats.tris.toLocaleString()}</dd>
          <dt>Draw calls</dt><dd>{stats.calls}</dd>
          <dt>1 m reference</dt>
          <dd data-testid="scale" className={scale?.ok ? 'ok' : 'bad'}>
            {scale ? (scale.found ? `${scale.measured_m!.toFixed(3)} m ${scale.ok ? '✓' : '✗'}` : 'not found') : 'loading…'}
          </dd>
        </dl>
        <p className="hint">Drag to orbit · right-drag to pan · wheel or +/- to zoom · <kbd>R</kbd> reset</p>
        <button type="button" onClick={resetCamera}>Reset camera</button>
        <p className="note">Synthetic mock scene. The reference check confirms the viewer keeps scale, not reconstruction accuracy.</p>
      </aside>
    </div>
  );
}
