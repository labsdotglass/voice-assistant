/**
 * The colour vocabulary: one name per scale file under ../styles/colors/.
 * Components take `color="<hue>"`; the meaning of each hue (green for
 * success, red for destructive, and so on) is a usage convention, not a
 * second vocabulary in code.
 *
 * `neutral` is the black bookend and the default accent; `gray` is Radix's
 * mid-grey.
 */
export const COLORS = [
  'neutral',
  'gray', 'mauve', 'slate', 'sage', 'olive', 'sand',
  'amber', 'blue', 'bronze', 'brown', 'crimson', 'cyan', 'gold', 'grass',
  'green', 'indigo', 'iris', 'jade', 'lime', 'mint', 'orange', 'pink', 'plum',
  'purple', 'red', 'ruby', 'sky', 'teal', 'tomato', 'violet', 'yellow',
] as const;

export type Color = (typeof COLORS)[number];

export function isColor(value: string): value is Color {
  return (COLORS as readonly string[]).includes(value);
}
