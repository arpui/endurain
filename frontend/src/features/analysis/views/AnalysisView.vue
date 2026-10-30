<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import type { FitnessQuery, FitnessPoint, LoadMethod } from '@/features/analysis/types'

import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useAnalysisSettingsQuery,
  useFitnessSeriesQuery,
} from '@/features/analysis/composables/useAnalysis'
import { toIsoDate } from '@/utils/datetime'
import AnalysisDailyLoadChart from '@/features/analysis/components/AnalysisDailyLoadChart.vue'
import AnalysisPmcChart from '@/features/analysis/components/AnalysisPmcChart.vue'
import AnalysisSettingsCard from '@/features/analysis/components/AnalysisSettingsCard.vue'

const { t } = useI18n()

/** Selectable history windows, in days, in display order. */
const WINDOW_OPTIONS = [30, 90, 365]

const METHOD_OPTIONS: LoadMethod[] = ['coggan_tss', 'skiba_score', 'hr_load', 'trimp']

/** The sport groups the series can be narrowed to (plus "all sports"). */
const SPORT_OPTIONS = [
  'run',
  'bike',
  'swim',
  'walk',
  'hike',
  'rowing',
  'snow_ski',
  'snowboard',
  'windsurf',
  'stand_up_paddleboarding',
  'surfing',
  'kayaking',
  'sailing',
  'snowshoeing',
  'inline_skating',
]

const windowDays = ref(90)
const method = ref<LoadMethod>('coggan_tss')
const sport = ref('')

/** ISO date `days` before today, local calendar. */
function daysAgo(days: number): string {
  return toIsoDate(new Date(Date.now() - days * 86_400_000))
}

const seriesQuery = ref<FitnessQuery>({
  method: method.value,
  sport: undefined,
  fromDate: undefined,
  toDate: undefined,
})

/** Rebuilds the query whenever a toolbar choice changes. */
function applyQuery(): void {
  seriesQuery.value = {
    method: method.value,
    sport: sport.value === '' ? undefined : sport.value,
    fromDate: daysAgo(windowDays.value - 1),
    toDate: today(),
  }
}

function today(): string {
  return toIsoDate(new Date())
}

applyQuery()

const settingsQuery = useAnalysisSettingsQuery()
const fitnessQuery = useFitnessSeriesQuery(seriesQuery)

const points = computed<FitnessPoint[]>(() => fitnessQuery.data.value?.points ?? [])
const latest = computed<FitnessPoint | undefined>(() => points.value[points.value.length - 1])
const windowTotal = computed(() => points.value.reduce((sum, point) => sum + point.load, 0))

/** The window has no measured activity at all (nothing to plot yet). */
const isEmpty = computed(
  () =>
    !fitnessQuery.isPending.value &&
    !fitnessQuery.isError.value &&
    points.value.every((point) => point.load === 0),
)

interface StatRow {
  key: string
  value: number | undefined
  color: string
}

/** Latest reading of each curve, shown as a compact numeric summary. */
const stats = computed<StatRow[]>(() => [
  {
    key: 'analysis.pmc.seriesFitness',
    value: latest.value?.chronic,
    color: 'var(--color-brand-mid)',
  },
  { key: 'analysis.pmc.seriesFatigue', value: latest.value?.acute, color: 'var(--color-hr)' },
  { key: 'analysis.pmc.seriesForm', value: latest.value?.balance, color: 'var(--color-info)' },
  { key: 'analysis.stats.windowTotal', value: windowTotal.value, color: 'var(--color-brand)' },
])
</script>

<template>
  <section class="flex flex-col gap-3">
    <header class="flex flex-col gap-1">
      <h1 class="text-page-title">{{ t('analysis.title') }}</h1>
      <p class="text-body">{{ t('analysis.subtitle') }}</p>
    </header>

    <div class="flex flex-wrap items-center gap-2">
      <Select
        v-model="windowDays"
        :aria-label="t('analysis.window.label')"
        class="w-32"
        @change="applyQuery"
      >
        <option v-for="days in WINDOW_OPTIONS" :key="days" :value="days">
          {{ t(`analysis.window.${days}`) }}
        </option>
      </Select>
      <Select
        v-model="method"
        :aria-label="t('analysis.method.label')"
        class="w-48"
        @change="applyQuery"
      >
        <option v-for="option in METHOD_OPTIONS" :key="option" :value="option">
          {{ t(`analysis.method.${option}`) }}
        </option>
      </Select>
      <Select
        v-model="sport"
        :aria-label="t('analysis.sport.label')"
        class="w-40"
        @change="applyQuery"
      >
        <option value="">{{ t('analysis.sport.all') }}</option>
        <option v-for="option in SPORT_OPTIONS" :key="option" :value="option">
          {{ t(`home.sports.${option}`) }}
        </option>
      </Select>
    </div>

    <ErrorState v-if="fitnessQuery.isError.value" :title="t('analysis.loadFailed')" />
    <template v-else>
      <Card class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div v-for="stat in stats" :key="stat.key" class="flex flex-col gap-1">
          <span class="flex items-center gap-2 text-body text-muted-foreground">
            <span
              class="size-2.5 rounded-full"
              :style="{ backgroundColor: stat.color }"
              aria-hidden="true"
            />
            {{ t(stat.key) }}
          </span>
          <Skeleton v-if="fitnessQuery.isPending.value" class="h-6 w-16" />
          <span v-else class="text-card-heading tabular-nums">
            {{ stat.value === undefined ? '—' : Math.round(stat.value) }}
          </span>
        </div>
      </Card>

      <EmptyState
        v-if="isEmpty"
        :title="t('analysis.empty.title')"
        :description="t('analysis.empty.description')"
      />
      <template v-else>
        <AnalysisPmcChart :points="points" />
        <AnalysisDailyLoadChart :points="points" :method="method" />
      </template>
    </template>

    <Skeleton v-if="settingsQuery.isPending.value" class="h-64 w-full" />
    <AnalysisSettingsCard
      v-else-if="settingsQuery.data.value"
      :settings="settingsQuery.data.value"
    />
  </section>
</template>
