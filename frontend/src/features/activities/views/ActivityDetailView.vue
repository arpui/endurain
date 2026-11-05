<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, useRoute } from 'vue-router'
import { ArrowLeft } from '@lucide/vue'

import { STREAM_TYPE } from '@/features/activities/types'
import type { ActivityOwner, HrZoneBucket } from '@/features/activities/types'
import type { StreamChart } from '@/features/activities/utils/streams'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ErrorState } from '@/components/ui/error-state'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useCurrentUser } from '@/features/auth/composables/useCurrentUser'
import { activityTypeIsRunning } from '@/features/activities/utils/activityType'
import { gradeTrailSection, pointsWithin } from '@/features/analysis/utils/trailDegree'
import type { MapOverlay } from '@/composables/useMapProvider'
import type { TrailSection, TrailVerdict } from '@/features/analysis/types'
import { themeColor } from '@/lib/themeColor'
import ActivityDetailHeader from '@/features/activities/components/ActivityDetailHeader.vue'
import ActivityIntervalTable from '@/features/analysis/components/ActivityIntervalTable.vue'
import ActivityLoadScores from '@/features/analysis/components/ActivityLoadScores.vue'
import LapDynamicsTable from '@/features/analysis/components/LapDynamicsTable.vue'
import TrailSectionsCard from '@/features/analysis/components/TrailSectionsCard.vue'
import {
  useActivityIntervalsQuery,
  useLapDynamicsQuery,
  useTrailSectionMarkMutation,
  useTrailSectionsQuery,
} from '@/features/analysis/composables/useAnalysis'
import ActivityGallery from '@/features/activities/components/ActivityGallery.vue'
import ActivityGearCard from '@/features/activities/components/ActivityGearCard.vue'
import ActivityHrZones from '@/features/activities/components/ActivityHrZones.vue'
import ActivityLapsTable from '@/features/activities/components/ActivityLapsTable.vue'
import ActivityMetricsGrid from '@/features/activities/components/ActivityMetricsGrid.vue'
import ActivityPrivacyAlerts from '@/features/activities/components/ActivityPrivacyAlerts.vue'
import ActivitySegmentsTable from '@/features/activities/components/ActivitySegmentsTable.vue'
import ActivityStreamChart from '@/features/activities/components/ActivityStreamChart.vue'
import ActivityWorkoutSets from '@/features/activities/components/ActivityWorkoutSets.vue'
import SegmentMatchDebugDialog from '@/features/segments/components/SegmentMatchDebugDialog.vue'
import {
  useActivityExerciseTitlesQuery,
  useActivityLapsQuery,
  useActivityOwnerQuery,
  useActivityQuery,
  useActivitySegmentsQuery,
  useActivitySetsQuery,
  useActivityStreamsQuery,
  useActivityWorkoutStepsQuery,
  useDisplayUnits,
} from '@/features/activities/composables/useActivityDetail'
import {
  buildMetricVisibility,
  canViewField,
  isActivityOwner,
  streamMetricVisibility,
} from '@/features/activities/utils/privacy'
import { extractHrZones } from '@/features/activities/utils/hrZones'
import { buildStreamCharts, extractTrackPoints } from '@/features/activities/utils/streams'

const route = useRoute()
const { t } = useI18n()
const { data: currentUser } = useCurrentUser()
const units = useDisplayUnits()

const activityId = computed(() => {
  const raw = Number(route.params.id)
  return Number.isFinite(raw) && raw > 0 ? raw : null
})

const { data: activity, isPending, isError, refetch } = useActivityQuery(activityId)

const activityLoaded = computed(() => !!activity.value)
const { data: streams } = useActivityStreamsQuery(activityId, activityLoaded)
const { data: laps } = useActivityLapsQuery(activityId, activityLoaded)

// Segment efforts are only readable by an authenticated viewer whose access to
// the activity the backend has resolved, so a public share shows no section.
const { data: segments } = useActivitySegmentsQuery(activityId, activityLoaded)

const currentUserId = computed(() => currentUser.value?.id ?? null)

// Only the owner manages gear; non-owners (incl. public viewers) never see the
// gear card.
const isOwner = computed(
  () => !!activity.value && isActivityOwner(activity.value, currentUserId.value),
)

