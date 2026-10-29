import { createContext, useContext } from 'react';

/** Shared between Field (provider) and the text controls (consumers) —
 * split from field.tsx so component files only export components
 * (react-refresh). */

export interface FieldContextValue {
  inputId: string;
  invalid: boolean;
  required: boolean;
  describedBy: string | undefined;
  registerDescription: (id: string) => () => void;
}

export const FieldContext = createContext<FieldContextValue | null>(null);

export function useFieldContext(): FieldContextValue | null {
  return useContext(FieldContext);
}
