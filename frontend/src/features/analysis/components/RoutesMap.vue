<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { DesignRoute } from '@/features/analysis/types'
import type { GeoPoint, MapInstance, MapOverlay } from '@/composables/useMapProvider'

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
}>()

const emit = defineEmits<{
  /** The viewer clicked a route on the map. */
  select: [routeId: number]
}>()

const ready = useRenderProvidersReady()
const container = ref<HTMLElement | null>(null)
let instance: MapInstance | null = null

/** One coloured polyline per route; the picked one rides at full weight. */
function overlays(): MapOverlay[] {
  return props.routes.map((route, index) => ({
    points: route.points,
    // the designer's own routes carry no technical claim: the brand family
    // distinguishes them from the analysed ground underneath
    color: themeColor(index % 2 === 0 ? '--color-brand' : '--color-effort'),
    weight: route.id === props.selected ? 6 : 4,
    opacity: route.id === props.selected ? 1 : 0.75,
    name: route.name,
    onSelect: () => emit('select', route.id),
  }))
}

/** Mounts the map once the provider is ready and there are routes to draw. */
function mountMap(): void {
  if (instance || !container.value || !ready.value || props.routes.length === 0) {
    return
  }
  const { provider } = useMapProvider()
  // no single base track: every route is an overlay, so the empty track is honest
  instance = provider.mount(container.value, { track: { points: [] }, overlays: overlays() })
  instance.focus(allPoints())
}

function allPoints(): GeoPoint[] {
  return props.routes.flatMap((route) => route.points)
}

onMounted(mountMap)
watch(ready, mountMap)
watch(
  () => [props.routes, props.selected],
  () => {
    if (!instance) {
      mountMap()
      return
    }
    instance.update({ track: { points: [] }, overlays: overlays() })
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
