<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, useRoute } from 'vue-router'

import type { FitnessQuery, FitnessPoint, LoadMethod } from '@/features/analysis/types'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useAnalysisSettingsQuery,
  useFitnessSeriesQuery,
} from '@/features/analysis/composables/useAnalysis'
import { LOAD_METHODS, SPORT_GROUPS } from '@/features/analysis/constants'
import { toIsoDate } from '@/utils/datetime'
import AnalysisPmcChart from '@/features/analysis/components/AnalysisPmcChart.vue'
import AnalysisSettingsCard from '@/features/analysis/components/AnalysisSettingsCard.vue'

const { t } = useI18n()
const route = useRoute()

/** Selectable history windows, in days, in display order. */
const WINDOW_OPTIONS = [30, 90, 365]

const windowDays = ref(90)
const method = ref<LoadMethod>('coggan_tss')
const sport = ref('')

/** ISO date `days` before today, local calendar. */
function daysAgo(days: number): string {
  return toIsoDate(new Date(Date.now() - days * 86_400_000))
}

function today(): string {
  return toIsoDate(new Date())
}

/** Rebuilds the query whenever a toolbar choice changes. */
function buildQuery(): FitnessQuery {
  return {
    method: method.value,
    sport: sport.value === '' ? undefined : sport.value,
    fromDate: daysAgo(windowDays.value - 1),
    toDate: today(),
  }
}

const seriesQuery = ref<FitnessQuery>(buildQuery())

function applyQuery(): void {
  seriesQuery.value = buildQuery()
}

const settingsQuery = useAnalysisSettingsQuery()
const fitnessQuery = useFitnessSeriesQuery(seriesQuery)

const points = computed<FitnessPoint[]>(() => fitnessQuery.data.value?.points ?? [])
const latest = computed<FitnessPoint | undefined>(() => points.value[points.value.length - 1])
/** Average daily load over the plotted window (total load alone invites misreading). */
const dailyAverage = computed(() =>
  points.value.length === 0
    ? 0
    : points.value.reduce((sum, point) => sum + point.load, 0) / points.value.length,
)

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
  /** The three curves are what the chart is read for; the rest is context. */
  emphasis: boolean
}

/** Latest reading of each curve, shown as a compact numeric summary. */
const stats = computed<StatRow[]>(() => [
  {
    key: 'analysis.pmc.seriesFitness',
    value: latest.value?.chronic,
    color: 'var(--color-brand-mid)',
    emphasis: true,
  },
  {
    key: 'analysis.pmc.seriesFatigue',
    value: latest.value?.acute,
    color: 'var(--color-hr)',
    emphasis: true,
  },
  {
    key: 'analysis.pmc.seriesForm',
    value: latest.value?.balance,
    color: 'var(--color-info)',
    emphasis: true,
  },
  {
    key: 'analysis.stats.dailyAvg',
    value: dailyAverage.value,
    color: 'var(--color-brand)',
    emphasis: false,
  },
])

/** Parameters the server actually used, so the chart can label itself. */
const seriesParams = computed(() => ({
  method: fitnessQuery.data.value?.method ?? method.value,
  acuteDays: fitnessQuery.data.value?.acuteDays ?? 0,
  chronicDays: fitnessQuery.data.value?.chronicDays ?? 0,
}))
</script>

<template>
  <section class="flex flex-col gap-3">
    <header class="flex flex-col gap-1">
      <h1 class="text-page-title">{{ t('analysis.title') }}</h1>
      <p class="text-body">{{ t('analysis.subtitle') }}</p>
    </header>

    <nav class="flex gap-1" :aria-label="t('analysis.sections')">
      <RouterLink v-slot="{ href, navigate }" :to="{ name: 'analysis' }" custom>
        <Button
          as="a"
          :href="href"
          :variant="route.name === 'analysis' ? 'default' : 'ghost'"
          @click="navigate"
        >
          {{ t('analysis.sectionLoad') }}
        </Button>
      </RouterLink>
      <RouterLink v-slot="{ href, navigate }" :to="{ name: 'analysis-power' }" custom>
        <Button
          as="a"
          :href="href"
          :variant="route.name === 'analysis-power' ? 'default' : 'ghost'"
          @click="navigate"
        >
          {{ t('analysis.sectionPower') }}
        </Button>
      </RouterLink>
    </nav>

    <div class="flex flex-wrap items-center gap-2">
      <Select
        v-model="windowDays"
        :aria-label="t('analysis.window.label')"
        class="w-28"
        @change="applyQuery"
      >
        <option v-for="days in WINDOW_OPTIONS" :key="days" :value="days">
          {{ t(`analysis.window.${days}`) }}
        </option>
      </Select>
      <Select
        v-model="method"
        :aria-label="t('analysis.method.label')"
        class="w-64"
        @change="applyQuery"
      >
        <option v-for="option in LOAD_METHODS" :key="option" :value="option">
          {{ t(`analysis.method.${option}`) }}
        </option>
      </Select>
      <Select
        v-model="sport"
        :aria-label="t('analysis.sport.label')"
        class="w-52"
        @change="applyQuery"
      >
        <option value="">{{ t('analysis.sport.all') }}</option>
        <option v-for="option in SPORT_GROUPS" :key="option" :value="option">
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
          <span
            v-else
            class="tabular-nums"
            :class="stat.emphasis ? 'text-3xl font-semibold' : 'text-body text-muted-foreground'"
          >
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
        <AnalysisPmcChart
          :points="points"
          :method="seriesParams.method"
          :acute-days="seriesParams.acuteDays"
          :chronic-days="seriesParams.chronicDays"
        />
      </template>
    </template>

    <Skeleton v-if="settingsQuery.isPending.value" class="h-64 w-full" />
    <AnalysisSettingsCard
      v-else-if="settingsQuery.data.value"
      :settings="settingsQuery.data.value"
    />
  </section>
</template>
