/* eslint-disable react-refresh/only-export-components */
import { useEffect, useRef, useState } from 'react';
import { effect, frame, frameLoop, init, sampler, surface, target, type Frame } from 'vgpu';
import { pulseAt, PULSE_PEAK } from './ai-avatar-cadence';
import { cn } from '../lib/utils';
import type { AiAvatarState } from './ai-avatar';
import styles from './ai-avatar.module.css';

/**
 * Cloudglass orb — the internal WebGPU (vgpu / WGSL) renderer behind
 * `AiAvatar`. Two passes, architecture from vgpu's transmission example:
 * pass A renders the original-orb field (AiAvatar blues, morph-in-place
 * clouds, a touch of white, the lavender-pink accent) into an offscreen
 * target; pass B is circular ripple glass — concentric ring lenses shearing
 * that texture radially, with the ring phase driven by the shared 7-beat
 * speech cadence so the glass ripples outward while "talking".
 *
 * This is a verbatim port of the concept POC (`demo/.../lab/orb-vgpu.tsx`)
 * with four adaptations: (A) fills its container instead of a `size` prop,
 * (B) the two blues + two grey stops become palette uniforms read from
 * design tokens, (C) `level` drives ring travel when supplied, (D) a
 * two-effect reduced-motion/appearance design. See the task brief for the
 * exact diffs. Do not redesign the shader or the easing here — this is the
 * approved look; any deliberate visual change belongs in the POC first.
 *
 * The `u`/`gu` uniform-struct variable names below must stay distinct: vgpu
 * uniform-struct variable names alias across effects sharing one gpu
 * context. Also note Vite HMR does not hot-swap GPU pipelines — hard-reload
 * the browser before judging any shader edit.
 */

/** Per-state uniform targets: speaking ribs travel outward on the
 * speech cadence (non-uniform, per-ring speeds); listening mirrors it
 * INWARD, with both the ribs' inward speed and their count driven by the
 * incoming voice (near-silence shows almost none; louder speech pulls
 * more ribs in — see the frame loop); thinking is plain blue at a slower
 * tempo, accent-free; idle
 * goes grey and near-still. `speed` scales an INTEGRATED animation
 * clock, so a state flip eases the tempo without ever snapping the
 * phase. Values ease in the frame loop. */
const STATE_PARAMS: Record<
  AiAvatarState,
  { speed: number; energy: number; ribs: number }
> = {
  // Only MOTION lives here now. Colour is entirely a palette concern:
  // - idle's quiet grey is a grey `--ai-avatar-color-*` for idle in the
  //   stylesheet (not a shader desaturation), so a host can keep a calm orb
  //   coloured (e.g. a green completion orb) instead of it greying out;
  // - the accent is `--ai-avatar-color-2`, gated by its own alpha, so it
  //   shows per state via the palette (opaque in speaking, transparent
  //   elsewhere by default) rather than a hardcoded per-state gate.
  idle: { speed: 0.22, energy: 0.7, ribs: 0 },
  thinking: { speed: 0.55, energy: 1.1, ribs: 0 },
  listening: { speed: 0.9, energy: 0.95, ribs: 0.7 },
  speaking: { speed: 1, energy: 1.2, ribs: 1 },
};

