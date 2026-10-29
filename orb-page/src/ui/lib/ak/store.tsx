/**
 * A tiny observable store + the React bindings used to read it.
 *
 * Subset of Ariakit's store layer: enough for our local primitives
 * (a Map of slots, listeners per slot via Set, subscribe + setState).
 * Future Ariakit features we'd add only if a primitive needs them:
 *   - sub-store sync (`disclosure` chaining)
 *   - batched updates, useSyncExternalStore
 *   - throwOnReadOnly invariants
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSafeLayoutEffect } from './hooks';

export type StoreState = Record<string, any>;

export interface Store<S extends StoreState = StoreState> {
  getState: () => S;
  setState: <K extends keyof S>(key: K, value: S[K] | ((prev: S[K]) => S[K])) => void;
  subscribe: (listener: (state: S) => void) => () => void;
  /** Subscribe to a single slot. The listener fires only when that slot changes. */
  subscribeKey: <K extends keyof S>(
    key: K,
    listener: (value: S[K]) => void,
  ) => () => void;
}

/**
 * Create a vanilla store. Listeners are notified per-slot via `subscribeKey`
 * and on any change via `subscribe`. Setting a slot to its current value is a
 * no-op.
 */
export function createStore<S extends StoreState>(initial: S): Store<S> {
  let state: S = { ...initial };
  const globalListeners = new Set<(state: S) => void>();
  const keyListeners = new Map<keyof S, Set<(value: any) => void>>();

  return {
    getState: () => state,

    setState(key, value) {
      const prev = state[key];
      const next = typeof value === 'function'
        ? (value as (p: any) => any)(prev)
        : value;
      if (prev === next) return;
      state = { ...state, [key]: next };
      keyListeners.get(key)?.forEach((listener) => listener(next));
      globalListeners.forEach((listener) => listener(state));
    },

    subscribe(listener) {
      globalListeners.add(listener);
      return () => {
        globalListeners.delete(listener);
      };
    },

    subscribeKey(key, listener) {
      let bucket = keyListeners.get(key);
      if (!bucket) {
        bucket = new Set();
        keyListeners.set(key, bucket);
      }
      bucket.add(listener);
      return () => {
        bucket?.delete(listener);
        if (bucket && bucket.size === 0) keyListeners.delete(key);
      };
    },
  };
}

/**
 * Create a store inside a component and keep its reference stable across
 * renders. Returns the store. Use `useStoreProps` to sync controlled-prop
 * inputs back into the store.
 */
export function useStore<S extends StoreState>(
  initialize: () => S,
): Store<S> {
  const storeRef = useRef<Store<S>>(undefined as unknown as Store<S>);
  if (!storeRef.current) {
    storeRef.current = createStore(initialize());
  }
  return storeRef.current;
}

/**
 * Read a single slot from the store and re-render on changes to that slot.
 */
export function useStoreState<S extends StoreState, K extends keyof S>(
  store: Store<S> | null | undefined,
  key: K,
): S[K] | undefined {
  const [value, setValue] = useState<S[K] | undefined>(() => store?.getState()[key]);
  useSafeLayoutEffect(() => {
    if (!store) return;
    setValue(store.getState()[key]);
    return store.subscribeKey(key, setValue);
  }, [store, key]);
  return value;
}

/**
 * Mirror a controlled prop into the store: whenever the prop changes,
 * write it back into the store under `key`.
 */
export function useStoreProps<S extends StoreState, K extends keyof S>(
  store: Store<S>,
  props: Partial<S>,
  key: K,
): void {
  const value = props[key];
  useEffect(() => {
    if (value === undefined) return;
    store.setState(key, value as S[K]);
  }, [store, key, value]);
}

/**
 * Memoise a value to feed into `useStore` initial state.
 */
export function useInitial<T>(value: T): T {
  return useMemo(() => value, []);
}
