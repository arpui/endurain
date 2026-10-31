import type { SegmentImportInput } from '../types'

/** A parse problem, expressed as an i18n key plus optional params. */
export interface ParseError {
  key: string
  params?: Record<string, unknown>
}

/** A `[lat, lng]` pair read from either accepted shape. */
export type RawPoint = [number, number]

/**
 * What a file or pasted text carries, before the form's own fields (display
 * name and id) are merged in. `segmentId` and `name` stay empty when the source
 * has none, so the form can ask for them instead of failing the read.
 */
export interface ParsedSegment {
  name: string
  segmentId: number | null
  latlng: RawPoint[]
  altitude: number[] | null
  distance: number[] | null
  source: 'strava' | 'manual'
}

/** The outcome of reading a segment out of pasted JSON or an uploaded GPX. */
export type ParseOutcome = { ok: true; parsed: ParsedSegment } | { ok: false; error: ParseError }

const MIN_POINTS = 2

/** Returns the pair when it holds two finite numbers, `null` otherwise. */
function toPoint(value: unknown): RawPoint | null {
  if (!Array.isArray(value) || value.length < 2) {
    return null
  }
  const lat = Number(value[0])
  const lng = Number(value[1])
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return null
  }
  return [lat, lng]
}

/** Returns the array of finite numbers, or `null` when any entry is not numeric. */
function toNumbers(value: unknown): number[] | null {
  if (!Array.isArray(value)) {
    return null
  }
  const numbers = value.map(Number)
  return numbers.every((entry) => Number.isFinite(entry)) ? numbers : null
}

/** Rejects a point list that is too short to be a segment, or arrays misaligned with it. */
function checkShape(parsed: ParsedSegment): ParseError | null {
  if (parsed.latlng.length < MIN_POINTS) {
    return { key: 'segments.import.tooFewPoints' }
  }
  for (const array of [parsed.altitude, parsed.distance]) {
    if (array !== null && array.length !== parsed.latlng.length) {
      return { key: 'segments.import.lengthMismatch' }
    }
  }
  return null
}

/**
 * Reads a segment from pasted JSON. Two shapes are accepted: the API import
 * contract (`{"segment_id", "name", "latlng", "altitude"?, "distance"?}`) and the
 * raw Strava stream the extraction script writes
 * (`{"latlng": [[lat, lng], ...], "altitude": [...], "distance": [...]}`).
 *
 * @param text - The JSON text.
 * @returns The parsed segment, or a parse error keyed for translation.
 */
export function parseSegmentJson(text: string): ParseOutcome {
  let body: unknown
  try {
    body = JSON.parse(text)
  } catch {
    return { ok: false, error: { key: 'segments.import.badJson' } }
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: { key: 'segments.import.badJson' } }
  }

  const record = body as Record<string, unknown>
  const parsed: ParsedSegment = {
    name: typeof record.name === 'string' ? record.name : '',
    segmentId: Number.isFinite(Number(record.segment_id)) ? Number(record.segment_id) : null,
    latlng: (Array.isArray(record.latlng) ? record.latlng : [])
      .map(toPoint)
      .filter((point): point is RawPoint => point !== null),
    altitude: record.altitude === undefined ? null : toNumbers(record.altitude),
    distance: record.distance === undefined ? null : toNumbers(record.distance),
    source: record.source === 'manual' ? 'manual' : 'strava',
  }

  const error = checkShape(parsed)
  return error ? { ok: false, error } : { ok: true, parsed }
}

/** Returns the text content of the first matching descendant element, or `''`. */
function childText(node: Element | null, tagName: string): string {
  if (!node) {
    return ''
  }
  return node.getElementsByTagName(tagName)[0]?.textContent?.trim() ?? ''
}

/**
 * Reads a segment out of a GPX document, taking its track points (or route
 * points, when there is no track) in document order. Altitudes come from `<ele>`
 * when every point carries one, so the detail page can draw the profile; the
 * server derives the length from the geometry.
 *
 * @param xml - The GPX text.
 * @returns The parsed segment, or a parse error keyed for translation.
 */
export function parseSegmentGpx(xml: string): ParseOutcome {
  const doc = new DOMParser().parseFromString(xml, 'application/xml')
  if (doc.querySelector('parsererror') || doc.documentElement?.localName !== 'gpx') {
    return { ok: false, error: { key: 'segments.import.badGpx' } }
  }

  const trackPoints = Array.from(doc.getElementsByTagName('trkpt'))
  const points = trackPoints.length ? trackPoints : Array.from(doc.getElementsByTagName('rtept'))
  const latlng = points
    .map((point) =>
      toPoint([point.getAttribute('lat'), point.getAttribute('lon') ?? point.getAttribute('lng')]),
    )
    .filter((entry): entry is RawPoint => entry !== null)

  const altitudes = points.map((point) => childText(point, 'ele')).filter((entry) => entry !== '')
  const numbers = altitudes.map(Number)
  const parsed: ParsedSegment = {
    name: childText(doc.querySelector('trk') ?? doc.querySelector('rte'), 'name'),
    segmentId: null,
    latlng,
    altitude: numbers.length === latlng.length && numbers.every(Number.isFinite) ? numbers : null,
    distance: null,
    source: 'manual',
  }

  const error = checkShape(parsed)
  return error ? { ok: false, error } : { ok: true, parsed }
}

/** True when a file name looks like GPX (what the import form reads as XML). */
export function looksLikeGpx(filename: string): boolean {
  return /\.gpx$/i.test(filename)
}

/**
 * Merges the form's own fields into a parsed segment to produce what the API
 * expects. The file usually supplies the geometry; the form supplies the display
 * name and the id when the file has none.
 *
 * @param parsed - What the JSON or GPX carried.
 * @param form - The form's name and id fields.
 * @returns The import input, or the first missing piece as a parse error.
 */
export function toImportInput(
  parsed: ParsedSegment,
  form: { name: string; segmentId: string },
): { ok: true; input: SegmentImportInput } | { ok: false; error: ParseError } {
  const name = form.name.trim() || parsed.name.trim()
  if (!name) {
    return { ok: false, error: { key: 'segments.import.needName' } }
  }
  const segmentId = parsed.segmentId ?? Number(form.segmentId)
  if (!Number.isFinite(segmentId) || segmentId <= 0) {
    return { ok: false, error: { key: 'segments.import.needId' } }
  }
  return {
    ok: true,
    input: {
      segmentId: Math.trunc(segmentId),
      name,
      latlng: parsed.latlng,
      altitude: parsed.altitude,
      distance: parsed.distance,
      source: parsed.source,
    },
  }
}