// The interval breakdown is measured against this athlete's own thresholds, so only
// they see it, and the query is not issued at all for public viewers.
const { data: intervals } = useActivityIntervalsQuery(
  activityId,
  computed(() => activityLoaded.value && isOwner.value),
)
const showIntervals = computed(
  () =>
    ((intervals.value?.segments.length ?? 0) > 0 && intervals.value?.thresholdW != null) ||
    // Terrain climbs need only altitude and speed: they answer for athletes
    // without a power meter too.
    (intervals.value?.climbs.length ?? 0) > 0,
)
// Which stretches of a trail or run read as technical terrain (experimental):
// same owner gate as the intervals; the endpoint answers empty for non-run sports.
const isRunActivity = computed(
  () => !!activity.value && activityTypeIsRunning(activity.value.activityType),
)
const { data: trailSections } = useTrailSectionsQuery(
  activityId,
  computed(() => activityLoaded.value && isOwner.value && isRunActivity.value),
)
const showTrailSections = computed(() => (trailSections.value?.sections.length ?? 0) > 0)
// The section both surfaces point at: chosen from the table or from the map.
const trailSelected = ref<number | null>(null)
// Technical stretches over the track, coloured by how assertively the detector
// claims each one (grades read against the threshold the server itself used).
const mapOverlays = computed(() => {
  const sections = trailSections.value?.sections ?? []
  const ratios = {
    threshold: trailSections.value?.threshold ?? 0,
    technicalRatio: trailSections.value?.technicalRatio ?? 1.3,
    hardRatio: trailSections.value?.hardRatio ?? 1.8,
    veryMaxLengthM: trailSections.value?.veryMaxLengthM ?? 200,
  }
  const overlays: MapOverlay[] = []
  sections.forEach((section, index) => {
    const grade = gradeTrailSection(section, ratios)
    const isSelected = trailSelected.value === section.startM
    overlays.push({
      points: section.points,
      color: themeColor(grade.colorToken),
      weight: isSelected ? grade.weight + 3 : grade.weight,
      opacity: isSelected ? 1 : grade.opacity,
      name: [
        `#${index + 1}`,
        `${(section.startM / 1000).toFixed(2)} km`,
        section.osm?.sacScale ?? section.osm?.mtbScale ?? section.osm?.surface,
      ]
        .filter(Boolean)
        .join(' · '),
      onSelect: () => {
        trailSelected.value = section.startM
      },
    })
    // the hard block is painted as itself: an extreme adjective over a kilometre
    // and a half of mixed ground is what makes the claim read wrong
    if (section.coreScore !== null && section.coreStartM !== null && section.coreEndM !== null) {
      overlays.push({
        points: pointsWithin(
          section.points,
          section.lengthM,
          section.coreStartM - section.startM,
          section.coreEndM - section.startM,
        ),
        color: themeColor('--color-hr'),
        weight: 7,
        opacity: 0.95,
        name: `#${index + 1} · ${t('analysis.trail.degree.veryTechnical')}`,
        onSelect: () => {
          trailSelected.value = section.startM
        },
      })
    }
  })
  return overlays
})
// Where the map should travel when a row is picked.
const trailFocus = computed(() => {
  const section = (trailSections.value?.sections ?? []).find(
    (candidate) => candidate.startM === trailSelected.value,
  )
  return section ? section.points : []
})
const trailMarks = useTrailSectionMarkMutation(activityId)
/** Judge a stretch: agreed, overstated, or never technical (``null`` takes it back). */
function markTrailSection(startM: number, verdict: TrailVerdict | null): void {
  trailMarks.mutate({ startM, verdict })
}

