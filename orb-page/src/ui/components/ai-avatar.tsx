/* eslint-disable react-refresh/only-export-components */
import {
  createElement as createReactElement,
  isValidElement,
  useState,
  type CSSProperties,
  type ElementType,
  type ReactNode,
} from 'react';
import {
  createElement,
  createHook,
  forwardRef,
  type Options,
  type Props,
} from '../lib/ak';
import { cn } from '../lib/utils';
import { AiAvatarGl, hasWebGPU } from './ai-avatar-gl';
import styles from './ai-avatar.module.css';

const TagName = 'span' satisfies ElementType;
type TagName = typeof TagName;

/** Activity states, in increasing energy. */
export type AiAvatarState = 'idle' | 'thinking' | 'listening' | 'speaking';

export interface AiAvatarOptions extends Options {
  /** Activity state; drives animation speed and intensity. @default 'idle' */
  state?: AiAvatarState;
  /** @deprecated No-op on the WebGPU (cloudglass) renderer; still honored by
   * the CSS fallback. Number of gradient blobs composing the field, clamped
   * to 0–4 (each blob has its own hand-tuned motion recipe). Blob 3 is the
   * color-cycling accent — the sole consumer of `--ai-avatar-color-2` —
   * so lower counts drop the accent first. @default 4
   * Kept only for the CSS fallback, so it goes when the fallback does. */
  blobs?: number;
  /** Accessible name. When set, the orb is exposed to AT (`role="img"`);
   * otherwise it is decorative (`aria-hidden`). */
  label?: string;
  /** Live amplitude for this frame, 0–1 (clamped). When supplied, the
   * orb's motion is driven by this value and the built-in speech cadence
   * stands down — pass a real audio envelope to make the orb pause when
   * the speech pauses. Omit it to keep the synthetic cadence. */
  level?: number;
  /** Which renderer to use. 'auto' prefers the WebGPU cloudglass orb and
   * falls back to the CSS orb when WebGPU is unavailable; 'css' forces the
   * CSS orb. @default 'auto' */
  renderer?: 'auto' | 'css';
}

export type AiAvatarProps<T extends ElementType = TagName> = Props<T, AiAvatarOptions>;

/**
 * `useAiAvatar` — props hook for the AI presence orb. Zero behavior, so no
 * store (cf. Ariakit's Separator/VisuallyHidden): the hook only composes
 * the internal gradient layers and the state/aria attributes.
 */
export const useAiAvatar = createHook<TagName, AiAvatarOptions>(
  function useAiAvatar({ state = 'idle', blobs = 4, label, level, renderer = 'auto', ...props }) {
    // Only four recipes exist; a fifth blob would retrace blob 0's orbit.
    const blobCount = Math.min(Math.max(Math.trunc(blobs), 0), 4);

    // ak render semantics let a render element's children REPLACE the
    // composed ones — here that would drop every visual layer (the root
    // paints nothing itself) and leave an invisible orb. Lift a render
    // element's children out and layer them over the orb instead.
    let render = props.render;
    let overlay: ReactNode = null;
    if (isValidElement<{ children?: ReactNode }>(render) && render.props.children != null) {
      const { children: lifted, ...renderProps } = render.props;
      overlay = lifted;
      render = createReactElement(render.type, {
        ...renderProps,
        key: render.key ?? undefined,
      });
    }

    // `level == null` (not falsy): level={0} is a real driven value —
    // silence must hold the orb still, not hand it back to the cadence.
    const driven = level != null;
    const clamped = driven ? Math.min(Math.max(level, 0), 1) : undefined;

    // GL when allowed, available, and not yet failed. glFailed latches a
    // runtime init failure so the next render paints the CSS fallback.
    const [glFailed, setGlFailed] = useState(false);
    const useGl = renderer !== 'css' && hasWebGPU() && !glFailed;

    const cssLayers = (
      // The grain lives inside the sphere's clip but counter-scales
      // against the breathe pulse, so the noise never scales on screen.
      <span className={styles.sphere}>
        <span className={styles.field}>
        {Array.from({ length: blobCount }, (_, i) => (
          // The mover carries the fast `speaking` churn (and the blend
          // mode — see the CSS); the blob inside carries its own orbit
          // loop. Nested transforms multiply, so the two motions compose
          // without `animation-composition`.
          <span
            key={i}
            className={styles.mover}
            style={{ '--blob-i': i } as CSSProperties}
          >
            <span className={styles.blob} data-blob={i} />
          </span>
        ))}
        </span>
        <span className={styles.grain} />
      </span>
    );

    const children = (
      <>
        {useGl
          ? (
            <AiAvatarGl
              state={state}
              level={clamped}
              onError={() => setGlFailed(true)}
            />
            )
          : cssLayers}
        {props.children}
        {overlay}
      </>
    );

    return {
      'data-activity': state,
      'data-driven': driven ? '' : undefined,
      // Decorative unless a label is supplied; then expose as an image role.
      'aria-hidden': label ? undefined : true,
      role: label ? 'img' : undefined,
      'aria-label': label,
      ...props,
      render,
      className: cn(styles.root, props.className),
      style: driven
        ? { ...props.style, '--ai-avatar-level': clamped } as CSSProperties
        : props.style,
      children,
    };
  },
);

/**
 * Decorative "AI presence" orb: a circle of slowly drifting gradient
 * clouds. Fills its container (`width: 100%`, always square) — size it
 * from the outside, from a 24px chat chip to a full-screen speaking view.
 *
 * State is a plain prop by design: the app already owns streaming state.
 * The component never announces state changes (no live region) — pair it
 * with textual status in the consuming UI.
 */
export const AiAvatar = forwardRef(function AiAvatar(props: AiAvatarProps) {
  const htmlProps = useAiAvatar(props);
  return createElement(TagName, htmlProps);
});