const FRAG_CLOUDS = /* wgsl */ `
struct U { anim: f32, energy: f32, accent: f32, hiAmt: f32, dim: f32,
           body: vec3f, pale: vec3f, accentColor: vec3f, hi: vec3f };
@group(0) @binding(0) var<uniform> u: U;

fn hash(p: vec2f) -> f32 {
  return fract(sin(dot(p, vec2f(127.1, 311.7))) * 43758.5453123);
}
fn vnoise(p: vec2f) -> f32 {
  let i = floor(p);
  let fr = fract(p);
  let s = fr * fr * (3.0 - 2.0 * fr);
  return mix(
    mix(hash(i), hash(i + vec2f(1.0, 0.0)), s.x),
    mix(hash(i + vec2f(0.0, 1.0)), hash(i + vec2f(1.0, 1.0)), s.x),
    s.y);
}
fn fbm(p0: vec2f) -> f32 {
  var v = 0.0;
  var a = 0.5;
  var p = p0;
  for (var i = 0; i < 4; i++) {
    v += a * vnoise(p);
    p = p * 2.03 + vec2f(17.0, 9.0);
    a *= 0.5;
  }
  return v;
}

/* The backdrop is the original-orb field: the AiAvatar blues (blue-9
 * body over a blue-3 cloud bank) with clouds that morph in place —
 * two counter-drifting fbm fields crossfading, with mid-blend contrast
 * re-expansion so the field never goes featureless mid-morph. */
fn field(p: vec2f, t: f32) -> f32 {
  let n1 = fbm(p * 1.4 + vec2f(t * 0.8, -t * 0.5));
  let n2 = fbm(p * 1.9 + vec2f(-t * 0.6, t * 0.9) + vec2f(41.0, 13.0));
  let m = 0.5 + 0.5 * sin(u.anim * 0.45);
  var n = mix(n1, n2, m);
  n = 0.5 + (n - 0.5) * (1.0 + 1.6 * m * (1.0 - m) * 2.0);
  return smoothstep(0.34, 0.72, n);
}

@fragment
fn fs_main(@location(0) uv: vec2f) -> @location(0) vec4f {
  let p = uv;
  let t = u.anim * 0.14;
  /* energy scales amplitude and brightness only — periods stay fixed,
   * so state changes never re-time the running motion. */
  let amp = (0.26 + 0.14 * sin(u.anim * 0.33 + 1.7)) * (0.6 + 0.5 * u.energy);
  let w1 = fbm(p * 1.5 + vec2f(t * 0.7, -t * 0.4));
  let w2 = fbm(p * 1.5 + vec2f(-t * 0.5, t * 0.6) + vec2f(31.0, 47.0));
  let q = p + (vec2f(w1, w2) - 0.5) * amp;
  let vert = smoothstep(0.1, 0.86, q.y);
  let sky = field(q, t);
  let swell = 0.5 + 0.5 * sin(u.anim * 0.27 + q.x * 2.0);
  var lum = clamp(vert * 0.42 + sky * 0.7 + swell * 0.05, 0.0, 1.0);
  /* u.dim scales luminance down: less of the field reaches the light pale
   * colour, so the orb leans on its darker body colour. 1.0 = untouched,
   * which is what the component passes in both appearances today. */
  lum = clamp(lum * (0.82 + 0.22 * u.energy) * u.dim, 0.0, 1.0);
  /* The body/cloud gradient comes straight from the palette — whatever the
   * ai-avatar-color vars resolve to for the current state (blue while active,
   * grey at idle, or a host override such as green on completion). There is
   * no shader desaturation any more: greyness is just a grey palette. */
  let body = u.body;
  let pale = u.pale;
  var col = mix(body, pale, lum);
  let wh = smoothstep(0.85, 1.0, lum) * 0.8 * u.hiAmt;
  /* The crest highlight tracks the cloud colour (pale) lightened toward the
   * mode-tuned hi tint, so it stays in-family with any palette: a near-white
   * cap over the blue clouds while active, a plain light-grey cap over the
   * idle grey, a pale-green cap over a green completion orb — never a stray
   * blue crest on a non-blue orb. */
  let hiCol = mix(pale, u.hi, 0.6);
  col = mix(col, hiCol, wh);
  /* Consumer-defined accent (--ai-avatar-color-2): a slow-orbiting, gently
   * breathing cast whose HUE is the palette's accent colour (u.accentColor)
   * and whose presence is that colour's ALPHA (u.accent) — a transparent
   * color-2 means no accent, the default in every state but speaking. */
  let ac = vec2f(0.5) + vec2f(cos(u.anim * 0.07), sin(u.anim * 0.09)) * 0.28;
  let ad = q - ac;
  /* Breathes: size swells/shrinks ~40% on the integrated clock, so idle
   * nearly freezes it. The 4x on the base is a ~2x radius bump (asz is the
   * Gaussian variance, so radius scales with its square root) — a large,
   * soft accent cast. */
  let asz = 0.045 * 4.0 * (0.7 + 0.6 * (0.5 + 0.5 * sin(u.anim * 0.15 + 1.0)));
  let accent = exp(-dot(ad, ad) / asz);
  /* Mix toward the accent hue, gated out by cloud-whiteness so it never
   * dirties the whites, and by the accent alpha so a transparent accent is
   * fully absent. */
  let aMix = accent * 0.55 * u.accent * (1.0 - wh);
  col = mix(col, u.accentColor, aMix);
  return vec4f(col, 1.0);
}
`;

