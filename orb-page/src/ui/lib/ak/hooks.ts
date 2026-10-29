/**
 * The slim subset of Ariakit's React hooks that our primitives need.
 * Same names and behaviour as `@ariakit/react-core/utils/hooks`.
 */

import {
  useCallback,
  useEffect,
  useId as useReactId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DependencyList,
  type EffectCallback,
  type Ref,
} from 'react';
import { setRef } from './misc';

const canUseDOM = typeof window !== 'undefined' && typeof document !== 'undefined';

export const useSafeLayoutEffect = canUseDOM ? useLayoutEffect : useEffect;

/**
 * Stable function reference that always sees the latest closure. Use for
 * event handlers stored on a store/context where identity must not change
 * but you still want fresh state.
 */
export function useEvent<T extends (...args: any[]) => any>(callback?: T): T {
  const ref = useRef<T | undefined>(callback);
  useSafeLayoutEffect(() => {
    ref.current = callback;
  });
  return useCallback(
    ((...args: any[]) => ref.current?.(...args)) as T,
    [],
  );
}

/**
 * Merge multiple refs (callback or object) into a single ref callback.
 */
export function useMergeRefs(
  ...refs: Array<Ref<unknown> | undefined>
): (value: unknown) => void {
  return useMemo(
    () => {
      if (refs.every((r) => r == null)) return () => {};
      return (value: unknown) => {
        for (const ref of refs) {
          if (ref) setRef(ref, value);
        }
      };
    },
    refs,
  );
}

/**
 * Stable id. Falls back to React's built-in `useId` when no default is
 * given; honours an explicit `defaultId` so consumers can supply one.
 */
export function useId(defaultId?: string): string | undefined {
  const reactId = useReactId();
  return defaultId ?? reactId;
}

/**
 * `useEffect` that skips the first render — runs only on subsequent updates.
 */
export function useUpdateEffect(effect: EffectCallback, deps?: DependencyList): void {
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    return effect();
  }, deps);
}

/**
 * Hold the latest value of `value` in a mutable ref, refreshed in a layout
 * effect. Lets stable callbacks (from `useEvent`) read the latest closure
 * without re-creating themselves.
 */
export function useLiveRef<T>(value: T): { readonly current: T } {
  const ref = useRef(value);
  useSafeLayoutEffect(() => {
    ref.current = value;
  });
  return ref;
}

/**
 * Stable state setter that also mirrors into a ref so effects can read it
 * without subscribing.
 */
export function useStateRef<T>(initial: T): [T, (next: T) => void, { readonly current: T }] {
  const [value, setValue] = useState(initial);
  const ref = useRef(value);
  ref.current = value;
  return [value, setValue, ref];
}
