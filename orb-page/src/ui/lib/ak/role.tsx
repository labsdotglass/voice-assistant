/**
 * The `Role` proxy. `<Role.div>` is just a `<div>` plus `render`-prop
 * composition. Pass an Ariakit-style `render` prop to swap the underlying
 * element while keeping props (events, refs, data attrs) merged.
 *
 *   <Role.div ref={ref} className="x" render={<button type="button" />} />
 *
 *   → renders a <button type="button" class="x" ref={ref} />
 */

import * as React from 'react';
import { forwardRef as akForwardRef, createElement as akCreateElement } from './system';
import type { Options, Props } from './types';

type RoleComponent<T extends keyof React.JSX.IntrinsicElements> = React.FC<
  Props<T, Options>
>;

function makeRole<T extends keyof React.JSX.IntrinsicElements>(tag: T): RoleComponent<T> {
  const Component = akForwardRef(function Role(props: Props<T, Options>) {
    return akCreateElement(tag, props);
  }) as RoleComponent<T> & { displayName?: string };
  Component.displayName = `Role.${tag}`;
  return Component;
}

/**
 * Common HTML tags used by our primitives — easy to extend if a primitive
 * needs another tag.
 */
export const Role = {
  div: makeRole('div'),
  span: makeRole('span'),
  button: makeRole('button'),
  a: makeRole('a'),
  ul: makeRole('ul'),
  li: makeRole('li'),
  nav: makeRole('nav'),
  section: makeRole('section'),
  header: makeRole('header'),
  footer: makeRole('footer'),
  main: makeRole('main'),
  aside: makeRole('aside'),
  p: makeRole('p'),
  h1: makeRole('h1'),
  h2: makeRole('h2'),
  h3: makeRole('h3'),
  h4: makeRole('h4'),
  input: makeRole('input'),
  label: makeRole('label'),
} as const;