const FRAG_GLASS = /* wgsl */ `
struct GU { time: f32, ring_phase: f32, ribs: f32 };
@group(0) @binding(0) var<uniform> gu: GU;
@group(0) @binding(1) var scene_tex: texture_2d<f32>;
@group(0) @binding(2) var scene_samp: sampler;

fn hash(p: vec2f) -> f32 {
  return fract(sin(dot(p, vec2f(127.1, 311.7))) * 43758.5453123);
}

@fragment
fn fs_main(@location(0) uv: vec2f) -> @location(0) vec4f {
  let p = uv;
  let d = distance(p, vec2f(0.5));
  let mask = 1.0 - smoothstep(0.496, 0.5, d);

  /* Circular ribs — concentric ripple-glass rings around the center.
   * Each ring is a half-torus lens: the RADIUS is remapped (compressed
   * toward the ring center + Snell bend), shearing the backdrop along
   * circles. No early return — textureSample needs uniform control
   * flow. */
  let rd = p - vec2f(0.5);
  let r = length(rd);
  let dir = rd / max(r, 0.0001);
  /* ring_phase travels the rings outward — it advances with the speech
   * cadence (see the frame loop), so the glass ripples while "talking"
   * and rests between beats. */
  let K = 14.0;
  /* Non-uniform travel: each ring moves at its own speed (hashed from a
   * first-pass ring index), so the ripples slide past one another
   * instead of marching in lockstep. The index estimate can disagree
   * with the final one for a pixel right at a boundary — a brief
   * shimmer that reads as glass, not as a defect. */
  let i0 = floor(r * K - gu.ring_phase);
  let sf = 0.6 + 0.8 * hash(vec2f(i0, 1.3));
  let phase = gu.ring_phase * sf;
  let ring = r * K - phase;
  let cell = (floor(ring) + 0.5 + phase) / K;
  let c = fract(ring) - 0.5;
  let nx = c * 2.0;
  let arc = sqrt(max(1.0 - nx * nx, 0.0));
  let bend = (nx - nx / 1.52) * arc;
  /* gu.ribs fades the glass in and out per state, and the edge mask
   * confines it to the outer third — the inner two thirds of the radius
   * stay clean glassless field. */
  let edgeMask = smoothstep(0.31, 0.42, r);
  let ribAmt = gu.ribs * edgeMask;
  let rs = mix(r, cell + c / K * 0.2 + bend * 0.45, ribAmt);

  /* Frosted sampling: five radial taps across the ring. */
  let f0 = 0.004 * ribAmt;
  let center = vec2f(0.5);
  var col = textureSample(scene_tex, scene_samp, center + dir * rs).rgb * 0.34
          + textureSample(scene_tex, scene_samp, center + dir * (rs - f0)).rgb * 0.22
          + textureSample(scene_tex, scene_samp, center + dir * (rs + f0)).rgb * 0.22
          + textureSample(scene_tex, scene_samp, center + dir * (rs - 2.0 * f0)).rgb * 0.11
          + textureSample(scene_tex, scene_samp, center + dir * (rs + 2.0 * f0)).rgb * 0.11;

  /* No painted flute lines and no rim shading: the ribs read purely
   * through the refraction discontinuities. */
  let g = hash(floor(p * 640.0) + vec2f(floor(gu.time * 8.0))) - 0.5;
  col += g * 0.05;

  return vec4f(col * mask, mask);
}
`;

/** `true` when the WebGPU entry point exists (SSR-safe: `navigator` may be
 * absent server-side). */
export function hasWebGPU(): boolean {
  return typeof navigator !== 'undefined' && 'gpu' in navigator;
}

