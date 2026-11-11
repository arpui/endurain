/**
 * Concrete Leaflet implementation of the {@link MapProvider} seam. Registered
 * during bootstrap so Activity and Home surfaces can render GPS tracks. Kept
 * behind the seam so minimal builds can omit Leaflet entirely.
 *
 * The pure {@link toLatLngs} and {@link boundsOf} helpers are exported so the
 * seam → Leaflet data translation can be unit-tested without a real map.
 */
import {
  circleMarker,
  divIcon,
  marker,
  map as createMap,
  polyline,
  tileLayer,
  type Layer,
  type LeafletMouseEvent,
} from 'leaflet'
import 'leaflet/dist/leaflet.css'

import type {
  GeoPoint,
  MapInstance,
  MapOverlay,
  MapProvider,
  MapRenderOptions,
  MapTrack,
} from '@/composables/useMapProvider'

import { DEFAULT_MAP_CONFIG } from '@/features/config/defaults'
import { themeColor } from '@/lib/themeColor'

/** A latitude/longitude pair in Leaflet's expected `[lat, lng]` order. */
export type LatLngTuple = [number, number]

/** Tile-server configuration for the Leaflet provider. */
export interface LeafletMapProviderConfig {
  /** Tile URL template (supports Leaflet's `{s}`, `{z}`, `{x}`, `{y}`). */
  tileUrl?: string
  /** Attribution HTML shown in the map corner. */
  attribution?: string
  /** Maximum zoom level offered by the tile server. */
  maxZoom?: number
  /** Polyline colour for the rendered track. */
  trackColor?: string
}

const DEFAULT_MAX_ZOOM = 19
/** Brand teal track colour, from the `--color-brand` design token. */
const DEFAULT_TRACK_COLOR = themeColor('--color-brand')
/** Start-marker fill — the `--color-goal` design token (green). */
const START_MARKER_COLOR = themeColor('--color-goal')
/** Finish-marker fill — the `--color-hr` design token (red). */
const FINISH_MARKER_COLOR = themeColor('--color-hr')

/**
 * Converts seam {@link GeoPoint}s to Leaflet `[lat, lng]` tuples.
 *
 * @param points - Coordinates to convert.
 * @returns Tuples in Leaflet order.
 */
export function toLatLngs(points: readonly GeoPoint[]): LatLngTuple[] {
  return points.map((point) => [point.lat, point.lng])
}

/**
 * Computes the bounding box covering a track's points and markers.
 *
 * @param track - The track to bound.
 * @returns `[[south, west], [north, east]]`, or `null` when the track is empty.
 */
export function boundsOf(track: MapTrack): [LatLngTuple, LatLngTuple] | null {
  const points = [...track.points]
  if (track.start) {
    points.push(track.start)
  }
  if (track.finish) {
    points.push(track.finish)
  }
  const first = points[0]
  if (!first) {
    return null
  }

  let south = first.lat
  let north = first.lat
  let west = first.lng
  let east = first.lng
  for (const point of points) {
    south = Math.min(south, point.lat)
    north = Math.max(north, point.lat)
    west = Math.min(west, point.lng)
    east = Math.max(east, point.lng)
  }

  return [
    [south, west],
    [north, east],
  ]
}

/**
 * Builds a circular emphasis marker with a white border and coloured fill,
 * used for the start (green) and finish (red) points.
 *
 * @param point - The marker location.
 * @param color - The fill colour.
 * @returns A Leaflet circle-marker layer.
 */
function emphasisMarker(point: GeoPoint, color: string): Layer {
  return circleMarker([point.lat, point.lng], {
    radius: 7,
    color: '#ffffff',
    weight: 3,
    fillColor: color,
    fillOpacity: 1,
  })
}

/**
 * Creates a Leaflet-backed map provider.
 *
 * @param config - Optional tile-server overrides; defaults to OpenStreetMap.
 * @returns A {@link MapProvider} ready to register via `setMapProvider`.
 */
