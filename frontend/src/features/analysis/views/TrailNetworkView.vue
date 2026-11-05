<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Skeleton } from '@/components/ui/skeleton'
import AnalysisSectionsNav from '@/features/analysis/components/AnalysisSectionsNav.vue'
import TrailNetworkMap from '@/features/analysis/components/TrailNetworkMap.vue'
import { useTrailNetworkQuery } from '@/features/analysis/composables/useAnalysis'
import { gradeTrailWay } from '@/features/analysis/utils/trailDegree'
import { themeColor } from '@/lib/themeColor'

const { t, locale } = useI18n()

const networkQuery = useTrailNetworkQuery()
const network = computed(() => networkQuery.data.value ?? null)

/** The way the viewer picked, on the map or in the table. */
const selected = ref<number | null>(null)

function pick(wayId: number): void {
  selected.value = selected.value === wayId ? null : wayId
}

const numberFormat = computed(
  () => new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }),
)

function score(value: number): string {
  return numberFormat.value.format(value)
}

function spread(way: { q1: number; q3: number }): string {
  return `${numberFormat.value.format(way.q1)}–${numberFormat.value.format(way.q3)}`
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

    <ErrorState v-if="networkQuery.isError.value" :title="t('analysis.loadFailed')" />

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
              <td class="py-2 pe-3 text-right">{{ way.descentCrossings }}</td>
              <td class="py-2 pe-3 text-right">{{ score(way.medScore) }}</td>
              <td class="py-2 pe-3 text-right text-muted-foreground">{{ spread(way) }}</td>
              <td class="py-2 pe-3 text-right">{{ way.medGradePct.toFixed(1) }}%</td>
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
