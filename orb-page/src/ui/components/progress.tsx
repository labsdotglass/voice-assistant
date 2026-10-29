import { type HTMLAttributes } from 'react';
import { cn } from '../lib/utils';
import styles from './progress.module.css';

interface ProgressProps extends HTMLAttributes<HTMLDivElement> {
  /** 0–100 (clamped). */
  value: number;
  /** Accessible name for the progressbar. */
  label?: string;
}

/** Determinate progress bar (RadialProgress's straight sibling).
 * Fill follows --accent-9 — wrap in data-accent-color to recolour. */
export function Progress({ value, label, className, ...props }: ProgressProps) {
  const clamped = Math.min(100, Math.max(0, value));
  return (
    <div
      className={cn(styles.root, className)}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      {...props}
    >
      <div className={styles.fill} style={{ width: `${clamped}%` }} />
    </div>
  );
}
