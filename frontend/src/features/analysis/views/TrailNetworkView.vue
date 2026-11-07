<script setup lang="ts">
import { computed, ref } from 'vue'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useI18n } from 'vue-i18n'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Skeleton } from '@/components/ui/skeleton'
import AnalysisSectionsNav from '@/features/analysis/components/AnalysisSectionsNav.vue'
import TrailNetworkMap from '@/features/analysis/components/TrailNetworkMap.vue'
import {
  useSegmentNetworkQuery,
  useTrailNetworkQuery,
} from '@/features/analysis/composables/useAnalysis'
import type { TrailNetworkWay } from '@/features/analysis/types'
import { gradeTrailWay } from '@/features/analysis/utils/trailDegree'
import { themeColor } from '@/lib/themeColor'

const { t, locale } = useI18n()

/** Which layer of the network to look at: ground the map names, ground they name. */
const view = ref<'ways' | 'segments'>('ways')
/** Whether the map also draws crossed ways that carry no claim. */
const showPlain = ref(true)
/** Whether short closed circuits (a lap of a field) count as network. */
const showLoops = ref(false)
const segmentQuery = useSegmentNetworkQuery(
  computed(() => view.value === 'segments'),
  showLoops,
)
const segments = computed(() => segmentQuery.data.value?.segments ?? [])
// The segment map speaks the way map's language: same claim bands, same colours.
const segmentWays = computed<TrailNetworkWay[]>(() =>
  segments.value.map((segment): TrailNetworkWay => ({
    wayId: segment.segmentId,
    name: segment.name,
    claim: segment.claim,
    crossingsTotal: segment.crossingsTotal,
    descentCrossings: segment.descentCrossings,
    ascentCrossings: 0,
    hardCrossings: 0,
    medScore: segment.medScore,
    q1: segment.q1,
    q3: segment.q3,
    minScore: null,
    maxScore: null,
    medGradePct: segment.medGradePct,
    lengthM: segment.lengthM,
    points: segment.points,
    sacScale: null,
    mtbScale: null,
    surface: null,
    tracktype: null,
  })),
)
const limit = computed(() => (showPlain.value ? 5000 : 1500))
const networkQuery = useTrailNetworkQuery(limit)
const network = computed(() => networkQuery.data.value ?? null)

/** The way the viewer picked, on the map or in the table. */
const selected = ref<number | null>(null)

function pick(wayId: number): void {
  selected.value = selected.value === wayId ? null : wayId
}

const numberFormat = computed(
  () => new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }),
)

function score(value: number | null): string {
  return value === null ? '--' : numberFormat.value.format(value)
}

function spread(way: { q1: number | null; q3: number | null }): string {
  return way.q1 === null || way.q3 === null
    ? '--'
    : `${numberFormat.value.format(way.q1)}–${numberFormat.value.format(way.q3)}`
}

