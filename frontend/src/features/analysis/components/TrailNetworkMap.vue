<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type { GeoPoint, MapInstance, MapOverlay } from '@/composables/useMapProvider'
import type { TrailClaim, TrailNetworkWay } from '@/features/analysis/types'

import { gradeTrailWay } from '@/features/analysis/utils/trailDegree'
import { useMapProvider } from '@/composables/useMapProvider'
import { themeColor } from '@/lib/themeColor'
import { useRenderProvidersReady } from '@/providers'

const props = defineProps<{
  /** The crossed ways to draw, one polyline each. */
  ways: TrailNetworkWay[]
  /** The way the viewer picked, emphasised and framed. */
  selected: number | null
  /** Accessible description of the drawn network. */
  label: string
}>()

const emit = defineEmits<{
  /** The viewer clicked a way on the map. */
  select: [wayId: number]
}>()

const { t } = useI18n()
const ready = useRenderProvidersReady()
const container = ref<HTMLElement | null>(null)
let instance: MapInstance | null = null

/** The four claims, in map order: what the colours mean. */
const LEGEND: TrailClaim[] = ['hard', 'technical', 'mild', 'thin']

/** One coloured polyline per way; the picked one rides at full opacity. */
function overlays(): MapOverlay[] {
  return props.ways.map((way) => {
    const grade = gradeTrailWay(way.claim)
    return {
      points: way.points,
      color: themeColor(grade.colorToken),
      weight: grade.weight,
      opacity: way.wayId === props.selected ? 1 : grade.opacity,
      name: way.name ?? `#${way.wayId}`,
      onSelect: () => emit('select', way.wayId),
    }
  })
}

/** Mounts the map once the provider is ready and there are ways to draw. */
function mountMap(): void {
  if (instance || !container.value || !ready.value || props.ways.length === 0) {
    return
  }
  const { provider } = useMapProvider()
  // no single base track: every way is an overlay, so the empty track is honest
  instance = provider.mount(container.value, { track: { points: [] }, overlays: overlays() })
  instance.focus(allPoints())
}

function allPoints(): GeoPoint[] {
  return props.ways.flatMap((way) => way.points)
}

onMounted(mountMap)
watch(ready, mountMap)
watch(
  () => [props.ways, props.selected],
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
  (wayId) => {
    const way = props.ways.find((item) => item.wayId === wayId)
    if (way && instance) {
      instance.focus(way.points)
    }
  },
)

onBeforeUnmount(() => {
  instance?.destroy()
  instance = null
})
</script>

<template>
  <div class="flex flex-col gap-2">
    <div
      ref="container"
      class="h-[60vh] min-h-[320px] w-full overflow-hidden rounded-md border"
      :aria-label="label"
    />
    <div class="flex flex-wrap items-center gap-4">
      <span
        v-for="claim in LEGEND"
        :key="claim"
        class="flex items-center gap-1.5 text-caption text-muted-foreground"
      >
        <span
          class="inline-block size-3 rounded-full"
          :style="{ backgroundColor: themeColor(gradeTrailWay(claim).colorToken) }"
        />
        {{ t(gradeTrailWay(claim).labelKey) }}
      </span>
    </div>
  </div>
</template>
