import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Grid, Line, OrbitControls } from '@react-three/drei';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box3, BoxHelper, Group, Matrix4, PerspectiveCamera, Vector3, type Object3D } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { parseManifest, resolveArtifactUrl, type ArtifactManifest } from './manifest';
import { ArtifactError, fmtBytes, loadVerifiedGlb } from './loader';
import { pollUntilTerminal, sourceFromLocation, type DataSource, type SceneEntry } from './source';
import { matrixFromRowMajor, toFrame } from './frames';
import { distanceM, fmtM, fmtVec, makePoint, snapToFaceVertex, type PickedPoint } from './measure';
import { inspect, type IntegrityReport } from './integrity';
import { checkReferenceScale } from './scale';

type Phase =
  | { k: 'scenes-loading' } | { k: 'scenes-error'; msg: string } | { k: 'no-scenes' }
  | { k: 'idle' } | { k: 'processing'; entry: SceneEntry; timedOut?: boolean } | { k: 'job-failed'; entry: SceneEntry }
  | { k: 'manifest-loading' } | { k: 'manifest-error'; msg: string[] }
  | { k: 'artifact-loading'; loaded: number; total: number | null } | { k: 'artifact-error'; kind: string; msg: string }
  | { k: 'ready' };

interface Loaded { manifest: ArtifactManifest; manifestUrl: string; artifactUrl: string; sha256: string; bytes: number; root: Group; report: IntegrityReport; warnings: string[]; toTarget: Matrix4 }
interface Stats { fps: number; tris: number; calls: number; camTarget: string }

declare global {
  interface Window {
    __viewer?: {
      phase: string; ready: boolean; error: string | null; stats: Stats; loaded: { artifact_id: string; sha256: string; scale_status: string; issues: string[]; refOk: boolean | null } | null;
      measurement: { points: number[][]; distance_m: number | null; snapped: boolean[] }; selected: string | null; mode: string; resets: number;
      project: (viewerXYZ: number[]) => { x: number; y: number } | null; refEndpointsViewer: number[][];
      /** Test/debug hook: frame the camera on a viewer-space box (camera only). */
      frameOn: (min: number[], max: number[], viewDir?: number[]) => void;
    };
  }
}

const q = new URLSearchParams(location.search);
const POLL_MS = Number(q.get('poll') ?? 5000);
const POLL_MAX = Number(q.get('pollMax') ?? 120);

function CameraRig({ controls, frameKey, box, viewDir, onProject, fly }: { controls: React.RefObject<OrbitControlsImpl | null>; frameKey: number; box: Box3 | null; viewDir: Vector3 | null; onProject: (f: (p: number[]) => { x: number; y: number } | null) => void; fly: boolean }) {
  const { camera, gl, size } = useThree();
  const keys = useRef(new Set<string>());
  useEffect(() => {
    onProject((p) => {
      const v = new Vector3(p[0], p[1], p[2]).project(camera);
      if (v.z > 1) return null;
      const r = gl.domElement.getBoundingClientRect();
      return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
    });
  }, [camera, gl, size, onProject]);
  // Frame camera on the geometry: moves the camera only, never the scene.
  useEffect(() => {
    const c = controls.current; if (!c || !box || box.isEmpty()) return;
    const cam = camera as PerspectiveCamera;
    const center = box.getCenter(new Vector3()), r = Math.max(box.getSize(new Vector3()).length() / 2, 0.5);
    const dist = r / Math.sin((cam.fov * Math.PI) / 360) * 1.1;
    cam.position.copy(center).add((viewDir ?? new Vector3(0.6, 0.45, 0.65)).clone().normalize().multiplyScalar(dist));
    cam.near = Math.max(r / 2000, 0.01); cam.far = Math.max(r * 200, 1000); cam.updateProjectionMatrix();
    c.target.copy(center); c.update();
  }, [frameKey, box, viewDir, camera, controls]);
  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (!(e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement)) keys.current.add(e.key.toLowerCase()); };
    const up = (e: KeyboardEvent) => keys.current.delete(e.key.toLowerCase());
    const blur = () => keys.current.clear();
    window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', blur);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur); };
  }, []);
  useFrame((_, dt) => {
    const c = controls.current; if (!fly || !c) return;
    const k = keys.current; if (!k.size) return;
    const r = box && !box.isEmpty() ? box.getSize(new Vector3()).length() / 2 : 20;
    const speed = Math.min(Math.max(r / 8, 1), 60) * (k.has('shift') ? 4 : 1) * Math.min(dt, 0.1);
    const fwd = new Vector3(); camera.getWorldDirection(fwd); fwd.y = 0; fwd.normalize();
    const right = new Vector3().crossVectors(fwd, camera.up).normalize();
    const mv = new Vector3();
    if (k.has('w')) mv.add(fwd); if (k.has('s')) mv.sub(fwd);
    if (k.has('d')) mv.add(right); if (k.has('a')) mv.sub(right);
    if (k.has('e')) mv.y += 1; if (k.has('q')) mv.y -= 1;
    if (!mv.lengthSq()) return;
    mv.normalize().multiplyScalar(speed);
    camera.position.add(mv); c.target.add(mv); c.update();
  });
  return null;
}

