<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { DesignRoute, SegmentNetworkItem } from '@/features/analysis/types'
import { gradeTrailWay } from '@/features/analysis/utils/trailDegree'
import type {
  GeoPoint,
  MapInstance,
  MapOverlay,
  MapRenderOptions,
} from '@/composables/useMapProvider'

import { useMapProvider } from '@/composables/useMapProvider'
import { themeColor } from '@/lib/themeColor'
import { useRenderProvidersReady } from '@/providers'

const props = defineProps<{
  /** The saved routes to draw, one polyline each. */
  routes: DesignRoute[]
  /** The route the viewer picked, emphasised and framed. */
  selected: number | null
  /** Accessible description of the drawn routes. */
  label: string
  /** When set, the route being drawn/edited: handles on each point, map taps add. */
  draft?: GeoPoint[] | null
  /** The saved route the draft came from; its stored line is hidden while editing. */
  editingId?: number | null
  /** The athlete's named corridors, drawn underneath as orientation. */
  segments?: SegmentNetworkItem[]
  /** The picked variant's line, drawn above everything. */
  highlight?: GeoPoint[] | null
  /** Endpoints of a plan being picked: drawn as labelled dots, joined by a line. */
  endpoints?: GeoPoint[] | null
  /** Where to open when there is nothing to frame yet: last route's start. */
  center?: GeoPoint | null
  /** Mapped water near the picked route, drawn as dots the athlete can read. */
  water?: { point: GeoPoint; name: string | null; kind: 'font' | 'spring' }[]
  /** Whether map taps pick plan endpoints instead of doing nothing. */
  picking?: boolean
}>()

const emit = defineEmits<{
  /** The viewer clicked a route on the map. */
  select: [routeId: number]
  /** The viewer tapped empty map while a draft is open: add that point. */
  addPoint: [point: GeoPoint]
  /** The viewer picked a draft handle: remove that point. */
  removeVertex: [index: number]
  /** The viewer dragged a draft handle and released it: that point moved. */
  moveVertex: [index: number, point: GeoPoint]
  /** The viewer tapped the map while picking: that point is the next endpoint. */
  endpoint: [point: GeoPoint]
}>()

const ready = useRenderProvidersReady()
const container = ref<HTMLElement | null>(null)
let instance: MapInstance | null = null

/** Handles stop being one-per-point past this: a dense saved route would
otherwise bury the map in thousands of grabbers; the evenly sampled set
keeps every stretch editable. */
const MAX_HANDLES = 150

/** Which draft points get handles, as their original indices. */
function handleIndices(points: GeoPoint[]): number[] {
  if (points.length <= MAX_HANDLES) {
    return points.map((_, index) => index)
  }
  const step = Math.ceil(points.length / MAX_HANDLES)
  const indices: number[] = []
  for (let index = 0; index < points.length; index += step) {
    indices.push(index)
  }
  if (indices[indices.length - 1] !== points.length - 1) {
    indices.push(points.length - 1)
  }
  return indices
}

/** Stored routes plus, while editing, the draft with its clickable handles. */
function overlays(): MapOverlay[] {
  const list: MapOverlay[] = (props.segments ?? []).map((segment) => ({
    points: segment.points,
    color: themeColor(gradeTrailWay(segment.claim).colorToken),
    weight: 3,
    opacity: 0.5,
    name: segment.name,
  }))
  list.push(
    ...props.routes
      .filter((route) => route.id !== (props.editingId ?? -1))
      .map((route, index) => ({
        points: route.points,
        // the designer's own routes carry no technical claim: the brand family
        // distinguishes them from the analysed ground underneath
        color: themeColor(index % 2 === 0 ? '--color-brand' : '--color-effort'),
        weight: route.id === props.selected ? 6 : 4,
        opacity: route.id === props.selected ? 1 : 0.75,
        name: route.name,
        onSelect: () => emit('select', route.id),
      })),
  )
  if (props.endpoints && props.endpoints.length > 0) {
    list.push({
      points: props.endpoints,
      color: themeColor('--color-goal'),
      weight: 2,
      opacity: 0.9,
      markers: props.endpoints.map((point, index) => ({
        point,
        color: themeColor('--color-goal'),
        label: index === 0 ? 'A' : 'B',
      })),
    })
  }
  if (props.water && props.water.length > 0) {
    list.push({
      points: [],
      color: themeColor('--color-info'),
      markers: props.water.map((spring) => ({
        point: spring.point,
        color: themeColor('--color-info'),
        label: spring.name ?? (spring.kind === 'font' ? 'Font' : 'Deu?'),
      })),
    })
  }
  if (props.highlight && props.highlight.length > 0) {
    list.push({
      points: props.highlight,
      color: themeColor('--color-goal'),
      weight: 5,
      opacity: 0.9,
    })
  }
  if (props.draft && props.draft.length > 0) {
    const draft = props.draft
    const handles = handleIndices(draft)
    list.push({
      points: draft,
      color: themeColor('--color-effort'),
      weight: 5,
      opacity: 0.95,
      vertices: handles.flatMap((index) => {
        const point = draft[index]
        return point ? [point] : []
      }),
      onVertexClick: (handle) => {
        const original = handles[handle]
        if (original !== undefined) {
          emit('removeVertex', original)
        }
      },
      onVertexDragEnd: (handle, point) => {
        const original = handles[handle]
        if (original !== undefined) {
          emit('moveVertex', original, point)
        }
      },
    })
  }
  return list
}

function renderOptions(): MapRenderOptions {
  return {
    // no single base track: every route is an overlay, so the empty track is honest
    track: { points: [] },
    overlays: overlays(),
    onMapClick: props.draft
      ? (point) => emit('addPoint', point)
      : props.picking
        ? (point) => emit('endpoint', point)
        : undefined,
  }
}

/** Mounts the map once the provider is ready and there is anything to draw. */
function mountMap(): void {
  if (instance || !container.value || !ready.value || (props.routes.length === 0 && !props.draft)) {
    return
  }
  const { provider } = useMapProvider()
  instance = provider.mount(container.value, renderOptions())
  const points = allPoints()
  if (points.length >= 2) {
    instance.focus(points)
  } else if (props.center) {
    // a fresh draft: open near the athlete's own ground, zoomed enough to
    // see where the first click lands - never the whole world
    instance.panTo?.(props.center, 16)
  }
}

function allPoints(): GeoPoint[] {
  return [...props.routes.flatMap((route) => route.points), ...(props.draft ?? [])]
}

onMounted(mountMap)
watch(ready, mountMap)
watch(
  () => [
    props.routes,
    props.selected,
    props.draft,
    props.editingId,
    props.segments,
    props.highlight,
    props.endpoints,
    props.water,
    props.picking,
  ],
  () => {
    if (!instance) {
      mountMap()
      return
    }
    instance.update(renderOptions())
    // with no hint to open on (an athlete without saved routes yet), the
    // second point is the first frameable content: pull the view from the
    // world down to where they are actually drawing
    if (!props.center && props.draft && props.draft.length === 2) {
      instance.focus(props.draft)
    }
  },
)
watch(
  () => props.selected,
  (routeId) => {
    const route = props.routes.find((item) => item.id === routeId)
    if (route && instance) {
      instance.focus(route.points)
    }
  },
)

onBeforeUnmount(() => {
  instance?.destroy()
  instance = null
})
</script>

<template>
  <div
    ref="container"
    class="h-[60vh] min-h-[320px] w-full overflow-hidden rounded-md border"
    :aria-label="label"
  />
</template>
