import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Grid, Line, OrbitControls } from '@react-three/drei';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box3, BoxHelper, Group, PerspectiveCamera, Raycaster, Vector3, type Material, type Mesh, type Object3D, type Points, type PointsMaterial, type Texture } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { parseManifest, resolveArtifactUrl, type ArtifactManifest } from './manifest';
import { ArtifactError, assertGlb, fmtBytes, loadVerifiedGlb, sha256Hex } from './loader';
import { pollUntilTerminal, sourceFromLocation, type DataSource, type SceneEntry } from './source';
import { GLTF_Y_UP_TO_SCENE_Z_UP, SCENE_Z_UP_TO_GLTF_Y_UP, VIEW_TO_SCENE, isIdentity, placementMatrix, toFrame } from './frames';
import { distanceM, fmtLength, fmtVec, makePoint, snapToFaceVertex, type PickedPoint } from './measure';
import { geometryObjects, inspect, type IntegrityReport } from './integrity';
import { checkReferenceScale } from './scale';
import { LIFECYCLE_LABEL, lifecycleFromJob, lifecycleWhenLoaded, type Lifecycle } from './lifecycle';

type Phase =
  | { k: 'scenes-loading' } | { k: 'scenes-error'; msg: string } | { k: 'no-scenes' }
  | { k: 'idle' } | { k: 'processing'; entry: SceneEntry; timedOut?: boolean } | { k: 'job-failed'; entry: SceneEntry }
  | { k: 'manifest-loading'; tile?: [number, number] } | { k: 'manifest-error'; msg: string[] }
  | { k: 'artifact-loading'; loaded: number; total: number | null; tile?: [number, number] } | { k: 'artifact-error'; kind: string; msg: string }
  | { k: 'ready' };

interface Tile { manifest: ArtifactManifest; manifestUrl: string | null; artifactUrl: string | null; sha256: string; verified: boolean; bytes: number; root: Group; report: IntegrityReport; warnings: string[]; notes: string[]; placed: boolean; sourceImages: number | null }
interface Loaded { tiles: Tile[]; expected: number; local: boolean }
type Nav = 'orbit' | 'fly' | 'walk';
interface Stats { fps: number; tris: number; calls: number; camTarget: string; geometries: number; textures: number }
interface Inspect { object: string; artifact_id: string; scene: number[]; pointIndex: number | null; sourceManifestUrl: string | null }

declare global {
  interface Window {
    __viewer?: {
      phase: string; lifecycle: Lifecycle | null; ready: boolean; error: string | null; stats: Stats; nav: Nav; reducedMotion: boolean; local: boolean;
      tiles: { artifact_id: string; placed: boolean; issues: string[] }[];
      loaded: { artifact_id: string; sha256: string; scale_status: string; origin: string; issues: string[]; notes: string[]; warnings: string[]; refOk: boolean | null; points: number; triangles: number; sourceImages: number | null; placed: boolean; verified: boolean } | null;
      measurement: { points: number[][]; distance_m: number | null; snapped: boolean[] }; selected: string | null; inspect: Inspect | null; mode: string; resets: number;
      camera: number[];
      project: (viewerXYZ: number[]) => { x: number; y: number } | null; refEndpointsViewer: number[][];
      frameOn: (min: number[], max: number[], viewDir?: number[]) => void;
    };
  }
}

const q = new URLSearchParams(location.search);
const POLL_MS = Number(q.get('poll') ?? 5000);
const POLL_MAX = Number(q.get('pollMax') ?? 120);
const VIEW_TO_SCENE_M = VIEW_TO_SCENE();
const EYE_M = 1.7;
const prefersReducedMotion = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };

/** Free GPU memory held by a replaced scene. */
export function disposeTree(root: Object3D) {
  root.traverse((o) => {
    const m = o as Mesh;
    m.geometry?.dispose?.();
    const mats = (Array.isArray(m.material) ? m.material : m.material ? [m.material] : []) as (Material & Record<string, unknown>)[];
    for (const mat of mats) {
      for (const v of Object.values(mat)) if ((v as Texture)?.isTexture) (v as Texture).dispose();
      mat.dispose();
    }
  });
}

async function countSourceImages(manifest: ArtifactManifest, manifestUrl: string | null, signal?: AbortSignal): Promise<number | null> {
  if (!manifest.source_image_manifest_uri || !manifestUrl) return null;
  try {
    const res = await fetch(resolveArtifactUrl(manifest.source_image_manifest_uri, manifestUrl), { signal });
    if (!res.ok) return null;
    const j = (await res.json()) as { frames?: unknown[]; summary?: { frames_kept?: number } };
    return Array.isArray(j.frames) ? j.frames.length : typeof j.summary?.frames_kept === 'number' ? j.summary.frames_kept : null;
  } catch { return null; }
}

const parseGlb = (bytes: ArrayBuffer) => new Promise<{ scene: Group }>((ok, fail) =>
  new GLTFLoader().parse(bytes, '', ok as never, (err) => fail(new ArtifactError('parse', `GLB could not be parsed: ${(err as unknown as Error)?.message ?? String(err)}`))));

/** Shared by API, fixture and local paths: place from manifest evidence, style points, inspect. */
async function buildTile(bytes: ArrayBuffer, manifest: ArtifactManifest, o: { manifestUrl: string | null; artifactUrl: string | null; sha256: string; verified: boolean; warnings: string[]; notes: string[]; signal?: AbortSignal }): Promise<Tile> {
  const { scene: root } = await parseGlb(bytes);
  const place = placementMatrix(manifest.glb_to_scene);
  const placed = !isIdentity(place);
  const notes = [...o.notes];
  if (placed) { root.matrixAutoUpdate = false; root.matrix.copy(place); notes.push(`${manifest.artifact_id}: placed with its manifest transform (not the bare Z-up/Y-up pair)`); }
  root.traverse((x) => { if ((x as Points).isPoints) { const pm = (x as Points).material as PointsMaterial; pm.size = 2; pm.sizeAttenuation = false; } });
  const report = inspect(root, manifest);
  const sourceImages = await countSourceImages(manifest, o.manifestUrl, o.signal);
  return { manifest, manifestUrl: o.manifestUrl, artifactUrl: o.artifactUrl, sha256: o.sha256, verified: o.verified, bytes: bytes.byteLength, root, report, warnings: o.warnings, notes, placed, sourceImages };
}

