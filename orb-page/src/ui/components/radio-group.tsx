import { type ComponentPropsWithoutRef, type ReactNode } from 'react';
import {
  Radio as AkRadio,
  RadioGroup as AkRadioGroup,
  RadioProvider as AkRadioProvider,
  type RadioProps as AkRadioProps,
  type RadioProviderProps as AkRadioProviderProps,
} from '@ariakit/react';
import { cn } from '../lib/utils';
import { useFieldContext } from './field-context';
import styles from './radio-group.module.css';

/** Plain single-select control over Ariakit's Radio family (provider-first)
 * — a conventional radio: a circular indicator plus label, stacked vertically
 * by default, for a single-choice settings field. */

export type RadioProviderProps = AkRadioProviderProps;
export type RadioGroupProps = ComponentPropsWithoutRef<typeof AkRadioGroup>;

export function RadioProvider({ focusLoop = true, ...props }: RadioProviderProps) {
  return <AkRadioProvider focusLoop={focusLoop} {...props} />;
}

/** Inside a `<Field>` the group element inherits `aria-invalid`,
 * `aria-required` and `aria-describedby` from context, so the field's error and
 * description reach it. It is not an input, so `FieldLabel htmlFor` does not
 * associate — a radiogroup is named with `aria-labelledby` (out of scope here);
 * the description and error still announce. A group's own `aria-invalid` counts
 * inside a non-invalid Field. */
export function RadioGroup({
  className, children, 'aria-describedby': ownDescribedBy, 'aria-invalid': ownInvalid, ...props
}: RadioGroupProps) {
  const field = useFieldContext();
  const describedBy =
    [field?.describedBy, ownDescribedBy].filter(Boolean).join(' ') || undefined;
  return (
    <AkRadioGroup
      aria-invalid={field?.invalid || ownInvalid || undefined}
      aria-required={field?.required || undefined}
      aria-describedby={describedBy}
      {...props}
      className={cn(styles.group, className)}
    >
      {children}
    </AkRadioGroup>
  );
}

interface RadioProps extends AkRadioProps {
  children?: ReactNode;
}

export function Radio({ children, className, ...props }: RadioProps) {
  return (
    <label className={cn(styles.chip, className)}>
      <AkRadio className={styles.input} {...props} />
      <span aria-hidden className={styles.dot} />
      {children}
    </label>
  );
}