type Rgb = [number, number, number];
type Rgba = [number, number, number, number];
let scratch: CanvasRenderingContext2D | null = null;
function colorToRgba(color: string, fallback: Rgba): Rgba {
  scratch ??= document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  if (!scratch || !color) return fallback;
  scratch.clearRect(0, 0, 1, 1);
  scratch.fillStyle = color;
  scratch.fillRect(0, 0, 1, 1);
  const d = scratch.getImageData(0, 0, 1, 1).data;
  return [d[0], d[1], d[2], d[3] / 255];
}
function colorToRgb(color: string, fallback: Rgb): Rgb {
  const [r, g, b] = colorToRgba(color, [...fallback, 1]);
  return [r, g, b];
}
/** WGSL wants 0..1 linear-ish sRGB floats; the shader treated its literals
 * as plain 0..1, so divide the readback by 255 (no gamma conversion — match
 * the POC's constants, which were authored in this same 0..1 space). */
function readPalette(el: Element) {
  const cs = getComputedStyle(el);
  const get = (name: string, fb: Rgb): [number, number, number] => {
    const [r, g, b] = colorToRgb(cs.getPropertyValue(name).trim(), fb);
    return [r / 255, g / 255, b / 255];
  };
  // The accent (color-2) carries an ALPHA: it gates the accent in-shader, so a
  // `transparent` color-2 means "no accent" for this state (the default in
  // every state but speaking). rgb is normalised to 0..1; alpha stays 0..1.
  const getAccent = (name: string): { rgb: Rgb; a: number } => {
    const [r, g, b, a] = colorToRgba(cs.getPropertyValue(name).trim(), [0, 0, 0, 0]);
    return { rgb: [r / 255, g / 255, b / 255], a };
  };
  // The palette never branches on the theme here — it is read from the CSS
  // vars below, which carry the component's own dark-mode values. `dim` is
  // the exception: it is a shader constant with no CSS counterpart.
  const dark = typeof document !== 'undefined'
    && document.documentElement.classList.contains('dark');
  return {
    // Match the CSS orb: read ITS resolved palette vars, not the raw blue
    // tokens. Those vars carry the component's own dark-mode pin (the pale
    // cloud, the only role that inverts with the ramps) as well as any
    // per-instance --ai-avatar-color-* override, so reading them — rather
    // than branching on the theme here — keeps both renderers in step.
    // Fallbacks are the default tokens.
    body: get('--ai-avatar-color-1', [0, 144, 255]),
    pale: get('--ai-avatar-color-3', [230, 244, 254]),
    accent: getAccent('--ai-avatar-color-2'),
    // The cloud-crest highlight is hand-tuned (not a token): near-white reads
    // as sun-on-cloud over the pale cloud colour, which the component keeps
    // pale in both appearances — so this needs no per-mode value.
    hi: [0.97, 0.98, 1.0] as [number, number, number],
    hiAmt: 1.0,
    // Field strength: below 1.0 less of the field reaches the pale cloud, so
    // the orb leans on its darker body colour. Light runs neutral — it is
    // meant to reach its cloud at full strength. Dark takes a light touch off
    // the top so the orb settles into the surface rather than glowing off it.
    // NB dark idle carries its own, steeper version of this in the PALETTE
    // (see ai-avatar.module.css) — the two compound there.
    dim: dark ? 0.9 : 1.0,
  };
}

/** A tiny local `prefers-reduced-motion` hook — no shared one exists yet
 * under `src/lib/`. SSR-safe: defaults to `false` when `window` is absent.
 * Mirrors `useAppearance()` (`src/lib/use-appearance.ts`): state, not a ref,
 * so an OS-level flip while mounted triggers a re-render (and, downstream,
 * Effect A restarting) instead of going unnoticed until an unrelated
 * re-render happens to read a stale ref. */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window !== 'undefined'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => {
      setReduced(mql.matches);
    };
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);
  return reduced;
}

/** Bumps whenever `document.documentElement`'s `class` attribute changes —
 * i.e. whenever the applied theme flips, whether that's a programmatic
 * `.dark` toggle from ANY `useAppearance()` instance (including one in a
 * different component tree than this orb's) or an OS `prefers-color-scheme`
 * change relayed through the app's own listener. A per-instance
 * `useAppearance()` here would miss both: its `appearance` state only
 * updates via that instance's own setter or its own mql listener (which
 * bails when a stored choice exists), so a sibling instance's toggle would
 * never reach it. Observing the actually-applied class is the only way to
 * catch every path that can change it. SSR-safe: never fires without a
 * `document`. */
