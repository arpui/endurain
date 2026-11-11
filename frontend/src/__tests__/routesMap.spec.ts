import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'

import type { GeoPoint, MapInstance, MapRenderOptions } from '@/composables/useMapProvider'

// Leaflet in jsdom: layers are inert recording stubs. The spec exercises the
// designer's overlay contract - subsampling, index mapping, re-render on
// identity - not real tiles.
type AnyFn = (...args: unknown[]) => unknown

const layerStub = (): Record<string, unknown> => {
  const layer: Record<string, unknown> = {}
  for (const method of ['on', 'addTo', 'bindTooltip', 'setLatLng', 'setLatLngs', 'getLatLng']) {
    layer[method] = vi.fn<AnyFn>(() => layer)
  }
  return layer
}

vi.mock('leaflet', () => ({
  map: vi.fn<AnyFn>(() => ({
    on: vi.fn<AnyFn>(),
    removeLayer: vi.fn<AnyFn>(),
    remove: vi.fn<AnyFn>(),
    fitBounds: vi.fn<AnyFn>(),
    setView: vi.fn<AnyFn>(),
  })),
  tileLayer: vi.fn<AnyFn>(layerStub),
  polyline: vi.fn<AnyFn>(layerStub),
  circleMarker: vi.fn<AnyFn>(layerStub),
  marker: vi.fn<AnyFn>(layerStub),
  divIcon: vi.fn<AnyFn>(() => ({})),
}))

import { setMapProvider, useMapProvider } from '@/composables/useMapProvider'
import { registerRenderProviders } from '@/providers'
import RoutesMap from '@/features/analysis/components/RoutesMap.vue'

const point = (index: number): GeoPoint => ({ lat: 42 + index / 1000, lng: 1.5 + index / 1000 })
const chain = (n: number): GeoPoint[] => Array.from({ length: n }, (_, i) => point(i))

/** The last options handed to the map, plus how often it was asked to redraw. */
let lastOptions: MapRenderOptions | null = null
let updateCalls = 0
let focused: number[] = []
let panned: { lat: number; lng: number; zoom: number }[] = []

async function mountMap(props: Record<string, unknown>) {
  const wrapper = mount(RoutesMap, {
    props: { routes: [], selected: null, label: 'map', ...props },
  })
  await flushPromises()
  return wrapper
}

describe('routes map editor', () => {
  beforeEach(async () => {
    lastOptions = null
    updateCalls = 0
    focused = []
    panned = []
    await registerRenderProviders()
    const inner = useMapProvider().provider
    setMapProvider({
      name: 'spy-map',
      mount: (el: HTMLElement, options: MapRenderOptions): MapInstance => {
        lastOptions = options
        const instance = inner.mount(el, options)
        return {
          ...instance,
          update: (next: MapRenderOptions): void => {
            lastOptions = next
            updateCalls += 1
            instance.update(next)
          },
          focus: (points: GeoPoint[]): void => {
            focused.push(points.length)
            instance.focus(points)
          },
          panTo: (center: GeoPoint, zoom: number): void => {
            panned.push({ lat: center.lat, lng: center.lng, zoom })
            instance.panTo?.(center, zoom)
          },
        }
      },
    })
  })

  it('draws the draft with one handle per point when it is hand-drawn', async () => {
    await mountMap({ draft: chain(4) })
    const draftOverlay = lastOptions?.overlays?.find((overlay) => overlay.vertices)
    expect(draftOverlay?.points).toHaveLength(4)
    expect(draftOverlay?.vertices).toHaveLength(4)
  })

  it('subsamples handles on a dense draft and maps callbacks to the real indices', async () => {
    const wrapper = await mountMap({ draft: chain(200) })
    const draftOverlay = lastOptions?.overlays?.find((overlay) => overlay.vertices)
    expect(draftOverlay?.points).toHaveLength(200)
    expect(draftOverlay?.vertices?.length ?? 0).toBeLessThanOrEqual(151)
    // handle 1 stands for draft point 2 (step 2): the callbacks must say so
    draftOverlay?.onVertexClick?.(1)
    expect(wrapper.emitted('removeVertex')?.[0]).toEqual([2])
    draftOverlay?.onVertexDragEnd?.(1, point(900))
    expect(wrapper.emitted('moveVertex')?.[0]).toEqual([2, point(900)])
  })

  it('redraws when the draft is replaced with a new array', async () => {
    const wrapper = await mountMap({ draft: chain(3) })
    const before = updateCalls
    await wrapper.setProps({ draft: chain(4) })
    expect(updateCalls).toBeGreaterThan(before)
  })

  it('draws the connect preview as one line per stretch, styled by kind', async () => {
    await mountMap({
      draft: chain(3),
      preview: {
        points: chain(5),
        carried: ['walked', 'mapped', 'straight', 'walked'],
        spans: [1, 2, 3, 4],
      },
    })
    const overs = lastOptions?.overlays ?? []
    // four two-point stretches, sliced at the shared boundaries
    const stretches = overs.filter(
      (overlay) => (overlay.points?.length ?? 0) === 2 && !overlay.vertices,
    )
    expect(stretches).toHaveLength(4)
    // mapped ground stands out dashed; the raw draft steps back under the answer
    expect(stretches.filter((overlay) => overlay.dashArray)).toHaveLength(1)
    const draftOverlay = overs.find((overlay) => overlay.vertices)
    expect(draftOverlay?.weight).toBe(2)
  })

  it('redraws when a preview arrives or is withdrawn', async () => {
    const wrapper = await mountMap({ draft: chain(2) })
    await wrapper.setProps({ preview: { points: chain(3), carried: ['walked'], spans: [2] } })
    expect(updateCalls).toBeGreaterThan(0)
    const afterArrival = updateCalls
    await wrapper.setProps({ preview: null })
    expect(updateCalls).toBeGreaterThan(afterArrival)
  })

  it('redraws when picking state, endpoints or water change', async () => {
    const wrapper = await mountMap({ draft: [] })
    await wrapper.setProps({ picking: true })
    expect(updateCalls).toBeGreaterThan(0)
    const afterPicking = updateCalls
    await wrapper.setProps({ endpoints: [point(0)] })
    expect(updateCalls).toBeGreaterThan(afterPicking)
    const afterEndpoints = updateCalls
    await wrapper.setProps({ water: [{ point: point(1), name: 'Font Freda', kind: 'font' }] })
    expect(updateCalls).toBeGreaterThan(afterEndpoints)
  })

  it("opens a fresh draft near the athlete's ground, not on the world", async () => {
    await mountMap({ draft: [point(0)], center: point(5) })
    expect(panned).toEqual([{ lat: point(5).lat, lng: point(5).lng, zoom: 16 }])
    expect(focused).toEqual([])
  })

  it('frames a hand-drawn line when it starts and never on later clicks', async () => {
    const wrapper = await mountMap({ draft: [] })
    await wrapper.setProps({ draft: chain(2) })
    expect(focused).toEqual([2])
    const frames = focused.length
    await wrapper.setProps({ draft: chain(3) })
    expect(focused).toHaveLength(frames)
  })

  it('focuses the full line when a saved route is edited', async () => {
    await mountMap({ draft: chain(30), center: point(5) })
    expect(focused).toEqual([30])
    expect(panned).toEqual([])
  })
})
