/**
 * Ariakit-style authoring types. A slim mirror of the shapes from
 * `@ariakit/react-core/utils/types` so primitives written here look
 * and feel identical to those shipped by Ariakit.
 */

import type * as React from 'react';

type AnyObject = Record<keyof any, any>;
type EmptyObject = Record<string, never>;

export type RenderProp<
  P = React.HTMLAttributes<any> & { ref?: React.Ref<any> },
> = (props: P) => React.ReactNode;

export type WrapElement = (element: React.ReactElement) => React.ReactElement;

/**
 * Props accepted by every primitive: standard HTML + `render` for composition.
 */
export interface Options {
  wrapElement?: WrapElement;
  /**
   * Replace the rendered HTML element / React component, merging this
   * primitive's behaviour onto it. Pass a React element or a function
   * receiving the merged props.
   */
  render?: RenderProp | React.ReactElement;
}

export type HTMLProps<
  T extends React.ElementType,
  P extends AnyObject = EmptyObject,
> = Omit<React.ComponentPropsWithRef<T>, keyof P> & {
  [index: `data-${string}`]: unknown;
};

export type Props<
  T extends React.ElementType,
  P extends AnyObject = EmptyObject,
> = P & HTMLProps<T, P>;

export type Hook<
  T extends React.ElementType,
  P extends AnyObject = EmptyObject,
> = <ElementType extends React.ElementType = T>(
  props?: Props<ElementType, P>,
) => HTMLProps<ElementType, P>;
