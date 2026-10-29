/**
 * The Ariakit-style authoring scaffolding for our local primitives.
 *
 *   - `forwardRef`: same shape as `React.forwardRef` but passes `ref` as
 *     a prop, so `useXxx` hooks can read/merge it just like any other prop.
 *   - `createElement`: renders an element with `render`-prop composition
 *     support (Ariakit's idiom; not Radix's `asChild`).
 *   - `createHook`: type-tags a `useProps`-style function so it satisfies
 *     the `Hook<TagName, Options>` signature.
 *   - `createStoreContext`: paired React contexts (regular + scoped) for
 *     a store, so sub-components can find each other.
 */

import * as React from 'react';
import { useMergeRefs } from './hooks';
import { getRefProperty, mergeProps } from './misc';
import type { Store } from './store';
import type { Hook, HTMLProps, Options, Props } from './types';

/**
 * `React.forwardRef` that passes `ref` along as a prop. Lets our `useXxx`
 * hooks merge refs uniformly via `useMergeRefs`.
 */
export function forwardRef<T extends React.FC<any>>(render: T): T {
  const Role = React.forwardRef(
    // @ts-expect-error React 19's ReactNode includes Promise; Ariakit suppresses this too.
    (props, ref) => render({ ...props, ref }),
  );
  Role.displayName = render.displayName || render.name;
  return Role as unknown as T;
}

/**
 * Render an element honoring the `render` prop:
 *   - `render` is a React element → cloneElement, merging the primitive's
 *     props onto it.
 *   - `render` is a function → call with merged props.
 *   - no `render` → fall back to `Type`.
 */
export function createElement(
  Type: React.ElementType,
  props: Props<React.ElementType, Options>,
): React.ReactElement {
  const { wrapElement, render, ...rest } = props as Props<React.ElementType, Options> & {
    ref?: React.Ref<unknown>;
  };
  const mergedRef = useMergeRefs(
    (rest as { ref?: React.Ref<unknown> }).ref,
    getRefProperty(render) ?? undefined,
  );

  let element: React.ReactElement;

  if (React.isValidElement(render)) {
    const r = render as React.ReactElement<React.HTMLAttributes<HTMLElement>>;
    const renderProps = {
      ...(r.props as object),
      ref: mergedRef,
    };
    element = React.cloneElement(
      r,
      mergeProps(rest as React.HTMLAttributes<HTMLElement>, renderProps as React.HTMLAttributes<HTMLElement>),
    );
  } else if (typeof render === 'function') {
    element = render({ ...(rest as object), ref: mergedRef } as React.HTMLAttributes<HTMLElement> & {
      ref?: React.Ref<unknown>;
    }) as React.ReactElement;
  } else {
    element = React.createElement(Type, { ...(rest as object), ref: mergedRef } as any);
  }

  if (wrapElement) return wrapElement(element);
  return element;
}

/**
 * Tag a `useProps` function with the `Hook<TagName, Options>` signature.
 * Pattern in component files:
 *
 *   const useScrollAreaThumb = createHook<TagName, ScrollAreaThumbOptions>(
 *     function useScrollAreaThumb(props) { … return props; }
 *   );
 */
export function createHook<
  T extends React.ElementType,
  P extends Record<string, any> = Record<string, never>,
>(useProps: (props: Props<T, P>) => HTMLProps<T, P>): Hook<T, P> {
  const useRole = (props: Props<T, P> = {} as Props<T, P>) => useProps(props);
  (useRole as { displayName?: string }).displayName = useProps.name;
  return useRole as Hook<T, P>;
}

type StoreProvider<T extends Store<any>> = React.ComponentType<{
  value: T | undefined;
  children?: React.ReactNode;
}>;

/**
 * Paired React contexts for a store. `useContext` returns the value if a
 * provider is present; `useProviderContext` returns the value only when
 * not the same as the scoped context (used to detect "are we inside our
 * own provider, or someone else's?").
 */
export function createStoreContext<T extends Store<any>>(
  providers: StoreProvider<T>[] = [],
  scopedProviders: StoreProvider<T>[] = [],
) {
  const context = React.createContext<T | undefined>(undefined);
  const scopedContext = React.createContext<T | undefined>(undefined);

  const useContext = () => React.useContext(context);

  const useScopedContext = (onlyScoped = false): T | undefined => {
    const scoped = React.useContext(scopedContext);
    const store = useContext();
    if (onlyScoped) return scoped;
    return scoped ?? store;
  };

  const useProviderContext = (): T | undefined => {
    const scoped = React.useContext(scopedContext);
    const store = useContext();
    if (scoped && scoped === store) return undefined;
    return store;
  };

  const ContextProvider = (
    props: React.ComponentPropsWithoutRef<typeof context.Provider>,
  ) =>
    providers.reduceRight(
      (children, Provider) => <Provider {...props}>{children}</Provider>,
      <context.Provider {...props} />,
    );

  const ScopedContextProvider = (
    props: React.ComponentPropsWithoutRef<typeof scopedContext.Provider>,
  ) => (
    <ContextProvider {...props}>
      {scopedProviders.reduceRight(
        (children, Provider) => <Provider {...props}>{children}</Provider>,
        <scopedContext.Provider {...props} />,
      )}
    </ContextProvider>
  );

  return {
    context,
    scopedContext,
    useContext,
    useScopedContext,
    useProviderContext,
    ContextProvider,
    ScopedContextProvider,
  };
}