class ManifestRejected extends Error { constructor(public errors: string[]) { super(errors.join('; ')); } }

async function loadTileFromUrl(manifestUrl: string, signal: AbortSignal, onPhase: (p: Phase) => void, tile?: [number, number]): Promise<Tile> {
  onPhase({ k: 'manifest-loading', tile });
  let res: Response;
  try { res = await fetch(manifestUrl, { signal, headers: { accept: 'application/json' } }); }
  catch (e) { if (signal.aborted) throw e; throw new ManifestRejected([`network or CORS failure fetching manifest (${(e as Error).message})`]); }
  if (res.status === 404) throw new ManifestRejected(['artifact manifest not found (HTTP 404): the job has no viewer artifact yet']);
  if (!res.ok) throw new ManifestRejected([`HTTP ${res.status} fetching manifest`]);
  const json = await res.json().catch(() => { throw new ManifestRejected(['manifest is not valid JSON']); });
  const pr = parseManifest(json);
  if (!pr.ok) throw new ManifestRejected(pr.errors);
  const m = pr.manifest;
  let artifactUrl: string;
  try { artifactUrl = resolveArtifactUrl(m.uri, manifestUrl); } catch (e) { throw new ManifestRejected([(e as Error).message]); }
  onPhase({ k: 'artifact-loading', loaded: 0, total: m.byte_size ?? null, tile });
  let last = 0;
  const { bytes, sha256 } = await loadVerifiedGlb(artifactUrl, m.sha256, {
    signal,
    onProgress: (l, t) => { const now = performance.now(); if (now - last > 100) { last = now; onPhase({ k: 'artifact-loading', loaded: l, total: t ?? m.byte_size ?? null, tile }); } },
  });
  return buildTile(bytes, m, { manifestUrl, artifactUrl, sha256, verified: true, warnings: [...m.producer_warnings, ...pr.warnings], notes: pr.notes, signal });
}

/** LOCAL PREVIEW: a GLB (and optional manifest) opened from this computer. Never shown as integrated. */
async function loadLocal(files: File[]): Promise<Tile> {
  const glbFile = files.find((f) => /\.glb$/i.test(f.name));
  const jsonFile = files.find((f) => /\.json$/i.test(f.name));
  if (!glbFile) throw new ManifestRejected(['choose a .glb file (optionally with its artifact manifest .json)']);
  const bytes = await glbFile.arrayBuffer();
  assertGlb(bytes);
  const sha256 = await sha256Hex(bytes);
  const localWarn = 'LOCAL PREVIEW: opened from this computer, not delivered by the Scannit API; not evidence of an integrated result';
  if (jsonFile) {
    const pr = parseManifest(JSON.parse(await jsonFile.text()));
    if (!pr.ok) throw new ManifestRejected(pr.errors);
    if (pr.manifest.sha256 !== sha256) throw new ArtifactError('hash_mismatch', `sha256 mismatch: manifest ${pr.manifest.sha256.slice(0, 12)}…, file ${sha256.slice(0, 12)}…`);
    return buildTile(bytes, pr.manifest, { manifestUrl: null, artifactUrl: null, sha256, verified: true, warnings: [localWarn, ...pr.manifest.producer_warnings, ...pr.warnings], notes: pr.notes });
  }
  // No manifest: assume the glTF standard (Y-up, meters) but claim nothing about scale or origin.
  const tmp = await parseGlb(bytes);
  const b = new Box3().setFromObject(tmp.scene);
  disposeTree(tmp.scene);
  const manifest: ArtifactManifest = {
    schema_version: 'local', artifact_id: `local:${glbFile.name}`, uri: glbFile.name, sha256, mime_type: 'model/gltf-binary', byte_size: bytes.byteLength,
    glb_to_scene: [...GLTF_Y_UP_TO_SCENE_Z_UP], glb_frame_id: 'gltf_yup', scene_frame_id: 'scene_zup',
    source_frame_id: 'scene_zup', target_frame_id: 'gltf_yup', transform_4x4: [...SCENE_Z_UP_TO_GLTF_Y_UP],
    bounds_m: { frame_id: 'gltf_yup', min: b.min.toArray(), max: b.max.toArray() }, unit: 'm', scale_status: 'arbitrary',
    artifact_origin: 'unknown', producer_warnings: [], raw: { note: 'synthesised by viewer for LOCAL PREVIEW; no manifest supplied' },
  };
  return buildTile(bytes, manifest, { manifestUrl: null, artifactUrl: null, sha256, verified: false, warnings: [localWarn, 'no manifest: hash not verified, scale arbitrary, origin unknown, glTF Y-up assumed'], notes: [] });
}

