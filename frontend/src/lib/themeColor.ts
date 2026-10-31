/**
 * Resolves an Endurain design-token colour (declared in the `@theme` block of
 * `src/assets/main.css`) to a concrete colour string for canvas/JS contexts —
 * Chart.js and similar render to a `<canvas>` and cannot consume `var(--…)`.
 *
 * Reading the live computed value keeps `main.css` the single source of truth,
 * so charts stay in sync with the design tokens instead of duplicating their
 * hex values. A bundled fallback is returned when no document/stylesheet is
 * available (SSR, unit tests).
 */

/** Design-token colours that may be resolved for canvas/JS rendering. */
export type ThemeColorToken =
  | '--color-brand'
  | '--color-brand-mid'
  | '--color-effort'
  | '--color-hr'
  | '--color-ink'
  | '--color-info'
  | '--color-muted-foreground'
  | '--color-goal'

/**
 * Bundled fallbacks mirroring `main.css`, used when the live stylesheet is not
 * available (SSR, unit tests). The single place these hex values are repeated.
 */
const TOKEN_FALLBACKS: Record<ThemeColorToken, string> = {
  '--color-brand': '#1d9e75',
  '--color-brand-mid': '#0f6e56',
  '--color-effort': '#ef9f27',
  '--color-hr': '#e24b4a',
  '--color-ink': '#2c2c2a',
  '--color-info': '#378add',
  '--color-muted-foreground': '#888780',
  '--color-goal': '#639922',
}

/**
 * Resolves a design-token colour to its concrete value.
 *
 * @param token - The CSS custom property name, e.g. `--color-brand-mid`.
 * @returns The live computed token value, or the bundled fallback when no
 *   document/stylesheet is available.
 */
export function themeColor(token: ThemeColorToken): string {
  if (typeof document !== 'undefined') {
    const resolved = getComputedStyle(document.documentElement).getPropertyValue(token).trim()
    if (resolved) {
      return resolved
    }
  }
  return TOKEN_FALLBACKS[token]
}

/**
 * Resolves a design-token colour as a translucent `rgba()` at the given alpha.
 * Suits chart elements that should recede into the background (context bars),
 * since one low-alpha colour reads softly on both light and dark surfaces.
 *
 * @param token - The CSS custom property name, e.g. `--color-brand`.
 * @param alpha - Fill opacity in the 0..1 range.
 * @returns An `rgba(r, g, b, alpha)` string.
 */
export function themeColorAlpha(token: ThemeColorToken, alpha: number): string {
  const hex = themeColor(token).replace('#', '')
  const r = Number.parseInt(hex.slice(0, 2), 16)
  const g = Number.parseInt(hex.slice(2, 4), 16)
  const b = Number.parseInt(hex.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
