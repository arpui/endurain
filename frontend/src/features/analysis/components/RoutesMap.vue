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
  /** Endpoints of a plan being picked: drawn as handles, joined by a line. */
  endpoints?: GeoPoint[] | null
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
  /** The viewer tapped the map while picking: that point is the next endpoint. */
  endpoint: [point: GeoPoint]
}>()

const ready = useRenderProvidersReady()
const container = ref<HTMLElement | null>(null)
let instance: MapInstance | null = null

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
      vertices: props.endpoints,
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
    list.push({
      points: props.draft,
      color: themeColor('--color-effort'),
      weight: 5,
      opacity: 0.95,
      vertices: props.draft,
      onVertexClick: (index) => emit('removeVertex', index),
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
  instance.focus(allPoints())
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
  ],
  () => {
    if (!instance) {
      mountMap()
      return
    }
    instance.update(renderOptions())
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