function CameraRig({ controls, frameKey, box, viewDir, onProject, nav, reducedMotion, collidables, onCamera }: {
  controls: React.RefObject<OrbitControlsImpl | null>; frameKey: number; box: Box3 | null; viewDir: Vector3 | null;
  onProject: (f: (p: number[]) => { x: number; y: number } | null) => void; nav: Nav; reducedMotion: boolean; collidables: Object3D[]; onCamera: (p: number[]) => void;
}) {
  const { camera, gl, size } = useThree();
  const keys = useRef(new Set<string>());
  const ray = useMemo(() => new Raycaster(), []);
  useEffect(() => {
    onProject((p) => {
      const v = new Vector3(p[0], p[1], p[2]).project(camera);
      if (v.z > 1) return null;
      const r = gl.domElement.getBoundingClientRect();
      return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
    });
  }, [camera, gl, size, onProject]);
  useEffect(() => {
    const c = controls.current; if (!c || !box || box.isEmpty()) return;
    const cam = camera as PerspectiveCamera;
    const center = box.getCenter(new Vector3()), r = Math.max(box.getSize(new Vector3()).length() / 2, 0.5);
    const dist = r / Math.sin((cam.fov * Math.PI) / 360) * 1.1;
    cam.position.copy(center).add((viewDir ?? new Vector3(0.6, 0.45, 0.65)).clone().normalize().multiplyScalar(dist));
    cam.near = Math.max(r / 2000, 0.01); cam.far = Math.max(r * 200, 1000); cam.updateProjectionMatrix();
    c.target.copy(center); c.update();
  }, [frameKey, box, viewDir, camera, controls]);
  // Entering walk mode: drop to eye height above the surface below, look level.
  useEffect(() => {
    const c = controls.current; if (nav !== 'walk' || !c) return;
    ray.set(camera.position.clone().add(new Vector3(0, 1e4, 0)), new Vector3(0, -1, 0));
    ray.far = 2e4;
    let hit = ray.intersectObjects(collidables, true).find((h) => h.point.y <= camera.position.y);
    if (!hit) {
      // Nothing below the camera: start walking where the view was centred.
      ray.set(c.target.clone().add(new Vector3(0, 1e4, 0)), new Vector3(0, -1, 0));
      hit = ray.intersectObjects(collidables, true)[0];
      if (hit) camera.position.set(hit.point.x, hit.point.y, hit.point.z);
    }
    if (hit) camera.position.y = hit.point.y + EYE_M;
    const fwd = new Vector3(); camera.getWorldDirection(fwd); fwd.y = 0; if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1); fwd.normalize();
    c.target.copy(camera.position).add(fwd.multiplyScalar(0.5)); c.update();
  }, [nav, camera, controls, collidables, ray]);
  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (!(e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement)) keys.current.add(e.key.toLowerCase()); };
    const up = (e: KeyboardEvent) => keys.current.delete(e.key.toLowerCase());
    const blur = () => keys.current.clear();
    window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', blur);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur); };
  }, []);
  const lastCam = useRef('');
  useFrame((_, dt) => {
    const c = controls.current;
    const camKey = camera.position.toArray().map((v) => v.toFixed(3)).join();
    if (camKey !== lastCam.current) { lastCam.current = camKey; onCamera(camera.position.toArray()); }
    if (nav === 'orbit' || !c) return;
    const k = keys.current; if (!k.size) return;
    const boost = k.has('shift') && !reducedMotion ? 3 : 1;
    // Frame-rate independent: integrate the whole frame time (up to 1 s) in sub-steps of <= 50 ms,
    // so speed is the same at 5 fps and 120 fps and collision is still checked every few cm.
    const total = Math.min(dt, 1);
    const n = Math.max(1, Math.ceil(total / 0.05));
    const step = total / n;
    const fwd = new Vector3(); camera.getWorldDirection(fwd); fwd.y = 0; fwd.normalize();
    const right = new Vector3().crossVectors(fwd, camera.up).normalize();
    const mv = new Vector3();
    if (k.has('w') || k.has('arrowup')) mv.add(fwd); if (k.has('s') || k.has('arrowdown')) mv.sub(fwd);
    if (k.has('d') || k.has('arrowright')) mv.add(right); if (k.has('a') || k.has('arrowleft')) mv.sub(right);
    if (nav === 'fly') { if (k.has('e')) mv.y += 1; if (k.has('q')) mv.y -= 1; }
    if (!mv.lengthSq()) return;
    mv.normalize();
    if (nav === 'fly') {
      const r = box && !box.isEmpty() ? box.getSize(new Vector3()).length() / 2 : 20;
      mv.multiplyScalar(Math.min(Math.max(r / 8, 1), 60) * (reducedMotion ? 0.5 : 1) * boost * total);
      camera.position.add(mv); c.target.add(mv); c.update();
      return;
    }
    // Walk: 1.4 m/s, blocked by geometry ahead at knee height, follows the surface below.
    const dist = 1.4 * (reducedMotion ? 0.5 : 1) * boost * step;
    const start = camera.position.clone();
    for (let i = 0; i < n; i++) {
      const knee = camera.position.clone(); knee.y -= EYE_M - 0.5;
      ray.set(knee, mv); ray.far = dist + 0.3;
      if (ray.intersectObjects(collidables, true).length) break; // collision: stop
      const next = camera.position.clone().addScaledVector(mv, dist);
      ray.set(next.clone().add(new Vector3(0, 0.6, 0)), new Vector3(0, -1, 0)); ray.far = EYE_M + 3.6;
      const ground = ray.intersectObjects(collidables, true)[0];
      if (ground) next.y = ground.point.y + EYE_M; // steps up to 0.6 m, drops up to 3 m
      camera.position.copy(next);
    }
    c.target.add(camera.position.clone().sub(start)); c.update();
  });
  return null;
}

function Probe({ controls, onStats }: { controls: React.RefObject<OrbitControlsImpl | null>; onStats: (s: Stats) => void }) {
  const { gl } = useThree();
  const acc = useRef({ t: 0, n: 0 });
  useFrame((_, dt) => {
    acc.current.t += dt; acc.current.n++;
    if (acc.current.t < 0.5) return;
    const t = controls.current?.target;
    onStats({ fps: Math.round(acc.current.n / acc.current.t), tris: gl.info.render.triangles, calls: gl.info.render.calls, camTarget: t ? fmtVec(toFrame(t, VIEW_TO_SCENE_M)) : '—', geometries: gl.info.memory.geometries, textures: gl.info.memory.textures });
    acc.current = { t: 0, n: 0 };
  });
  return null;
}

