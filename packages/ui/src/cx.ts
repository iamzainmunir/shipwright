/** Tiny className combiner. Filters out falsy values and joins with a space. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