function grade(value: number | null): string {
  return value === null ? '--' : `${value.toFixed(1)}%`
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-col gap-1">
      <div class="flex items-center gap-2">
        <h1 class="text-page-title">{{ t('analysis.trailNetwork.title') }}</h1>
        <Badge variant="secondary">{{ t('analysis.trail.experimental') }}</Badge>
      </div>
      <p class="text-body">{{ t('analysis.trailNetwork.subtitle') }}</p>
    </header>

    <AnalysisSectionsNav />

    <Tabs :model-value="view" @update:model-value="view = $event as 'ways' | 'segments'">
      <TabsList>
        <TabsTrigger value="ways">{{ t('analysis.trailNetwork.tabWays') }}</TabsTrigger>
        <TabsTrigger value="segments">{{ t('analysis.trailNetwork.tabSegments') }}</TabsTrigger>
      </TabsList>
    </Tabs>

    <label
      v-if="view === 'ways'"
      class="flex items-center gap-2 text-caption text-muted-foreground"
    >
      <Switch v-model="showPlain" />
      {{ t('analysis.trailNetwork.showPlain') }}
    </label>
    <label v-else class="flex items-center gap-2 text-caption text-muted-foreground">
      <Switch v-model="showLoops" />
      {{ t('analysis.trailNetwork.showLoops') }}
    </label>

    <template v-if="view === 'segments'">
      <ErrorState v-if="segmentQuery.isError.value" :title="t('analysis.loadFailed')" />
      <Skeleton v-else-if="segmentQuery.isPending.value" class="h-64 w-full" />
      <template v-else>
        <Card v-if="segments.length === 0" class="p-3">
          <EmptyState
            :title="t('analysis.trailNetwork.empty.title')"
            :description="t('analysis.trailNetwork.empty.description')"
          />
        </Card>
        <Card v-else class="p-3">
          <TrailNetworkMap
            :ways="segmentWays"
            :selected="selected"
            :label="t('analysis.trailNetwork.mapLabel')"
            @select="pick"
          />
        </Card>
        <Card v-if="segments.length > 0" class="overflow-x-auto p-3">
          <table class="w-full text-body">
            <thead>
              <tr class="border-b text-left text-caption text-muted-foreground">
                <th class="py-2 font-medium">{{ t('analysis.trailNetwork.colSegment') }}</th>
                <th class="py-2 font-medium">{{ t('analysis.trailNetwork.colClaim') }}</th>
                <th class="py-2 pe-3 text-right font-medium">
                  {{ t('analysis.trailNetwork.colDescents') }}
                </th>
                <th class="py-2 pe-3 text-right font-medium">
                  {{ t('analysis.trailNetwork.colMedian') }}
                </th>
                <th class="py-2 pe-3 text-right font-medium">
                  {{ t('analysis.trailNetwork.colSpread') }}
                </th>
                <th class="py-2 pe-3 text-right font-medium">
                  {{ t('analysis.trailNetwork.colGrade') }}
                </th>
                <th class="py-2 pe-3 text-right font-medium">
                  {{ t('analysis.trailNetwork.colLength') }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="segment in segments"
                :key="segment.segmentId"
                class="cursor-pointer border-b last:border-0 hover:bg-muted/50"
                :class="{ 'bg-muted/60': selected === segment.segmentId }"
                @click="pick(segment.segmentId)"
              >
                <td class="py-2">
                  <span
                    class="me-2 inline-block size-2.5 rounded-full align-middle"
                    :style="{
                      backgroundColor: themeColor(gradeTrailWay(segment.claim).colorToken),
                    }"
                  />
                  {{ segment.name }}
                  <span v-if="segment.loop" class="ms-1 text-caption text-muted-foreground">
                    ({{ t('analysis.trailNetwork.loop') }})
                  </span>
                </td>
                <td class="py-2">{{ t(gradeTrailWay(segment.claim).labelKey) }}</td>
                <td class="py-2 pe-3 text-right">{{ segment.crossingsTotal }}</td>
                <td class="py-2 pe-3 text-right">{{ segment.descentCrossings }}</td>
                <td class="py-2 pe-3 text-right">{{ score(segment.medScore) }}</td>
                <td class="py-2 pe-3 text-right text-muted-foreground">{{ spread(segment) }}</td>
                <td class="py-2 pe-3 text-right">{{ grade(segment.medGradePct) }}</td>
                <td class="py-2 pe-3 text-right">{{ Math.round(segment.lengthM) }} m</td>
              </tr>
            </tbody>
          </table>
          <p class="mt-2 text-caption text-muted-foreground">
            {{
              t('analysis.trailNetwork.hint', { min: segmentQuery.data.value?.minDescents ?? 3 })
            }}
          </p>
        </Card>
      </template>
    </template>

    <ErrorState v-else-if="networkQuery.isError.value" :title="t('analysis.loadFailed')" />

    <Skeleton v-else-if="networkQuery.isPending.value" class="h-64 w-full" />

    <template v-else-if="network">
      <Card v-if="network.ways.length === 0" class="p-3">
        <EmptyState
          :title="t('analysis.trailNetwork.empty.title')"
          :description="t('analysis.trailNetwork.empty.description')"
        />
      </Card>
      <Card v-else class="p-3">
        <TrailNetworkMap
          :ways="network.ways"
          :selected="selected"
          :label="t('analysis.trailNetwork.mapLabel')"
          @select="pick"
        />
      </Card>

      <Card v-if="network && network.ways.length > 0" class="overflow-x-auto p-3">
        <table class="w-full text-body">
          <thead>
            <tr class="border-b text-left text-caption text-muted-foreground">
              <th class="py-2 font-medium">{{ t('analysis.trailNetwork.colWay') }}</th>
              <th class="py-2 font-medium">{{ t('analysis.trailNetwork.colClaim') }}</th>
              <th class="py-2 pe-3 text-right font-medium">
                {{ t('analysis.trailNetwork.colDescents') }}
              </th>
              <th class="py-2 pe-3 text-right font-medium">
                {{ t('analysis.trailNetwork.colMedian') }}
              </th>
              <th class="py-2 pe-3 text-right font-medium">
                {{ t('analysis.trailNetwork.colSpread') }}
              </th>
              <th class="py-2 pe-3 text-right font-medium">
                {{ t('analysis.trailNetwork.colGrade') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="way in network.ways"
              :key="way.wayId"
              class="cursor-pointer border-b last:border-0 hover:bg-muted/50"
              :class="{ 'bg-muted/60': selected === way.wayId }"
              @click="pick(way.wayId)"
            >
              <td class="py-2">
                <span
                  class="me-2 inline-block size-2.5 rounded-full align-middle"
                  :style="{ backgroundColor: themeColor(gradeTrailWay(way.claim).colorToken) }"
                />
                {{ way.name ?? `#${way.wayId}` }}
                <span v-if="way.ascentCrossings > 0" class="text-caption text-muted-foreground">
                  (+{{ way.ascentCrossings }})
                </span>
              </td>
              <td class="py-2">{{ t(gradeTrailWay(way.claim).labelKey) }}</td>
              <td class="py-2 pe-3 text-right">{{ way.crossingsTotal }}</td>
              <td class="py-2 pe-3 text-right">{{ way.descentCrossings }}</td>
              <td class="py-2 pe-3 text-right">{{ score(way.medScore) }}</td>
              <td class="py-2 pe-3 text-right text-muted-foreground">{{ spread(way) }}</td>
              <td class="py-2 pe-3 text-right">{{ grade(way.medGradePct) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="mt-2 text-caption text-muted-foreground">
          {{ t('analysis.trailNetwork.hint', { min: network.minDescents }) }}
        </p>
      </Card>
    </template>
  </div>
</template>