function PointsPicking({ box }: { box: Box3 | null }) {
  const { raycaster } = useThree();
  useEffect(() => {
    const d = box && !box.isEmpty() ? box.getSize(new Vector3()).length() : 10;
    raycaster.params.Points = { threshold: Math.max(d * 0.002, 0.005) };
  }, [box, raycaster]);
  return null;
}

function downloadJson(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function metadataFor(l: Loaded) {
  return {
    generator: 'scannit-web viewer', generated_at: new Date().toISOString(), local_preview: l.local,
    note: 'Describes the artifact(s) as received and verified by the viewer. Not a new export; geometry is unchanged.',
    tiles: l.tiles.map((t) => ({
      artifact_id: t.manifest.artifact_id, sha256: t.sha256, sha256_verified_against_manifest: t.verified, bytes: t.bytes,
      origin: t.manifest.artifact_origin, scale_status: t.manifest.scale_status,
      frames: { glb: t.manifest.glb_frame_id, scene: t.manifest.scene_frame_id, glb_to_scene_row_major: t.manifest.glb_to_scene, manifest_transform_row_major: t.manifest.transform_4x4, as_written: { source: t.manifest.source_frame_id, target: t.manifest.target_frame_id } },
      geometry: { triangles: Math.round(t.report.triangleCount), points: t.report.pointCount, bounds_scene_m: t.report.geometryBoundsScene, bounds_mismatch_m: t.report.boundsMismatch_m },
      references: t.report.references.map((r) => ({ id: r.id, declared_m: r.declared_m, on_geometry: r.endpointsOnGeometry })),
      source_images: t.sourceImages, warnings: t.warnings, notes: t.notes, issues: t.report.issues, manifest_raw: t.manifest.raw,
    })),
  };
}

export default function App() {
  const source = useMemo<DataSource | Error>(() => { try { return sourceFromLocation(location, import.meta.env as Record<string, string>); } catch (e) { return e as Error; } }, []);
  const [phase, setPhase] = useState<Phase>({ k: 'scenes-loading' });
  const [scenes, setScenes] = useState<SceneEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [stats, setStats] = useState<Stats>({ fps: 0, tris: 0, calls: 0, camTarget: '—', geometries: 0, textures: 0 });
  const [mode, setMode] = useState<'select' | 'measure'>('select');
  const [nav, setNav] = useState<Nav>('orbit');
  const [reducedMotion, setReducedMotion] = useState(prefersReducedMotion);
  const [snap, setSnap] = useState(true);
  const [points, setPoints] = useState<PickedPoint[]>([]);
  const [selected, setSelected] = useState<Object3D | null>(null);
  const [inspectInfo, setInspectInfo] = useState<Inspect | null>(null);
  const [frameKey, setFrameKey] = useState(0);
  const [focusBox, setFocusBox] = useState<Box3 | null>(null);
  const [viewDir, setViewDir] = useState<Vector3 | null>(null);
  const [navMsg, setNavMsg] = useState<string | null>(null);
  const controls = useRef<OrbitControlsImpl | null>(null);
  const loadAbort = useRef<AbortController | null>(null);
  const projectRef = useRef<(p: number[]) => { x: number; y: number } | null>(() => null);
  const cameraRef = useRef<number[]>([0, 0, 0]);
  const resets = useRef(0);
  const fileInput = useRef<HTMLInputElement | null>(null);

  // Dispose only AFTER the commit that removed tiles from the scene: disposing while a tile is still
  // mounted lets the render loop draw it once more, which silently re-uploads (leaks) its buffers.
  const pendingDispose = useRef<Tile[]>([]);
  const shownTiles = useRef<Tile[]>([]);
  useEffect(() => {
    const now = loaded?.tiles ?? [];
    pendingDispose.current.push(...shownTiles.current.filter((t) => !now.includes(t)));
    shownTiles.current = now;
    const victims = pendingDispose.current.splice(0);
    victims.forEach((t) => disposeTree(t.root));
  }, [loaded]);
  const replaceLoaded = useCallback((next: Loaded | null) => {
    setLoaded(next);
    setPoints([]); setSelected(null); setInspectInfo(null); setNav('orbit'); setNavMsg(null);
  }, []);

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

  // Selected scene -> (poll) -> manifest(s) -> verified GLB(s) -> tiles, progressively. Cancelled on change.
  useEffect(() => {
    if (!selectedId || source instanceof Error) return;
    const entry0 = scenes.find((s) => s.id === selectedId); if (!entry0) return;
    loadAbort.current?.abort();
    const ac = new AbortController(); loadAbort.current = ac;
    replaceLoaded(null);
    const onPhase = (p: Phase) => { if (!ac.signal.aborted) setPhase(p); };
    (async () => {
      let entry = entry0;
      if (entry.job && !entry.manifestUrl) {
        if (entry.job.status === 'FAILED' || entry.job.status === 'CANCELLED' || entry.job.status === 'NEEDS_REFERENCE') { setPhase({ k: 'job-failed', entry }); return; }
        setPhase({ k: 'processing', entry });
        const r = await pollUntilTerminal(source, entry.id, { intervalMs: POLL_MS, maxAttempts: POLL_MAX, signal: ac.signal, onUpdate: (e) => onPhase({ k: 'processing', entry: e }) });
        if (ac.signal.aborted) return;
        entry = r.entry;
        setScenes((ss) => ss.map((s) => (s.id === entry.id ? entry : s)));
        if (r.timedOut) { setPhase({ k: 'processing', entry, timedOut: true }); return; }
        if (!entry.manifestUrl) { setPhase({ k: 'job-failed', entry }); return; }
      }
      const urls = entry.tileManifestUrls ?? [entry.manifestUrl!];
      const tiles: Tile[] = [];
      try {
        for (let i = 0; i < urls.length; i++) {
          const t = await loadTileFromUrl(urls[i], ac.signal, onPhase, urls.length > 1 ? [i + 1, urls.length] : undefined);
          if (ac.signal.aborted) { disposeTree(t.root); return; }
          tiles.push(t);
          // Progressive: show each tile as soon as it is verified; frame the camera on the first.
          setLoaded({ tiles: [...tiles], expected: urls.length, local: false });
          if (i === 0) setFrameKey((k) => k + 1);
        }
        setPhase({ k: 'ready' });
      } catch (e) {
        if (ac.signal.aborted) { tiles.forEach((t) => disposeTree(t.root)); return; }
        if (e instanceof ManifestRejected) setPhase({ k: 'manifest-error', msg: e.errors });
        else setPhase({ k: 'artifact-error', kind: (e as ArtifactError).kind ?? 'parse', msg: (e as Error).message });
      }
    })().catch((e) => { if (!ac.signal.aborted) setPhase({ k: 'scenes-error', msg: (e as Error).message }); });
    return () => ac.abort();
  }, [selectedId, source, replaceLoaded]);

  const openLocal = async (files: File[]) => {
    if (!files.length) return;
    loadAbort.current?.abort();
    replaceLoaded(null);
    setPhase({ k: 'manifest-loading' });
    try {
      const t = await loadLocal(files);
      replaceLoaded({ tiles: [t], expected: 1, local: true });
      setPhase({ k: 'ready' }); setFrameKey((k) => k + 1);
    } catch (e) {
      if (e instanceof ManifestRejected) setPhase({ k: 'manifest-error', msg: e.errors });
      else setPhase({ k: 'artifact-error', kind: (e as ArtifactError).kind ?? 'parse', msg: (e as Error).message });
    }
  };

  const primary = loaded?.tiles[0] ?? null;
  const box = useMemo(() => {
    if (!loaded?.tiles.length) return null;
    const b = new Box3(); for (const t of loaded.tiles) b.expandByObject(t.root); return b;
  }, [loaded]);
  const collidables = useMemo(() => (loaded ? loaded.tiles.flatMap((t) => geometryObjects(t.root).filter((o) => o.isMesh)) : []), [loaded]);
  const refEndpointsViewer = useMemo(() => primary?.report.references[0]?.endpointsWorld ?? [], [primary]);
  const helper = useMemo(() => (selected ? new BoxHelper(selected, 0xff9a3c) : null), [selected]);
  useEffect(() => () => { helper?.geometry.dispose(); (helper?.material as Material | undefined)?.dispose(); }, [helper]);
  const tileOf = useCallback((o: Object3D): Tile | null => {
    for (const t of loaded?.tiles ?? []) { let x: Object3D | null = o; while (x) { if (x === t.root) return t; x = x.parent; } }
    return null;
  }, [loaded]);

  const onPick = (e: ThreeEvent<MouseEvent>) => {
    if (e.delta > 4 || !loaded) return;
    e.stopPropagation();
    const hit = e.intersections[0]; if (!hit) return;
    const tile = tileOf(hit.object);
    if (mode === 'select') {
      const s = snapToFaceVertex(hit, 0);
      setSelected(hit.object);
      setInspectInfo({
        object: hit.object.name || '(unnamed)', artifact_id: tile?.manifest.artifact_id ?? '—',
        scene: toFrame(s.index != null ? s.point : hit.point, VIEW_TO_SCENE_M).toArray(), pointIndex: s.index ?? null,
        sourceManifestUrl: tile?.manifest.source_image_manifest_uri && tile.manifestUrl ? resolveArtifactUrl(tile.manifest.source_image_manifest_uri, tile.manifestUrl) : null,
      });
      return;
    }
    const r = box ? Math.max(box.getSize(new Vector3()).length() * 0.004, 0.02) : 0.05;
    const s = snap ? snapToFaceVertex(hit, Math.min(r, hit.distance * 0.01 + 0.01)) : { point: hit.point.clone(), snapped: false, index: undefined };
    const p = { ...makePoint(s.point, VIEW_TO_SCENE_M, s.snapped, hit.object.name || '(unnamed)'), markerR: Math.max(hit.distance * 0.006, 0.004), pointIndex: s.index };
    setPoints((ps) => (ps.length >= 2 ? [p] : [...ps, p]));
  };

  // Double-click: walk/fly teleports to the surface (eye height above it); orbit re-centres there.
  const onDoublePick = (e: ThreeEvent<MouseEvent>) => {
    const c = controls.current; const hit = e.intersections[0]; if (!c || !hit) return;
    e.stopPropagation();
    if (nav === 'walk' || nav === 'fly') {
      const dir = new Vector3().subVectors(c.target, c.object.position); dir.y = 0; if (dir.lengthSq() < 1e-6) dir.set(0, 0, -1); dir.normalize();
      const dest = hit.point.clone(); dest.y += EYE_M;
      c.object.position.copy(dest); c.target.copy(dest).add(dir.multiplyScalar(0.5)); c.update();
      setNavMsg(`Teleported to ${fmtVec(toFrame(hit.point, VIEW_TO_SCENE_M))} (+${EYE_M} m eye height)`);
    } else { c.target.copy(hit.point); c.update(); }
  };

  const setNavChecked = useCallback((n: Nav) => {
    if (n === 'walk' && loaded && !collidables.length) { setNavMsg('Walk needs a surface mesh; this artifact is points only. Use fly, or double-click to teleport.'); setNav('fly'); return; }
    setNavMsg(n === 'walk' ? `Walking at ${EYE_M} m eye height${primary?.manifest.scale_status === 'arbitrary' ? ' (assumes meters; this model has arbitrary scale)' : ''}. Double-click a surface to teleport.` : null);
    setNav(n);
  }, [loaded, collidables.length, primary]);

  const resetCamera = useCallback(() => { setFocusBox(null); setViewDir(null); setNav('orbit'); setNavMsg(null); setFrameKey((k) => k + 1); resets.current++; }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      const c = controls.current;
      if (e.key === 'r' || e.key === 'R') resetCamera();
      if (e.key === 'm' || e.key === 'M') setMode((m) => (m === 'measure' ? 'select' : 'measure'));
      if (e.key === 'f' || e.key === 'F') setNavChecked(nav === 'fly' ? 'orbit' : 'fly');
      if (e.key === 'g' || e.key === 'G') setNavChecked(nav === 'walk' ? 'orbit' : 'walk');
      if (e.key === 'Escape') { setPoints([]); setSelected(null); setInspectInfo(null); }
      if (c && nav === 'orbit' && (e.key === '+' || e.key === '=')) { c.dollyIn?.(1.2); c.update(); }
      if (c && nav === 'orbit' && e.key === '-') { c.dollyOut?.(1.2); c.update(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [resetCamera, setNavChecked, nav]);

  const distance = points.length === 2 ? distanceM(points[0], points[1]) : null;
  const refOk = primary ? (primary.report.references.length ? primary.report.references.every((r) => r.endpointsOnGeometry === true) : null) : null;
  const nodeRef = primary ? checkReferenceScale(primary.root) : null;
  const allIssues = loaded ? loaded.tiles.flatMap((t) => t.report.issues) : [];
  const scaleStatus = primary ? (loaded!.local && !primary.verified ? 'arbitrary' : primary.manifest.scale_status) : 'arbitrary';
  const lifecycle: Lifecycle | null =
    phase.k === 'processing' ? lifecycleFromJob(phase.entry.job!.status)
    : phase.k === 'job-failed' || phase.k === 'manifest-error' || phase.k === 'artifact-error' || phase.k === 'scenes-error' ? 'failed'
    : phase.k === 'ready' && primary ? lifecycleWhenLoaded(loaded!.local ? 'arbitrary' : scaleStatus, allIssues.length)
    : phase.k === 'manifest-loading' || phase.k === 'artifact-loading' ? 'processing' : null;
  const errorText = phase.k === 'scenes-error' ? phase.msg : phase.k === 'manifest-error' ? phase.msg.join('; ') : phase.k === 'artifact-error' ? `${phase.kind}: ${phase.msg}` : phase.k === 'job-failed' ? `job ${phase.entry.job?.status}` : null;
  const validated = !!primary && !loaded!.local && loaded!.tiles.every((t) => t.manifest.scale_status === 'metric_validated') && allIssues.length === 0;
  const uniq = (xs: string[]) => [...new Set(xs)];
  const warnings = loaded ? uniq(loaded.tiles.flatMap((t) => [...t.warnings, ...t.report.issues])) : [];
  const notes = loaded ? uniq(loaded.tiles.flatMap((t) => t.notes)) : [];

  useEffect(() => {
    window.__viewer = {
      phase: phase.k, lifecycle, ready: phase.k === 'ready', error: errorText, stats, nav, reducedMotion, local: !!loaded?.local,
      tiles: loaded ? loaded.tiles.map((t) => ({ artifact_id: t.manifest.artifact_id, placed: t.placed, issues: t.report.issues })) : [],
      loaded: primary ? { artifact_id: primary.manifest.artifact_id, sha256: primary.sha256, scale_status: scaleStatus, origin: primary.manifest.artifact_origin, issues: allIssues, notes, warnings, refOk, points: primary.report.pointCount, triangles: primary.report.triangleCount, sourceImages: primary.sourceImages, placed: primary.placed, verified: primary.verified } : null,
      measurement: { points: points.map((p) => p.target.toArray()), distance_m: distance, snapped: points.map((p) => p.snapped) },
      selected: selected?.name ?? null, inspect: inspectInfo, mode, resets: resets.current, camera: cameraRef.current,
      project: (p) => projectRef.current(p), refEndpointsViewer,
      frameOn: (min, max, dir) => { setFocusBox(new Box3(new Vector3(...min), new Vector3(...max))); setViewDir(dir ? new Vector3(...dir) : null); setFrameKey((k) => k + 1); },
    };
  });

  return (
    <div className="root">
      <Canvas camera={{ position: [18, 14, 22], fov: 50, near: 0.05, far: 5000 }} gl={{ antialias: true }} dpr={[1, 2]} onPointerMissed={() => { if (mode === 'select') { setSelected(null); setInspectInfo(null); } }}>
        <color attach="background" args={['#14171c']} />
        <hemisphereLight args={['#dfe8f5', '#3a3428', 0.9]} />
        <directionalLight position={[30, 40, 20]} intensity={2.2} />
        <Grid args={[80, 80]} cellSize={1} sectionSize={10} infiniteGrid fadeDistance={box ? Math.max(box.getSize(new Vector3()).length() * 2, 120) : 120} cellColor="#2a3038" sectionColor="#47505c" position={[0, box ? box.min.y - 0.002 : 0.001, 0]} />
        {loaded?.tiles.map((t) => <primitive key={t.manifest.artifact_id + t.sha256} object={t.root} onClick={onPick} onDoubleClick={onDoublePick} />)}
        {helper && <primitive object={helper} />}
        {points.map((p, i) => (
          <mesh key={i} position={p.viewer} renderOrder={10}>
            <sphereGeometry args={[p.markerR ?? 0.03, 16, 12]} />
            <meshBasicMaterial color={p.snapped ? '#7bd88f' : '#ff9a3c'} depthTest={false} />
          </mesh>
        ))}
        {points.length === 2 && <Line points={[points[0].viewer, points[1].viewer]} color="#ff9a3c" lineWidth={2} depthTest={false} renderOrder={9} />}
        <OrbitControls ref={controls} makeDefault enableDamping={false} enableZoom={nav === 'orbit'} enablePan={nav === 'orbit'} />
        <CameraRig controls={controls} frameKey={frameKey} box={focusBox ?? box} viewDir={viewDir} nav={nav} reducedMotion={reducedMotion} collidables={collidables}
          onProject={(f) => { projectRef.current = f; }} onCamera={(p) => { cameraRef.current = p; }} />
        <PointsPicking box={box} />
        <Probe controls={controls} onStats={setStats} />
      </Canvas>

      <aside className="hud" aria-live="polite" data-testid="hud">
        <h1>Scannit viewer</h1>
        <label className="row">Scene{' '}
          <select data-testid="scene-select" value={loaded?.local ? '' : (selectedId ?? '')} onChange={(e) => setSelectedId(e.target.value)} disabled={!scenes.length}>
            {loaded?.local && <option value="">Local preview</option>}
            {scenes.map((s) => <option key={s.id} value={s.id}>{s.label}{s.job ? ` · ${s.job.status}` : ''}</option>)}
          </select>
        </label>
        <p className="muted">Source: {source instanceof Error ? 'misconfigured' : source.kind}{' · '}
          <button type="button" className="link" onClick={() => fileInput.current?.click()}>Open local file…</button>
          <input ref={fileInput} data-testid="local-file" type="file" accept=".glb,.json,model/gltf-binary,application/json" multiple hidden onChange={(e) => { void openLocal(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
        </p>
        {loaded?.local && <div className="badge local" data-testid="local-banner" role="note">LOCAL PREVIEW — not an integrated result</div>}

        {lifecycle && (
          <div className={`lifecycle lc-${lifecycle}`} role="status" data-testid="lifecycle" data-state={lifecycle}>
            <span>{LIFECYCLE_LABEL[lifecycle]}</span>
            {phase.k === 'processing' && phase.entry.job?.progress_pct != null && <progress max={100} value={phase.entry.job.progress_pct} aria-label="Reconstruction progress" />}
            {phase.k === 'artifact-loading' && <progress max={phase.total ?? undefined} value={phase.total ? phase.loaded : undefined} aria-label="Model download progress" />}
          </div>
        )}
        <Status phase={phase} onRetry={() => (phase.k === 'scenes-error' || phase.k === 'no-scenes' ? loadScenes() : setSelectedId((id) => { setTimeout(() => setSelectedId(id), 0); return null; }))} onCancel={() => loadAbort.current?.abort()} />
        {loaded && loaded.expected > 1 && <p data-testid="tiles" className="muted">Tiles {loaded.tiles.length} / {loaded.expected}{loaded.tiles.length < loaded.expected ? ' — loading…' : ''}</p>}

        {primary && (
          <>
            <div data-testid="scale-badge" className={`badge ${validated ? 'ok' : 'warn'}`}>
              {validated ? 'Verified metric scale' : scaleStatus === 'arbitrary' ? 'Arbitrary scale — not metric' : 'Metric scale not validated'}
            </div>
            <dl>
              <dt>Artifact</dt><dd data-testid="artifact-id">{primary.manifest.artifact_id}{loaded!.tiles.length > 1 ? ` +${loaded!.tiles.length - 1}` : ''}</dd>
              <dt>sha256</dt><dd data-testid="artifact-sha" title={primary.sha256}>{primary.sha256.slice(0, 12)}… {primary.verified ? '✓ matches manifest' : 'not verified (no manifest)'}</dd>
              <dt>Origin</dt><dd data-testid="origin" className={primary.manifest.artifact_origin === 'camera' ? '' : 'warnText'}>{primary.manifest.artifact_origin === 'camera' ? 'camera-derived' : primary.manifest.artifact_origin}{primary.manifest.artifact_type ? ` · ${primary.manifest.artifact_type}` : ''}</dd>
              <dt>Source images</dt><dd data-testid="source-images">{primary.sourceImages ?? 'unknown'}</dd>
              <dt>Dataset · job</dt><dd>{primary.manifest.dataset_id ?? '—'} · {primary.manifest.job_id ?? '—'}</dd>
              <dt>Transform</dt><dd data-testid="transform">{primary.manifest.source_frame_id} → {primary.manifest.target_frame_id} (row-major 4×4; GLB frame {primary.manifest.glb_frame_id})</dd>
              <dt>Units</dt><dd>{scaleStatus === 'arbitrary' ? "model units (labelled 'm', scale arbitrary)" : 'meters (as delivered, not rescaled)'}</dd>
              <dt>Geometry</dt><dd>{fmtBytes(loaded!.tiles.reduce((a, t) => a + t.bytes, 0))} · {primary.report.triangleCount ? `${Math.round(loaded!.tiles.reduce((a, t) => a + t.report.triangleCount, 0)).toLocaleString()} triangles` : ''}{primary.report.pointCount ? `${primary.report.pointCount.toLocaleString()} points` : ''}</dd>
              <dt>Target</dt><dd data-testid="cam">{stats.camTarget} {scaleStatus === 'arbitrary' ? 'model units' : 'm'} ({primary.manifest.scene_frame_id})</dd>
              <dt>FPS · draws</dt><dd>{stats.fps} · {stats.calls}</dd>
              <dt>GPU objects</dt><dd data-testid="gpu-mem">{stats.geometries} geometries · {stats.textures} textures</dd>
              <dt>Reference</dt>
              <dd data-testid="scale" className={refOk ? 'ok' : refOk === false ? 'bad' : ''}>
                {primary.report.references.length
                  ? primary.report.references.map((r) => `${r.id} ${r.declared_m.toFixed(3)} m ${r.endpointsOnGeometry ? 'on geometry ✓' : r.endpointsOnGeometry === false ? '✗ off geometry' : 'unchecked'}`).join('; ')
                  : nodeRef?.found ? `${nodeRef.measured_m!.toFixed(3)} m` : 'none in manifest'}
              </dd>
            </dl>
            {notes.length > 0 && <ul className="notes" data-testid="notes">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
            {warnings.length > 0 && <ul className="warnings" data-testid="warnings">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>}

            <div className="tools" role="group" aria-label="Navigation">
              {(['orbit', 'walk', 'fly'] as const).map((n) => (
                <button key={n} type="button" aria-pressed={nav === n} onClick={() => setNavChecked(n)}>{n === 'orbit' ? 'Orbit' : n === 'walk' ? 'Walk (G)' : 'Fly (F)'}</button>
              ))}
              <button type="button" onClick={resetCamera}>Reset (R)</button>
            </div>
            {navMsg && <p className="muted" data-testid="nav-msg" role="status">{navMsg}</p>}
            <label className="muted"><input type="checkbox" data-testid="reduced-motion" checked={reducedMotion} onChange={(e) => setReducedMotion(e.target.checked)} /> Reduced motion (slower moves, no speed boost)</label>
            <div className="tools" role="group" aria-label="Tools">
              <button type="button" aria-pressed={mode === 'measure'} onClick={() => setMode((m) => (m === 'measure' ? 'select' : 'measure'))}>Measure (M)</button>
            </div>
            {mode === 'measure' && (
              <div className="measure" data-testid="measure">
                <label><input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> Snap to vertices</label>
                <p>{points.length === 0 ? 'Click the first point.' : points.length === 1 ? 'Click the second point.' : null}</p>
                {distance !== null && <p className="dist" data-testid="distance">{fmtLength(distance, scaleStatus)}{!validated && <span className="muted"> ({scaleStatus === 'arbitrary' ? 'arbitrary scale — not a physical length' : 'not validated'})</span>}</p>}
                {points.map((p, i) => <p key={i} className="muted">P{i + 1} {fmtVec(p.target)} in {primary.manifest.scene_frame_id}{p.snapped ? ' · snapped' : ''}{p.pointIndex != null ? ` · point #${p.pointIndex}` : ''}</p>)}
              </div>
            )}
            {inspectInfo && mode === 'select' && (
              <section className="inspect" data-testid="inspect" aria-label="Inspect">
                <h2>Inspect</h2>
                <dl>
                  <dt>Node</dt><dd data-testid="inspect-node">{inspectInfo.object}</dd>
                  <dt>Asset</dt><dd>{inspectInfo.artifact_id}</dd>
                  <dt>Position</dt><dd data-testid="inspect-pos">{inspectInfo.scene.map((v) => v.toFixed(3)).join(', ')} {scaleStatus === 'arbitrary' ? 'model units' : 'm'}</dd>
                  {inspectInfo.pointIndex != null && <><dt>Point</dt><dd>#{inspectInfo.pointIndex}</dd></>}
                  <dt>Source images</dt><dd>{inspectInfo.sourceManifestUrl ? <a href={inspectInfo.sourceManifestUrl} target="_blank" rel="noreferrer" data-testid="inspect-source">image manifest</a> : 'not linked'}<span className="muted"> (per-point image links not in manifest)</span></dd>
                </dl>
              </section>
            )}
            <p>
              {primary.artifactUrl && <><a href={primary.artifactUrl} download={`${primary.manifest.artifact_id}.glb`} data-testid="download">Download original GLB</a><span className="muted"> — as produced (sha256 {primary.sha256.slice(0, 8)}…), not a new export</span><br /></>}
              <button type="button" className="link" data-testid="download-metadata" onClick={() => downloadJson(`${primary.manifest.artifact_id.replace(/[^\w.-]+/g, '_')}.metadata.json`, metadataFor(loaded!))}>Download metadata JSON</button>
            </p>
          </>
        )}
        <p className="hint">Drag look/orbit · Walk G (WASD/arrows, Shift) · Fly F (+Q/E) · double-click teleport/re-centre · M measure · Esc clear · R reset</p>
        {primary?.manifest.synthetic && <p className="note">Synthetic scene. Measurements here test the software, not reconstruction accuracy.</p>}
      </aside>
    </div>
  );
}

function Status({ phase, onRetry, onCancel }: { phase: Phase; onRetry: () => void; onCancel: () => void }) {
  const tile = (t?: [number, number]) => (t ? ` (tile ${t[0]} of ${t[1]})` : '');
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
    case 'manifest-loading': return <p data-testid="status">Loading artifact manifest…{tile(phase.tile)}</p>;
    case 'manifest-error': return <div data-testid="status" className="state bad" role="alert">Manifest rejected:<ul>{phase.msg.map((m) => <li key={m}>{m}</li>)}</ul><button type="button" onClick={onRetry}>Retry</button></div>;
    case 'artifact-loading': return <div data-testid="status">Downloading model{tile(phase.tile)}… {fmtBytes(phase.loaded)}{phase.total ? ` of ${fmtBytes(phase.total)}` : ''} <button type="button" onClick={onCancel}>Cancel</button></div>;
    case 'artifact-error': return <div data-testid="status" className="state bad" role="alert">Model not shown ({phase.kind}): {phase.msg} <button type="button" onClick={onRetry}>Retry</button></div>;
  }
}
