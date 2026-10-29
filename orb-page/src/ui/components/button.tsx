import {
  type ButtonHTMLAttributes, type MouseEvent, type MouseEventHandler,
  type ReactElement, type Ref, cloneElement, isValidElement,
} from 'react';
import { cn } from '../lib/utils';
import type { Color } from './color';
import styles from './button.module.css';

export type ButtonVariant = 'solid' | 'outline' | 'ghost' | 'plain';
export type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'color'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Hue by scale name (tokens.md §2.3). Sets `data-accent-color` so
   * `--accent-*` remaps for this button. With no value the button inherits
   * the nearest accent, which is the Theme's accent or `neutral` at the root.
   * Pick from the usage table: `red` for destructive actions, `neutral` to
   * force the bookend inside a coloured subtree. */
  color?: Color;
  /** Square button for a single icon. Caller must supply an `aria-label` AND
   * wrap it in a Tooltip mirroring that label — the
   * aria-label serves screen readers, the tooltip serves everyone else. */
  iconOnly?: boolean;
  /** Show a spinner and block interaction (also implies disabled). */
  loading?: boolean;
  /** Ariakit-style polymorphism: render as a different element (e.g. an `<a>` or
   * `NavLink`) instead of `<button>`, keeping the button styling. */
  render?: ReactElement;
  /** Ref to the underlying element — to focus it, measure it, or hand it to an
   * Ariakit `initialFocus`. Declared explicitly because `ButtonHTMLAttributes`
   * does not include `ref`; React 19 passes it through as a regular prop, which
   * `...props` spreads onto the rendered element. Typed `HTMLButtonElement`
   * because that is the default element, but note the widening: in the
   * polymorphic `render` path the ref lands on whatever element `render`
   * supplies (e.g. an `HTMLAnchorElement` for `render={<a … />}`), so the
   * runtime type can differ from the declared one. */
  ref?: Ref<HTMLButtonElement>;
}

export function Button({
  variant = 'solid',
  size = 'md',
  color,
  iconOnly = false,
  loading = false,
  disabled,
  className,
  children,
  type,
  render,
  ...props
}: ButtonProps) {
  const isDisabled = disabled || loading;

  const shared = {
    className: cn(styles.root, className),
    // With no `color`, omit the attribute entirely so the button inherits the
    // nearest `data-accent-color` ancestor (a Theme's accent, or `neutral` at
    // the root). Passing `color` stamps it explicitly, overriding whatever
    // the button would otherwise inherit.
    'data-accent-color': color,
    'data-variant': variant,
    'data-size': size,
    'data-icon-only': iconOnly || undefined,
    'data-loading': loading || undefined,
  } as const;

  const content = (
    <>
      {loading && <span className={styles.spinner} aria-hidden />}
      <span className={styles.label}>{children}</span>
    </>
  );

  // Polymorphic render: clone the provided element, merging our styling +
  // disabled semantics. Used for link-buttons without Ariakit's `asChild`.
  if (render && isValidElement(render)) {
    const rendered = render as ReactElement<Record<string, unknown>>;
    const { onClick: propsOnClick, ...restProps } = props;
    const renderedOnClick = rendered.props.onClick as
      | MouseEventHandler<HTMLButtonElement> | undefined;
    // A rendered <a> has no native `disabled`, so `aria-disabled` alone would
    // leave it clickable and in the tab order — it would lie to AT. Make it
    // genuinely inert: swallow the click, drop it from the tab order, and let
    // CSS kill pointer events (`.root[aria-disabled="true"]`). When enabled,
    // chain the render element's own handler with the consumer's (Ariakit
    // merges rather than clobbering).
    const mergedOnClick: MouseEventHandler<HTMLButtonElement> = isDisabled
      ? (e: MouseEvent<HTMLButtonElement>) => e.preventDefault()
      : (e: MouseEvent<HTMLButtonElement>) => { renderedOnClick?.(e); propsOnClick?.(e); };
    return cloneElement(rendered, {
      ...shared,
      ...restProps,
      className: cn(styles.root, rendered.props.className as string | undefined, className),
      'aria-disabled': isDisabled || undefined,
      'aria-busy': loading || undefined,
      tabIndex: isDisabled ? -1 : (rendered.props.tabIndex as number | undefined),
      onClick: mergedOnClick,
    }, content);
  }

  return (
    <button
      type={type ?? 'button'}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      {...shared}
      {...props}
    >
      {content}
    </button>
  );
}
