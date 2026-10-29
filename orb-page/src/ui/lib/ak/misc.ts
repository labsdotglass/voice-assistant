/**
 * Small DOM / React helpers used across the ak/ utilities.
 * Mirrors `@ariakit/react-core/utils/misc`.
 */

import { isValidElement, type HTMLAttributes, type MutableRefObject, type ReactElement, type Ref, type RefCallback } from 'react';

export function setRef<T>(
  ref: RefCallback<T> | MutableRefObject<T> | null | undefined,
  value: T,
): void {
  if (typeof ref === 'function') {
    ref(value);
  } else if (ref) {
    ref.current = value;
  }
}

export function isValidElementWithRef<P extends { ref?: Ref<unknown> }>(
  element: unknown,
): element is ReactElement<P> & { ref?: Ref<unknown> } {
  if (!element) return false;
  if (!isValidElement<{ ref?: Ref<unknown> }>(element)) return false;
  if ('ref' in element.props) return true;
  if ('ref' in element) return true;
  return false;
}

export function getRefProperty(element: unknown): Ref<unknown> | null | undefined {
  if (!isValidElementWithRef(element)) return null;
  const props = element.props as { ref?: Ref<unknown> };
  return props.ref ?? (element as { ref?: Ref<unknown> }).ref;
}

/**
 * Merges two prop bags. `overrides` win, except event handlers, `className`,
 * and `style`, which compose with the base values.
 */
export function mergeProps<T extends HTMLAttributes<HTMLElement>>(
  base: T,
  overrides: T,
): T {
  const props: T = { ...base };

  for (const key in overrides) {
    if (!Object.prototype.hasOwnProperty.call(overrides, key)) continue;

    if (key === 'className') {
      const baseClass = (base as { className?: string }).className;
      const overrideClass = (overrides as { className?: string }).className;
      (props as { className?: string }).className =
        baseClass && overrideClass
          ? `${baseClass} ${overrideClass}`
          : overrideClass || baseClass;
      continue;
    }

    if (key === 'style') {
      const baseStyle = (base as { style?: React.CSSProperties }).style;
      const overrideStyle = (overrides as { style?: React.CSSProperties }).style;
      (props as { style?: React.CSSProperties }).style = baseStyle
        ? { ...baseStyle, ...overrideStyle }
        : overrideStyle;
      continue;
    }

    const overrideValue = overrides[key];

    if (key.startsWith('on')) {
      if (typeof overrideValue !== 'function') continue;
      const baseValue = base[key];
      if (typeof baseValue === 'function') {
        const baseFn = baseValue as (...args: unknown[]) => unknown;
        const overrideFn = overrideValue as (...args: unknown[]) => unknown;
        // Run the override (consumer / render element) first, then the base
        // (primitive) handler — UNLESS the override called preventDefault().
        // This is how Ariakit lets a composed element veto primitive behaviour
        // (e.g. `onClick={(e) => e.preventDefault()}` to cancel a toggle).
        (props as Record<string, unknown>)[key] = (...args: unknown[]) => {
          overrideFn(...args);
          const event = args[0] as { defaultPrevented?: boolean } | undefined;
          if (event && event.defaultPrevented) return;
          baseFn(...args);
        };
        continue;
      }
    }

    (props as Record<string, unknown>)[key] = overrideValue;
  }

  return props;
}