export function createLeafletMapProvider(config: LeafletMapProviderConfig = {}): MapProvider {
  const tileUrl = config.tileUrl ?? DEFAULT_MAP_CONFIG.tileUrl
  const attribution = config.attribution ?? DEFAULT_MAP_CONFIG.attribution
  const maxZoom = config.maxZoom ?? DEFAULT_MAX_ZOOM
  const trackColor = config.trackColor ?? DEFAULT_TRACK_COLOR

  return {
    name: 'leaflet',
    mount(target: HTMLElement, options: MapRenderOptions): MapInstance {
      const interactive = options.interactive ?? true
      const map = createMap(target, {
        attributionControl: true,
        zoomControl: interactive,
        dragging: interactive,
        scrollWheelZoom: interactive,
        keyboard: interactive,
      })

      tileLayer(tileUrl, { attribution, maxZoom }).addTo(map)

      let overlays: Layer[] = []
      let currentTrack: MapTrack = options.track
      // one registered handler, always delegating to the latest render's callback
      let onMapClick = options.onMapClick
      map.on('click', (event: LeafletMouseEvent) => {
        onMapClick?.({ lat: event.latlng.lat, lng: event.latlng.lng })
      })

      /**
       * Frames a track's bounds - once, and only real geometry.
       *
       * The camera is the viewer's: an update must never yank it (a designer
       * clicking points would lose their place every time), the empty track
       * of a surface that draws through overlays means nothing, and a
       * single point would zoom past any useful level. The first render
       * carrying a spread of points is what earns the fit; explicit
       * recenter and focus still frame on demand.
       */
      let framed = false
      const fit = (track: MapTrack): void => {
        const bounds = boundsOf(track)
        if (!bounds) {
          return
        }
        const [[south, west], [north, east]] = bounds
        if ((south === north && west === east) || framed) {
          return
        }
        framed = true
        map.fitBounds(bounds, { padding: [24, 24] })
      }

      const draw = (track: MapTrack, extra: MapOverlay[] = []): void => {
        currentTrack = track
        for (const layer of overlays) {
          map.removeLayer(layer)
        }
        overlays = []

        const linePoints = toLatLngs(track.points)
        if (linePoints.length > 0) {
          overlays.push(polyline(linePoints, { color: trackColor, weight: 4 }).addTo(map))
        }
        for (const overlay of extra) {
          const points = toLatLngs(overlay.points)
          const positions: [number, number][] = points
          let lineRef: ReturnType<typeof polyline> | null = null
          if (points.length > 0) {
            lineRef = polyline(points, {
              color: overlay.color,
              weight: overlay.weight ?? 6,
              opacity: overlay.opacity ?? 0.85,
              dashArray: overlay.dashArray,
            }).addTo(map)
            if (overlay.name) {
              lineRef.bindTooltip(overlay.name)
            }
            if (overlay.onSelect) {
              lineRef.on('click', overlay.onSelect)
            }
            overlays.push(lineRef)
          }
          if (overlay.markers) {
            overlay.markers.forEach((marker) => {
              const dot = circleMarker([marker.point.lat, marker.point.lng], {
                radius: 6,
                color: marker.color,
                weight: 2,
                fillColor: '#ffffff',
                fillOpacity: 1,
              }).addTo(map)
              if (marker.label) {
                dot.bindTooltip(marker.label)
              }
              overlays.push(dot)
            })
          }
          if (overlay.vertices && overlay.onVertexClick) {
            overlay.vertices.forEach((vertex, index) => {
              const handle = circleMarker([vertex.lat, vertex.lng], {
                radius: 5,
                color: overlay.color,
                weight: 2,
                fillColor: '#ffffff',
                fillOpacity: 1,
              }).addTo(map)
              handle.on('click', () => overlay.onVertexClick?.(index))
              overlays.push(handle)
              if (overlay.onVertexDragEnd) {
                // a transparent draggable grabber over the handle: dragging
                // moves the handle and redraws the line inside the provider
                // (no full re-render, which would destroy the layer being
                // dragged); only the release reports up to the app
                const grabber = marker([vertex.lat, vertex.lng], {
                  draggable: true,
                  opacity: 0,
                  icon: divIcon({ className: '', iconSize: [20, 20] }),
                }).addTo(map)
                let dragged = false
                grabber.on('dragstart', () => {
                  dragged = false
                })
                grabber.on('drag', () => {
                  dragged = true
                  const at = grabber.getLatLng()
                  handle.setLatLng(at)
                  positions[index] = [at.lat, at.lng]
                  lineRef?.setLatLngs(positions)
                })
                grabber.on('dragend', () => {
                  if (!dragged) {
                    return
                  }
                  const at = grabber.getLatLng()
                  overlay.onVertexDragEnd?.(index, { lat: at.lat, lng: at.lng })
                })
                grabber.on('click', () => {
                  if (dragged) {
                    dragged = false
                    return
                  }
                  overlay.onVertexClick?.(index)
                })
                overlays.push(grabber)
              }
            })
          }
        }
        if (track.start) {
          overlays.push(emphasisMarker(track.start, START_MARKER_COLOR).addTo(map))
        }
        if (track.finish) {
          overlays.push(emphasisMarker(track.finish, FINISH_MARKER_COLOR).addTo(map))
        }

        fit(track)
      }

      draw(options.track, options.overlays)

      return {
        update(next: MapRenderOptions): void {
          onMapClick = next.onMapClick
          draw(next.track, next.overlays)
        },
        recenter(): void {
          fit(currentTrack)
        },
        focus(points: GeoPoint[]): void {
          const bounds = boundsOf({ points })
          if (bounds) {
            framed = true
            map.fitBounds(bounds, { padding: [24, 24] })
          }
        },
        panTo(point: GeoPoint, zoom: number): void {
          framed = true
          map.setView([point.lat, point.lng], zoom)
        },
        destroy(): void {
          map.remove()
        },
      }
    },
  }
}