function Probe({ controls, toTarget, onStats }: { controls: React.RefObject<OrbitControlsImpl | null>; toTarget: Matrix4 | null; onStats: (s: Stats) => void }) {
  const { gl } = useThree();
  const acc = useRef({ t: 0, n: 0 });
  useFrame((_, dt) => {
    acc.current.t += dt; acc.current.n++;
    if (acc.current.t < 0.5) return;
    const t = controls.current?.target;
    onStats({ fps: Math.round(acc.current.n / acc.current.t), tris: gl.info.render.triangles, calls: gl.info.render.calls, camTarget: t && toTarget ? fmtVec(toFrame(t, toTarget)) : '—' });
    acc.current = { t: 0, n: 0 };
  });
  return null;
}

export default function App() {
  const source = useMemo<DataSource | Error>(() => { try { return sourceFromLocation(location, import.meta.env as Record<string, string>); } catch (e) { return e as Error; } }, []);
  const [phase, setPhase] = useState<Phase>({ k: 'scenes-loading' });
  const [scenes, setScenes] = useState<SceneEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [stats, setStats] = useState<Stats>({ fps: 0, tris: 0, calls: 0, camTarget: '—' });
  const [mode, setMode] = useState<'select' | 'measure'>('select');
  const [snap, setSnap] = useState(true);
  const [fly, setFly] = useState(false);
  const [points, setPoints] = useState<PickedPoint[]>([]);
  const [selected, setSelected] = useState<Object3D | null>(null);
  const [frameKey, setFrameKey] = useState(0);
  const [focusBox, setFocusBox] = useState<Box3 | null>(null);
  const [viewDir, setViewDir] = useState<Vector3 | null>(null);
  const controls = useRef<OrbitControlsImpl | null>(null);
  const loadAbort = useRef<AbortController | null>(null);
  const projectRef = useRef<(p: number[]) => { x: number; y: number } | null>(() => null);
  const resets = useRef(0);

  // 1. Scene list.
  const loadScenes = useCallback(async () => {
    if (source instanceof Error) { setPhase({ k: 'scenes-error', msg: source.message }); return; }
    setPhase({ k: 'scenes-loading' });
    try {
      const list = await source.listScenes();
      setScenes(list);
      if (!list.length) { setPhase({ k: 'no-scenes' }); return; }
      setPhase({ k: 'idle' });
      setSelectedId((cur) => cur ?? q.get('scene') ?? list[0].id);
    } catch (e) { setPhase({ k: 'scenes-error', msg: (e as Error).message }); }
  }, [source]);
  useEffect(() => { void loadScenes(); }, [loadScenes]);

  // 2. Selected scene -> (poll) -> manifest -> verified GLB -> parsed scene. Cancelled on change/unmount.
  useEffect(() => {
    if (!selectedId || source instanceof Error) return;
    const entry0 = scenes.find((s) => s.id === selectedId); if (!entry0) return;
    loadAbort.current?.abort();
    const ac = new AbortController(); loadAbort.current = ac;
    setLoaded(null); setPoints([]); setSelected(null);
    (async () => {
      let entry = entry0;
      if (entry.job && !entry.manifestUrl) {
        if (entry.job.status === 'FAILED' || entry.job.status === 'CANCELLED' || entry.job.status === 'NEEDS_REFERENCE') { setPhase({ k: 'job-failed', entry }); return; }
        setPhase({ k: 'processing', entry });
        const r = await pollUntilTerminal(source, entry.id, { intervalMs: POLL_MS, maxAttempts: POLL_MAX, signal: ac.signal, onUpdate: (e) => !ac.signal.aborted && setPhase({ k: 'processing', entry: e }) });
        if (ac.signal.aborted) return;
        entry = r.entry;
        setScenes((ss) => ss.map((s) => (s.id === entry.id ? entry : s)));
        if (r.timedOut) { setPhase({ k: 'processing', entry, timedOut: true }); return; }
        if (!entry.manifestUrl) { setPhase({ k: 'job-failed', entry }); return; }
      }
      const manifestUrl = entry.manifestUrl!;
      setPhase({ k: 'manifest-loading' });
      let manifest: ArtifactManifest, warnings: string[], artifactUrl: string;
      try {
        const res = await fetch(manifestUrl, { signal: ac.signal, headers: { accept: 'application/json' } });
        if (res.status === 404) throw new Error('artifact manifest not found (HTTP 404): the job has no viewer artifact yet');
        if (!res.ok) throw new Error(`HTTP ${res.status} fetching manifest`);
        const pr = parseManifest(await res.json().catch(() => { throw new Error('manifest is not valid JSON'); }));
        if (!pr.ok) { setPhase({ k: 'manifest-error', msg: pr.errors }); return; }
        manifest = pr.manifest; warnings = pr.warnings;
        artifactUrl = resolveArtifactUrl(manifest.uri, manifestUrl);
      } catch (e) {
        if (ac.signal.aborted) return;
        setPhase({ k: 'manifest-error', msg: [(e as Error).message] }); return;
      }
      setPhase({ k: 'artifact-loading', loaded: 0, total: manifest.byte_size ?? null });
      try {
        let last = 0;
        const { bytes, sha256 } = await loadVerifiedGlb(artifactUrl, manifest.sha256, {
          signal: ac.signal,
          onProgress: (l, t) => { const now = performance.now(); if (now - last > 100) { last = now; setPhase({ k: 'artifact-loading', loaded: l, total: t ?? manifest.byte_size ?? null }); } },
        });
        const gltf = await new Promise<{ scene: Group }>((ok, fail) => new GLTFLoader().parse(bytes, '', ok as never, (err) => fail(new ArtifactError('parse', `GLB could not be parsed: ${(err as unknown as Error)?.message ?? String(err)}`))));
        if (ac.signal.aborted) return;
        const report = inspect(gltf.scene, manifest);
        setLoaded({ manifest, manifestUrl, artifactUrl, sha256, bytes: bytes.byteLength, root: gltf.scene, report, warnings, toTarget: matrixFromRowMajor(manifest.transform_4x4) });
        setPhase({ k: 'ready' }); setFrameKey((k) => k + 1);
      } catch (e) {
        if (ac.signal.aborted) return;
        const err = e as ArtifactError;
        setPhase({ k: 'artifact-error', kind: err.kind ?? 'parse', msg: err.message });
      }
    })().catch((e) => { if (!ac.signal.aborted) setPhase({ k: 'scenes-error', msg: (e as Error).message }); });
    return () => ac.abort();
  }, [selectedId, source]);

  const box = useMemo(() => (loaded ? new Box3().setFromObject(loaded.root) : null), [loaded]);
  const refEndpointsViewer = useMemo(() => {
    if (!loaded?.manifest.reference_lengths?.length) return [];
    const inv = loaded.toTarget.clone().invert();
    return loaded.manifest.reference_lengths[0].endpoints_m.map((e) => toFrame(e, inv).toArray());
  }, [loaded]);
  const helper = useMemo(() => (selected ? new BoxHelper(selected, 0xff9a3c) : null), [selected]);

  const onPick = (e: ThreeEvent<MouseEvent>) => {
    if (e.delta > 4 || !loaded) return; // a drag, not a click
    e.stopPropagation();
    const hit = e.intersections[0]; if (!hit) return;
    if (mode === 'select') { setSelected(hit.object); return; }
    const r = box ? Math.max(box.getSize(new Vector3()).length() * 0.004, 0.02) : 0.05;
    const s = snap ? snapToFaceVertex(hit, Math.min(r, hit.distance * 0.01 + 0.01)) : { point: hit.point.clone(), snapped: false };
    const p = { ...makePoint(s.point, loaded.toTarget, s.snapped, hit.object.name || '(unnamed)'), markerR: Math.max(hit.distance * 0.006, 0.004) };
    setPoints((ps) => (ps.length >= 2 ? [p] : [...ps, p]));
  };

  const resetCamera = useCallback(() => { setFocusBox(null); setViewDir(null); setFrameKey((k) => k + 1); resets.current++; }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      const c = controls.current;
      if (e.key === 'r' || e.key === 'R') resetCamera();
      if (e.key === 'm' || e.key === 'M') setMode((m) => (m === 'measure' ? 'select' : 'measure'));
      if (e.key === 'f' || e.key === 'F') setFly((f) => !f);
      if (e.key === 'Escape') { setPoints([]); setSelected(null); }
      if (c && (e.key === '+' || e.key === '=')) { c.dollyIn?.(1.2); c.update(); }
      if (c && e.key === '-') { c.dollyOut?.(1.2); c.update(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [resetCamera]);

  const distance = points.length === 2 ? distanceM(points[0], points[1]) : null;
  const refOk = loaded ? (loaded.report.references.length ? loaded.report.references.every((r) => r.endpointsOnGeometry === true) : null) : null;
  const nodeRef = loaded ? checkReferenceScale(loaded.root) : null;
  const errorText = phase.k === 'scenes-error' ? phase.msg : phase.k === 'manifest-error' ? phase.msg.join('; ') : phase.k === 'artifact-error' ? `${phase.kind}: ${phase.msg}` : phase.k === 'job-failed' ? `job ${phase.entry.job?.status}` : null;

  useEffect(() => {
    window.__viewer = {
      phase: phase.k, ready: phase.k === 'ready', error: errorText, stats,
      loaded: loaded ? { artifact_id: loaded.manifest.artifact_id, sha256: loaded.sha256, scale_status: loaded.manifest.scale_status, issues: loaded.report.issues, refOk } : null,
      measurement: { points: points.map((p) => p.target.toArray()), distance_m: distance, snapped: points.map((p) => p.snapped) },
      selected: selected?.name ?? null, mode, resets: resets.current,
      project: (p) => projectRef.current(p), refEndpointsViewer,
      frameOn: (min, max, dir) => { setFocusBox(new Box3(new Vector3(...min), new Vector3(...max))); setViewDir(dir ? new Vector3(...dir) : null); setFrameKey((k) => k + 1); },
    };
  });

  const validated = loaded?.manifest.scale_status === 'metric_validated' && loaded.report.issues.length === 0;

  return (
    <div className="root">
      <Canvas camera={{ position: [18, 14, 22], fov: 50, near: 0.05, far: 5000 }} gl={{ antialias: true }} dpr={[1, 2]} onPointerMissed={() => mode === 'select' && setSelected(null)}>
        <color attach="background" args={['#14171c']} />
        <hemisphereLight args={['#dfe8f5', '#3a3428', 0.9]} />
        <directionalLight position={[30, 40, 20]} intensity={2.2} />
        <Grid args={[80, 80]} cellSize={1} sectionSize={10} infiniteGrid fadeDistance={box ? Math.max(box.getSize(new Vector3()).length() * 2, 120) : 120} cellColor="#2a3038" sectionColor="#47505c" position={[0, box ? box.min.y - 0.002 : 0.001, 0]} />
        {loaded && <primitive object={loaded.root} onClick={onPick} />}
        {helper && <primitive object={helper} />}
        {points.map((p, i) => (
          <mesh key={i} position={p.viewer} renderOrder={10}>
            <sphereGeometry args={[p.markerR ?? 0.03, 16, 12]} />
            <meshBasicMaterial color={p.snapped ? '#7bd88f' : '#ff9a3c'} depthTest={false} />
          </mesh>
        ))}
        {points.length === 2 && <Line points={[points[0].viewer, points[1].viewer]} color="#ff9a3c" lineWidth={2} depthTest={false} renderOrder={9} />}
        <OrbitControls ref={controls} makeDefault enableDamping={false} />
        <CameraRig controls={controls} frameKey={frameKey} box={focusBox ?? box} viewDir={viewDir} fly={fly} onProject={(f) => { projectRef.current = f; }} />
        <Probe controls={controls} toTarget={loaded?.toTarget ?? null} onStats={setStats} />
      </Canvas>

      <aside className="hud" aria-live="polite" data-testid="hud">
        <h1>Scannit viewer</h1>
        <label className="row">Scene{' '}
          <select data-testid="scene-select" value={selectedId ?? ''} onChange={(e) => setSelectedId(e.target.value)} disabled={!scenes.length}>
            {scenes.map((s) => <option key={s.id} value={s.id}>{s.label}{s.job ? ` · ${s.job.status}` : ''}</option>)}
          </select>
        </label>
        <p className="muted">Source: {source instanceof Error ? 'misconfigured' : source.kind}</p>

        <Status phase={phase} onRetry={() => (phase.k === 'scenes-error' || phase.k === 'no-scenes' ? loadScenes() : setSelectedId((id) => { setTimeout(() => setSelectedId(id), 0); return null; }))} onCancel={() => loadAbort.current?.abort()} />

        {loaded && (
          <>
            <div data-testid="scale-badge" className={`badge ${validated ? 'ok' : 'warn'}`}>
              {validated ? 'Verified metric scale' : loaded.manifest.scale_status === 'arbitrary' ? 'Arbitrary scale — not metric' : 'Metric scale not validated'}
            </div>
            <dl>
              <dt>Artifact</dt><dd data-testid="artifact-id">{loaded.manifest.artifact_id}</dd>
              <dt>sha256</dt><dd data-testid="artifact-sha" title={loaded.sha256}>{loaded.sha256.slice(0, 12)}… ✓</dd>
              <dt>Dataset · job</dt><dd>{loaded.manifest.dataset_id ?? '—'} · {loaded.manifest.job_id ?? '—'}</dd>
              <dt>Frames</dt><dd>{loaded.manifest.source_frame_id} → {loaded.manifest.target_frame_id}</dd>
              <dt>Units</dt><dd>meters (as delivered, not rescaled)</dd>
              <dt>Size</dt><dd>{fmtBytes(loaded.bytes)} · {loaded.report.vertexCount.toLocaleString()} vertices</dd>
              <dt>Target</dt><dd data-testid="cam">{stats.camTarget} m</dd>
              <dt>FPS · tris</dt><dd>{stats.fps} · {stats.tris.toLocaleString()}</dd>
              <dt>Reference</dt>
              <dd data-testid="scale" className={refOk ? 'ok' : refOk === false ? 'bad' : ''}>
                {loaded.report.references.length
                  ? loaded.report.references.map((r) => `${r.id} ${r.declared_m.toFixed(3)} m ${r.endpointsOnGeometry ? 'on geometry ✓' : r.endpointsOnGeometry === false ? '✗ off geometry' : 'unchecked'}`).join('; ')
                  : nodeRef?.found ? `${nodeRef.measured_m!.toFixed(3)} m` : 'none in manifest'}
              </dd>
            </dl>
            {[...loaded.warnings, ...loaded.report.issues].length > 0 && (
              <ul className="warnings" data-testid="warnings">{[...loaded.warnings, ...loaded.report.issues].map((w) => <li key={w}>{w}</li>)}</ul>
            )}

            <div className="tools" role="group" aria-label="Tools">
              <button type="button" aria-pressed={mode === 'measure'} onClick={() => setMode((m) => (m === 'measure' ? 'select' : 'measure'))}>Measure (M)</button>
              <button type="button" aria-pressed={fly} onClick={() => setFly((f) => !f)}>Fly (F)</button>
              <button type="button" onClick={resetCamera}>Fit to scene (R)</button>
            </div>
            {mode === 'measure' && (
              <div className="measure" data-testid="measure">
                <label><input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> Snap to vertices</label>
                <p>{points.length === 0 ? 'Click the first point.' : points.length === 1 ? 'Click the second point.' : null}</p>
                {distance !== null && <p className="dist" data-testid="distance">{fmtM(distance)}{!validated && <span className="muted"> (not validated)</span>}</p>}
                {points.map((p, i) => <p key={i} className="muted">P{i + 1} {fmtVec(p.target)} m in {loaded.manifest.target_frame_id}{p.snapped ? ' · snapped' : ''}</p>)}
              </div>
            )}
            {selected && mode === 'select' && <p data-testid="selected">Selected: {selected.name || '(unnamed)'}</p>}
            <p><a href={loaded.artifactUrl} download={`${loaded.manifest.artifact_id}.glb`} data-testid="download">Download original GLB</a><span className="muted"> — the file as produced (sha256 {loaded.sha256.slice(0, 8)}…), not a new export</span></p>
          </>
        )}
        <p className="hint">Drag orbit · right-drag pan · wheel zoom · F fly (WASD, Q/E, Shift) · M measure · Esc clear · R fit</p>
        {loaded?.manifest.synthetic && <p className="note">Synthetic scene. Measurements here test the software, not reconstruction accuracy.</p>}
      </aside>
    </div>
  );
}

function Status({ phase, onRetry, onCancel }: { phase: Phase; onRetry: () => void; onCancel: () => void }) {
  switch (phase.k) {
    case 'ready': case 'idle': return null;
    case 'scenes-loading': return <p data-testid="status">Loading scene list…</p>;
    case 'no-scenes': return <div data-testid="status" className="state">No scenes yet. Start a reconstruction job, then refresh. <button type="button" onClick={onRetry}>Refresh</button></div>;
    case 'scenes-error': return <div data-testid="status" className="state bad" role="alert">Could not load scenes: {phase.msg} <button type="button" onClick={onRetry}>Retry</button></div>;
    case 'processing': {
      const j = phase.entry.job!;
      return <div data-testid="status" className="state">{phase.timedOut ? 'Still processing; stopped checking.' : 'Processing'} — {j.status}{j.progress_pct != null ? ` (${Math.round(j.progress_pct)}%)` : ''}{' '}
        {phase.timedOut ? <button type="button" onClick={onRetry}>Check again</button> : <button type="button" onClick={onCancel}>Stop waiting</button>}</div>;
    }
    case 'job-failed': return <div data-testid="status" className="state bad" role="alert">Job {phase.entry.job?.status.toLowerCase()}: {phase.entry.job?.error_message ?? 'no viewer artifact was produced'}.</div>;
    case 'manifest-loading': return <p data-testid="status">Loading artifact manifest…</p>;
    case 'manifest-error': return <div data-testid="status" className="state bad" role="alert">Manifest rejected:<ul>{phase.msg.map((m) => <li key={m}>{m}</li>)}</ul><button type="button" onClick={onRetry}>Retry</button></div>;
    case 'artifact-loading': return <div data-testid="status">Downloading model… {fmtBytes(phase.loaded)}{phase.total ? ` of ${fmtBytes(phase.total)}` : ''} <button type="button" onClick={onCancel}>Cancel</button></div>;
    case 'artifact-error': return <div data-testid="status" className="state bad" role="alert">Model not shown ({phase.kind}): {phase.msg} <button type="button" onClick={onRetry}>Retry</button></div>;
  }
}