function useThemeVersion(): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (typeof MutationObserver === 'undefined' || typeof document === 'undefined') return;
    const el = document.documentElement;
    const obs = new MutationObserver(() => setV((n) => n + 1));
    obs.observe(el, { attributes: true, attributeFilter: ['class'] });
    return () => obs.disconnect();
  }, []);
  return v;
}

export interface AiAvatarGlProps {
  state: AiAvatarState;
  /** Clamped 0–1 amplitude, or undefined to run the built-in cadence. */
  level?: number;
  /** Called once if WebGPU init fails, so the caller can fall back to CSS. */
  onError: () => void;
  className?: string;
}

/**
 * Internal WebGPU cloudglass renderer behind `AiAvatar`. Fills its
 * container; owns its GPU lifecycle end to end. Renders one decorative
 * `<canvas>` and reports init failure through `onError` rather than
 * rendering a fallback itself — the caller swaps to the CSS renderer.
 */
export const AiAvatarGl: React.FC<AiAvatarGlProps> = ({ state, level, onError, className }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const themeVersion = useThemeVersion();
  const reduced = usePrefersReducedMotion();

  // Bumps whenever the orb's own root element changes its `class` — i.e. a host
  // re-palettes THIS instance by swapping a theming class (the CSS custom
  // properties are inherited into the canvas, so the shader must re-read them).
  // documentElement theme flips are covered by useThemeVersion; the orb's own
  // conversational state by the `state` dep below. `class` only (not `style`):
  // AiAvatar writes --ai-avatar-level to the root's inline style every driven
  // frame, and observing `style` would fire the re-read loop on every one.
  const [paletteEpoch, setPaletteEpoch] = useState(0);
  useEffect(() => {
    const el = canvasRef.current?.parentElement;
    if (!el || typeof MutationObserver === 'undefined') return undefined;
    const obs = new MutationObserver(() => setPaletteEpoch((n) => n + 1));
    obs.observe(el, { attributes: true, attributeFilter: ['class'] });
    return () => obs.disconnect();
  }, []);

  // The frame loop (and the static reduced-motion redraw) read state/level
  // through refs so a change never tears down the GPU pipeline — it only
  // changes easing targets or triggers one static redraw.
  const stateRef = useRef(state);
  const levelRef = useRef(level);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  useEffect(() => {
    levelRef.current = level;
  }, [level]);

  const drawStaticRef = useRef<(() => void) | null>(null);
  // A performance.now() timestamp until which the palette should be re-read
  // each frame — opened by the themeVersion effect to track the CSS palette
  // transition after a theme flip (see below). And a hook for the
  // reduced-motion path (which has no frame loop) to re-read + redraw.
  const palDirtyUntilRef = useRef(0);
  const refreshStaticRef = useRef<(() => void) | null>(null);

  // Effect A: init the gpu pipeline. Restarts only on a reduced-motion flip
  // (loop vs. single-frame). Theme changes do NOT restart it — the palette
  // is re-read live instead (see the themeVersion effect), so the pipeline
  // is never torn down and rebuilt on a theme toggle.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !hasWebGPU()) {
      onError();
      return;
    }
    let disposed = false;
    // init() is async: keep the StrictMode contract by disposing through
    // the pending promise (cf. vgpu's own example scaffold).
    const pending = (async () => {
      const gpu = await init();
      try {
        if (disposed) {
          gpu.dispose();
          return null;
        }
        const output = surface(gpu, canvas, { dpr: [1, 2] });
        const start = performance.now();
        const scene = target(gpu, { size: output.size });
        const samp = sampler(gpu, {
          minFilter: 'linear',
          magFilter: 'linear',
          addressModeU: 'clamp-to-edge',
          addressModeV: 'clamp-to-edge',
        });
        const clouds = effect(gpu, FRAG_CLOUDS);
        const glass = effect(gpu, FRAG_GLASS);
        glass.set({ scene_tex: scene, scene_samp: samp });
        // Mutable so a theme flip can refresh it in place (see palDirtyUntil
        // in the loop and refreshStaticRef) without rebuilding the pipeline.
        let pal = readPalette(canvas);

        // The accent's presence is its colour's ALPHA. We ease that alpha here
        // in the shader rather than via a CSS transition on --ai-avatar-color-2:
        // a registered <color> custom property won't interpolate its alpha out
        // of a (near-)transparent value across gamuts, so the CSS var snaps and
        // this eased copy carries the fade. The accent HUE stays put (the "off"
        // default keeps the hue at ~0.01 alpha), so the accent never fades
        // through black.
        let easedAccentA = pal.accent.a;

        // Eased state params: ~260ms for tempo/energy/ribs (the
        // original's state transition), a slower ~600ms for the accent
        // fade — the original's flicker fix. The palette itself (body /
        // cloud colour) now eases via the CSS custom-property transition,
        // re-read each frame while the palette-dirty window is open (a
        // state change opens it; see below).
        const cur = { ...STATE_PARAMS[stateRef.current] };

        // Shared by both the animated loop and the reduced-motion single
        // frame, so the two paths can't drift: uploads uniforms, then
        // encodes both passes. `f` is optional so the reduced-motion call
        // site (outside any frame) can open its own, while the animated
        // loop encodes into the frame `frameLoop` already opened for it —
        // frames are not reentrant, so it must not open a second one.
        const drawPasses = (
          anim: number,
          ringPhase: number,
          time: number,
          ribsAmt: number,
          f?: Frame,
        ) => {
          clouds.set({
            u: {
              anim,
              energy: cur.energy,
              accent: easedAccentA,
              body: pal.body,
              pale: pal.pale,
              accentColor: pal.accent.rgb,
              hi: pal.hi,
              hiAmt: pal.hiAmt,
              dim: pal.dim,
            },
          });
          glass.set({ gu: { time, ring_phase: ringPhase, ribs: ribsAmt } });
          const encode = (frameArg: Frame) => {
            frameArg.pass({ target: scene }, (pass) => pass.draw(clouds));
            frameArg.pass(output, glass);
          };
          if (f) {
            encode(f);
          } else {
            frame(gpu, encode);
          }
        };

        const drawStatic = () => {
          const tgt = STATE_PARAMS[stateRef.current];
          cur.speed = tgt.speed;
          cur.energy = tgt.energy;
          cur.ribs = tgt.ribs;
          easedAccentA = pal.accent.a; // reduced motion: no easing, snap
          drawPasses(0, 0, 0, cur.ribs);
        };
        drawStaticRef.current = drawStatic;
        // Reduced motion has no frame loop, so the themeVersion effect drives
        // catch-up redraws through this: re-read the palette, then repaint.
        refreshStaticRef.current = () => {
          pal = readPalette(canvas);
          drawStatic();
        };

        if (reduced) {
          drawStatic();
        } else {
          // Speaking makes the rings travel: the shared 7-beat speech
          // cadence (ai-avatar-cadence) drives the ring phase velocity, so
          // the glass ripples outward on each beat and rests between
          // words. When `level` is supplied it drives ring travel instead
          // of the cadence — level === 0 holds the rings still.
          let ringPhase = 0;
          let last = start;
          let anim = 0;
          // Smoothed listening activity (0..1): gates how many ribs show, so
          // the count swells with the voice and settles toward silence
          // instead of strobing with each syllable.
          let listenEnv = 0;
          frameLoop(gpu, (frameArg) => {
            const now = performance.now();
            // Track the CSS palette transition after a theme flip: re-read
            // the tokens each frame while the window (opened by the
            // themeVersion effect) is open, so the orb fades in step with
            // the CSS orbs instead of freezing on the pre-transition colors.
            if (now < palDirtyUntilRef.current) pal = readPalette(canvas);
            const time = ((now - start) / 1000) % 3600;
            const dt = Math.min((now - last) / 1000, 0.1);
            last = now;
            const tgt = STATE_PARAMS[stateRef.current];
            const kFast = 1 - Math.exp(-dt / 0.26);
            const kSlow = 1 - Math.exp(-dt / 0.6);
            cur.speed += (tgt.speed - cur.speed) * kFast;
            cur.energy += (tgt.energy - cur.energy) * kFast;
            cur.ribs += (tgt.ribs - cur.ribs) * kFast;
            // Accent presence eases on the slower ~600ms curve (see easedAccentA).
            easedAccentA += (pal.accent.a - easedAccentA) * kSlow;
            // The integrated clock: tempo changes ease, phase never jumps.
            anim = (anim + dt * cur.speed) % 3600;
            // Ring velocity + rib count by state. `beat` is the raw pulse
            // (0..PULSE_PEAK) or the driven level; `activity` is the same
            // signal normalized to 0..1 for the count envelope.
            const st = stateRef.current;
            const beat = levelRef.current != null ? levelRef.current : pulseAt(time);
            const activity = levelRef.current != null
              ? levelRef.current
              : pulseAt(time) / PULSE_PEAK;
            listenEnv += (activity - listenEnv) * (1 - Math.exp(-dt / 0.2));
            let ringVel = 0;
            let ribsOut = cur.ribs;
            if (st === 'speaking') {
              // Ribs ripple OUTWARD, pushed on each beat, resting between
              // words.
              ringVel = beat * 8;
            } else if (st === 'listening') {
              // Ribs travel INWARD, drawing the voice in — a mirror of
              // speaking. Both speed and count track the incoming voice:
              // near-silence pulls almost no ribs, active speech pulls a
              // full set inward. A small floor keeps a hint of glass alive.
              ringVel = -beat * 8;
              ribsOut = cur.ribs * (0.1 + 0.9 * listenEnv);
            }
            // Everything scales by the eased ribs amount, so it fades with
            // the glass on a state change.
            ringPhase = (((ringPhase + ringVel * dt * cur.ribs) % 100) + 100) % 100;
            drawPasses(anim, ringPhase, time, ribsOut, frameArg);
          });
        }
        return gpu;
      } catch (error) {
        gpu.dispose();
        throw error;
      }
    })().catch((error) => {
      console.error('AiAvatarGl: WebGPU init failed', error);
      // A disposed init means either StrictMode's double-invoke cleanup
      // already ran, or this run was superseded by a newer one (a
      // reduced-motion flip re-triggered Effect A before this one settled) —
      // either way the component may be unmounted or a later init may still
      // succeed, so don't call onError() and don't latch a permanent
      // failure over it.
      if (disposed) return null;
      onError();
      return null;
    });
    return () => {
      disposed = true;
      drawStaticRef.current = null;
      refreshStaticRef.current = null;
      void pending.then((gpu) => gpu?.dispose());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reduced]);

  // The CSS palette vars (--ai-avatar-color-*) TRANSITION over ~600ms whenever
  // the resolved palette changes: a theme flip (themeVersion), a state change
  // (state — idle greys the palette, active un-greys it, and a completion
  // palette rides in on the same change), or a host re-paletting this instance
  // by swapping a theming class (paletteEpoch). At the moment of the change the
  // computed value is still the pre-transition colour, so a single re-read
  // would freeze the orb on the stale colour. Instead open a re-read window
  // that spans the transition: the animation loop polls
  // palDirtyUntilRef itself; the reduced-motion path (no loop) gets driven
  // catch-up redraws so it still lands on the settled palette.
  const firstPalRun = useRef(true);
  useEffect(() => {
    // Initial mount: Effect A already reads the palette when the loop starts,
    // and there is no transition in flight to track.
    if (firstPalRun.current) {
      firstPalRun.current = false;
      return;
    }
    palDirtyUntilRef.current = performance.now() + 700;
    if (!reduced) return;
    let raf = 0;
    const start = performance.now();
    const tick = () => {
      refreshStaticRef.current?.();
      if (performance.now() - start < 700) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [themeVersion, state, paletteEpoch, reduced]);

  // Effect B: under reduced motion, redraw one static frame when state
  // changes (the animating path never tears down for this). `drawStatic`
  // always snaps to STATE_PARAMS and ignores `level` (anim/ringPhase/time
  // are pinned to 0), so `level` is deliberately not a dep here — including
  // it would redraw an identical frame on every envelope tick while
  // reduced motion is active.
  useEffect(() => {
    if (!reduced) return;
    drawStaticRef.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  return <canvas className={cn(styles.canvas, className)} aria-hidden ref={canvasRef} />;
};
