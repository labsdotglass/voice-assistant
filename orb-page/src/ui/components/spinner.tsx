import { type HTMLAttributes } from 'react';
import { cn } from '../lib/utils';
import styles from './spinner.module.css';

interface SpinnerProps extends HTMLAttributes<HTMLSpanElement> {
  /** Diameter in px. */
  size?: number;
  /** Accessible label. Omit for a purely decorative spinner (aria-hidden). */
  label?: string;
}

/** Indeterminate activity ring (the same drawing Button's loading state
 * uses). Sized in px, colored via currentColor. */
export function Spinner({ size = 16, label, className, style, ...props }: SpinnerProps) {
  return (
    <span
      className={cn(styles.root, className)}
      style={{ width: size, height: size, ...style }}
      role={label ? 'status' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      {...props}
    />
  );
}
