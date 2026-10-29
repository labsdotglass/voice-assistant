/**
 * Public surface of a slim Ariakit-style authoring layer (mirroring Ariakit's
 * internals, MIT) used by these primitives.
 */

export type { Hook, HTMLProps, Options, Props, RenderProp, WrapElement } from './types';
export {
  createElement,
  createHook,
  createStoreContext,
  forwardRef,
} from './system';
export {
  useEvent,
  useId,
  useLiveRef,
  useMergeRefs,
  useSafeLayoutEffect,
  useStateRef,
  useUpdateEffect,
} from './hooks';
export { Role } from './role';
export {
  createStore,
  useInitial,
  useStore,
  useStoreProps,
  useStoreState,
  type Store,
  type StoreState,
} from './store';