const galleryElement = ref<HTMLElement | null>(null)
/** Table row click: select the section, move the map to it and bring it into view. */
function selectTrailSection(section: TrailSection): void {
  trailSelected.value = section.startM
  galleryElement.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
// The analysis laps table replaces the plain one for the owner: same rows, plus
// power, stride length, the lap's ascent-and-descent pair and its share of the aRSS.
const { data: lapDynamics } = useLapDynamicsQuery(
  activityId,
  computed(() => activityLoaded.value && isOwner.value),
)

const ownerQuery = useActivityOwnerQuery(activity)
const owner = computed<ActivityOwner | null>(() => {
  if (!activity.value) {
    return null
  }
  // Self: build the identity from the cached current user.
  if (currentUser.value && isActivityOwner(activity.value, currentUserId.value)) {
    return {
      name: currentUser.value.name,
      username: currentUser.value.username,
      avatarUrl: currentUser.value.avatarUrl,
    }
  }
  // Other viewers (authenticated or public): the fetched owner, or null when
  // the "show user info on public links" setting withholds it.
  return ownerQuery.data.value ?? null
})

const metricVisibility = computed(() =>
  activity.value ? buildMetricVisibility(activity.value, currentUserId.value) : null,
)

const trackPoints = computed(() => extractTrackPoints(streams.value ?? []))
const showMap = computed(
  () =>
    !!activity.value &&
    canViewField(activity.value, 'hideMap', currentUserId.value) &&
    trackPoints.value.length > 0,
)

const streamCharts = computed(() => {
  if (!activity.value) {
    return []
  }
  return buildStreamCharts(
    streams.value ?? [],
    activity.value,
    units.value,
    streamMetricVisibility(activity.value, currentUserId.value),
  )
})

const showLaps = computed(
  () =>
    !!activity.value &&
    canViewField(activity.value, 'hideLaps', currentUserId.value) &&
    (laps.value?.length ?? 0) > 0,
)

const showSegments = computed(() => !!activity.value && (segments.value?.length ?? 0) > 0)

// The owner can always open the segment match-debug trail: it answers why an
// activity scored (or missed) the nearby segments, empty list included.
const segmentsDebugOpen = ref(false)

// Stryd-era power streams are served recalibrated to the athlete's current
// scale (the backend applies the stored factor at read); flag those activities
// so a wattage is never read as if it were the older pod scale.
const powerRecalibrated = computed(() =>
  (streams.value ?? []).some(
    (s) => s.streamType === STREAM_TYPE.power && s.powerSource === 'stryd',
  ),
)

// Workout steps/sets. Fetched for every activity (like laps/streams); the
// section self-hides when there is no data, mirroring v1 which fetches these
// regardless of activity type and lets data presence drive display.
const { data: workoutSteps } = useActivityWorkoutStepsQuery(activityId, activityLoaded)
const { data: workoutSets } = useActivitySetsQuery(activityId, activityLoaded)
const hasWorkoutData = computed(
  () => (workoutSteps.value?.length ?? 0) > 0 || (workoutSets.value?.length ?? 0) > 0,
)
// The exercise-name catalogue is only needed once we know workout data exists.
const { data: exerciseTitles } = useActivityExerciseTitlesQuery(hasWorkoutData)

const showWorkout = computed(
  () =>
    !!activity.value &&
    canViewField(activity.value, 'hideWorkoutSetsSteps', currentUserId.value) &&
    hasWorkoutData.value,
)

// Active tab when both laps and workout sets are available (v1's pill switch).
const splitsTab = ref('laps')

// HR zones live on the HR stream; gate them by the same HR visibility as the
// HR chart so a privacy-restricted viewer never sees them.
const hrZones = computed(() => extractHrZones(streams.value ?? []))
const showHrZones = computed(
  () =>
    !!activity.value &&
    canViewField(activity.value, 'hideHr', currentUserId.value) &&
    (hrZones.value?.length ?? 0) > 0,
)

// One chart-grid panel: either a metric chart or the HR-zones card. The HR
// zones are inserted immediately after the HR chart so they sit side by side
// in the two-column grid.
type ChartGridPanel =
  | { kind: 'chart'; key: string; chart: StreamChart }
  | { kind: 'hr-zones'; key: string; zones: HrZoneBucket[] }

const chartPanels = computed<ChartGridPanel[]>(() => {
  const panels: ChartGridPanel[] = []
  const zones = showHrZones.value ? hrZones.value : null
  let zonesInserted = false
  for (const chart of streamCharts.value) {
    panels.push({ kind: 'chart', key: chart.metric, chart })
    if (chart.metric === 'hr' && zones) {
      panels.push({ kind: 'hr-zones', key: 'hr-zones', zones })
      zonesInserted = true
    }
  }
  // HR zones present but no HR chart (unlikely) — still surface them.
  if (zones && !zonesInserted) {
    panels.push({ kind: 'hr-zones', key: 'hr-zones', zones })
  }
  return panels
})

// `data` is `null` (not `undefined`) once a fetch resolves to a missing or
// non-shareable activity; distinguish that from the still-loading state.
const notFound = computed(() => !isPending.value && !isError.value && activity.value === null)
</script>

<template>
  <div class="flex flex-col gap-3">
    <Button variant="ghost" size="sm" class="self-start" @click="$router.back()">
      <ArrowLeft class="size-4" />
      {{ t('activities.back') }}
    </Button>

    <!-- Loading: mirror the summary (athlete header + metrics), the map, the
         charts grid, and the splits table so the placeholder matches the load. -->
    <template v-if="isPending">
      <!-- Summary: athlete header + at-a-glance metrics (one card, as loaded). -->
      <Card class="flex flex-col gap-3" aria-busy="true">
        <div class="flex items-start justify-between gap-3">
          <div class="flex min-w-0 items-center gap-3">
            <Skeleton class="size-14 shrink-0 rounded-full" />
            <div class="space-y-2">
              <Skeleton class="h-4 w-32" />
              <Skeleton class="h-5 w-40 rounded-full" />
              <Skeleton class="h-3 w-48" />
            </div>
          </div>
          <Skeleton class="size-8 shrink-0 rounded-input" />
        </div>
        <Skeleton class="h-7 w-2/3" />
        <div class="border-t border-border pt-4">
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Skeleton v-for="n in 6" :key="n" class="h-16 rounded-input" />
          </div>
        </div>
      </Card>

      <!-- Map zone: matches the gallery's height. -->
      <Skeleton class="h-72 w-full rounded-card lg:h-150" />

      <!-- Charts grid: two-column stream-chart placeholders. -->
      <div class="grid gap-3 lg:grid-cols-2" aria-busy="true">
        <Card v-for="n in 4" :key="n" class="flex min-w-0 flex-col gap-3">
          <div class="flex items-center gap-2">
            <Skeleton class="size-2.5 rounded-full" />
            <Skeleton class="h-5 w-32" />
          </div>
          <Skeleton class="h-56 w-full rounded-input" />
          <div class="flex flex-wrap gap-x-6 gap-y-2">
            <div class="space-y-1">
              <Skeleton class="h-3 w-12" />
              <Skeleton class="h-4 w-16" />
            </div>
            <div class="space-y-1">
              <Skeleton class="h-3 w-12" />
              <Skeleton class="h-4 w-16" />
            </div>
          </div>
        </Card>
      </div>

      <!-- Splits/laps table. -->
      <Card class="flex flex-col gap-3" aria-busy="true">
        <Skeleton class="h-5 w-24" />
        <div class="space-y-2">
          <Skeleton v-for="n in 4" :key="n" class="h-8 w-full" />
        </div>
      </Card>
    </template>

    <!-- Network error -->
    <ErrorState
      v-else-if="isError"
      :title="t('activities.error.title')"
      :description="t('activities.error.description')"
    >
      <template #action="{ retry }">
        <Button
          variant="outline"
          size="sm"
          @click="
            () => {
              retry()
              refetch()
            }
          "
        >
          {{ t('activities.error.retry') }}
        </Button>
      </template>
    </ErrorState>

    <!-- Not found / not shareable -->
    <Card v-else-if="notFound" class="flex flex-col items-center gap-3 py-12 text-center">
      <p class="text-item-title">{{ t('activities.notFound.title') }}</p>
      <p class="max-w-sm text-body">
        {{ t('activities.notFound.description') }}
      </p>
      <Button as-child variant="outline" size="sm">
        <RouterLink :to="{ name: 'home' }">{{ t('activities.notFound.action') }}</RouterLink>
      </Button>
    </Card>

    <!-- Content -->
    <template v-else-if="activity && metricVisibility">
      <!-- Summary: title/user + at-a-glance metrics, before the map (v1 order). -->
      <Card class="flex flex-col gap-3">
        <ActivityDetailHeader
          :activity="activity"
          :units="units"
          :current-user-id="currentUserId"
          :owner="owner"
        />
        <div class="border-t border-border pt-4">
          <ActivityMetricsGrid :activity="activity" :units="units" :visibility="metricVisibility" />
          <p v-if="powerRecalibrated" class="text-caption mt-2 text-muted-foreground">
            * {{ t('activities.power.recalibrated') }}
          </p>
          <ActivityLoadScores :activity-id="activityId" />
        </div>
      </Card>

      <ActivityPrivacyAlerts :activity="activity" :current-user-id="currentUserId" />

      <div ref="galleryElement">
        <ActivityGallery
          :points="showMap ? trackPoints : []"
          :overlays="showTrailSections ? mapOverlays : []"
          :focus-on="trailFocus"
          :activity-id="activity.id"
          :is-owner="isOwner"
        />
      </div>

      <ActivityGearCard v-if="isOwner" :activity="activity" />

      <div v-if="chartPanels.length > 0" class="grid gap-3 lg:grid-cols-2">
        <template v-for="panel in chartPanels" :key="panel.key">
          <ActivityStreamChart
            v-if="panel.kind === 'chart'"
            :title="t(panel.chart.titleKey)"
            :render="panel.chart.render"
            :stats="panel.chart.stats"
          />
          <Card v-else class="flex min-w-0 flex-col gap-3">
            <div class="flex items-center gap-2">
              <span class="bg-hr size-2.5 rounded-full" aria-hidden="true" />
              <h3 class="text-card-heading">{{ t('activities.hrZones.title') }}</h3>
            </div>
            <div class="flex flex-1 items-center">
              <ActivityHrZones :zones="panel.zones" class="w-full" />
            </div>
          </Card>
        </template>
      </div>

      <Card v-if="showLaps || showWorkout" class="flex flex-col gap-3">
        <!-- Both available → switch between Laps and Sets (mirrors v1's pills). -->
        <Tabs v-if="showLaps && showWorkout" v-model="splitsTab">
          <TabsList>
            <TabsTrigger value="laps">{{ t('activities.laps.title') }}</TabsTrigger>
            <TabsTrigger value="sets">{{ t('activities.workout.title') }}</TabsTrigger>
          </TabsList>
          <TabsContent value="laps">
            <LapDynamicsTable
              v-if="isOwner"
              :laps="laps ?? []"
              :dynamics="lapDynamics ?? []"
              :activity="activity"
              :units="units"
            />
            <ActivityLapsTable v-else :laps="laps ?? []" :activity="activity" :units="units" />
          </TabsContent>
          <TabsContent value="sets">
            <ActivityWorkoutSets
              :workout-steps="workoutSteps ?? []"
              :workout-sets="workoutSets ?? []"
              :exercise-titles="exerciseTitles ?? []"
            />
          </TabsContent>
        </Tabs>

        <!-- Only one available → a plain titled table. -->
        <template v-else-if="showLaps">
          <h2 class="text-card-heading">{{ t('activities.laps.title') }}</h2>
          <LapDynamicsTable
            v-if="isOwner"
            :laps="laps ?? []"
            :dynamics="lapDynamics ?? []"
            :activity="activity"
            :units="units"
          />
          <ActivityLapsTable v-else :laps="laps ?? []" :activity="activity" :units="units" />
        </template>
        <template v-else>
          <h2 class="text-card-heading">{{ t('activities.workout.title') }}</h2>
          <ActivityWorkoutSets
            :workout-steps="workoutSteps ?? []"
            :workout-sets="workoutSets ?? []"
            :exercise-titles="exerciseTitles ?? []"
          />
        </template>
      </Card>

      <!-- The athlete's own effort breakdown, computed on read by the analysis
           module: owners only, because it is measured against their thresholds. -->
      <Card v-if="showIntervals" class="flex flex-col gap-3">
        <h2 class="text-card-heading">{{ t('analysis.intervals.title') }}</h2>
        <ActivityIntervalTable
          :intervals="intervals!"
          :activity-type="activity?.activityType ?? 0"
          :units="units"
        />
      </Card>

      <!-- Which stretches read as technical ground: experimental pass, owners
           only, shown when it finds anything at all. -->
      <TrailSectionsCard
        v-if="showTrailSections"
        :sections="trailSections!"
        :activity-type="activity?.activityType ?? 0"
        :units="units"
        :selected="trailSelected"
        @select="selectTrailSection"
        @mark="markTrailSection"
      />

      <Card v-if="showSegments || isOwner" class="flex flex-col gap-3">
        <div class="flex items-center justify-between gap-2">
          <h2 class="text-card-heading">{{ t('activities.segments.title') }}</h2>
          <Button v-if="isOwner" variant="outline" size="sm" @click="segmentsDebugOpen = true">
            {{ t('activities.segments.debug.button') }}
          </Button>
        </div>
        <ActivitySegmentsTable
          v-if="showSegments"
          :segments="segments ?? []"
          :activity-type="activity?.activityType ?? 0"
          :units="units"
        />
        <p v-else class="text-hint text-muted-foreground">
          {{ t('activities.segments.debug.noneStored') }}
        </p>
        <SegmentMatchDebugDialog
          v-model:open="segmentsDebugOpen"
          :activity-id="activityId ?? 0"
          :units="units"
        />
      </Card>
    </template>
  </div>
</template>
