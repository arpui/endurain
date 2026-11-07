<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Pencil, Plus, Trash2, X } from '@lucide/vue'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import AnalysisSectionsNav from '@/features/analysis/components/AnalysisSectionsNav.vue'
import RoutesMap from '@/features/analysis/components/RoutesMap.vue'
import { connectRoutePoints } from '@/features/analysis/services/analysis'
import {
  useCreateRouteMutation,
  useDeleteRouteMutation,
  useRouteSegmentsQuery,
  useRoutesQuery,
  useSegmentNetworkQuery,
  useUpdateRouteMutation,
} from '@/features/analysis/composables/useAnalysis'
import { gradeTrailWay } from '@/features/analysis/utils/trailDegree'
import { themeColor } from '@/lib/themeColor'
import type { DesignRoute } from '@/features/analysis/types'
import type { GeoPoint } from '@/composables/useMapProvider'

const { t, locale } = useI18n()

const routesQuery = useRoutesQuery()
const routes = computed(() => routesQuery.data.value ?? [])

/** The route the viewer picked, on the map or in the table. */
const selected = ref<number | null>(null)

function pick(routeId: number): void {
  selected.value = selected.value === routeId ? null : routeId
}

// The athlete's named corridors underneath the map, and the corridors a picked
// route runs along: the route tool should speak in their names, not in cells.
const showSegments = ref(false)
const segmentQuery = useSegmentNetworkQuery(showSegments, true)
const networkSegments = computed(() =>
  showSegments.value ? (segmentQuery.data.value?.segments ?? []) : [],
)
const inventoryQuery = useRouteSegmentsQuery(selected)

const numberFormat = computed(
  () => new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }),
)

function km(metres: number): string {
  return `${numberFormat.value.format(metres / 1000)} km`
}

// --- the editor: a draft polyline on the map ---
// null draft means nobody is designing; a draft of points is created by map
// taps and thinned by picking its handles. Saving measures totals server-side.
const draft = ref<GeoPoint[] | null>(null)
/** null draft id: a brand-new route; otherwise the saved route being redrawn. */
const editingId = ref<number | null>(null)
const draftName = ref('')
const draftNotes = ref<string | undefined>(undefined)

const createMutation = useCreateRouteMutation()
const updateMutation = useUpdateRouteMutation()
const saving = computed(() => createMutation.isPending.value || updateMutation.isPending.value)
const canSave = computed(() => draftName.value.trim().length > 0 && (draft.value?.length ?? 0) >= 2)
// Two ways to draw: the honest freehand line, or anchors carried by the
// athlete's own walked ground (the personal graph shapes what is saved).
const snapMode = ref(true)
const connectFailed = ref(false)

function startNew(): void {
  draft.value = []
  editingId.value = null
  draftName.value = ''
  draftNotes.value = undefined
}

function startEdit(route: DesignRoute): void {
  draft.value = [...route.points]
  editingId.value = route.id
  draftName.value = route.name
  draftNotes.value = route.notes ?? undefined
}

function cancelEdit(): void {
  draft.value = null
  editingId.value = null
}

function addPoint(point: GeoPoint): void {
  if (draft.value) {
    draft.value.push(point)
  }
}

function removeVertex(index: number): void {
  draft.value?.splice(index, 1)
}

function undoLast(): void {
  draft.value?.pop()
}

async function saveDraft(): Promise<void> {
  if (!canSave.value || !draft.value) {
    return
  }
  connectFailed.value = false
  let points = [...draft.value]
  if (snapMode.value) {
    try {
      points = (await connectRoutePoints(points)).points
    } catch {
      connectFailed.value = true
      return
    }
  }
  const name = draftName.value.trim()
  const done = { onSuccess: () => cancelEdit() }
  if (editingId.value === null) {
    createMutation.mutate({ name, points, notes: draftNotes.value }, done)
  } else {
    updateMutation.mutate({ routeId: editingId.value, name, points, notes: draftNotes.value }, done)
  }
}

// --- deletion ---
const deleteMutation = useDeleteRouteMutation()
const routeToDelete = ref<DesignRoute | null>(null)
const isDeleteOpen = ref(false)

function askDelete(route: DesignRoute): void {
  routeToDelete.value = route
  isDeleteOpen.value = true
}

function confirmDelete(): void {
  if (!routeToDelete.value) {
    return
  }
  deleteMutation.mutate(routeToDelete.value.id, {
    onSuccess: () => {
      isDeleteOpen.value = false
      if (selected.value === routeToDelete.value?.id) {
        selected.value = null
      }
    },
  })
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-col gap-1">
      <h1 class="text-page-title">{{ t('analysis.routes.title') }}</h1>
      <p class="text-body">{{ t('analysis.routes.subtitle') }}</p>
    </header>

    <AnalysisSectionsNav />

    <ErrorState v-if="routesQuery.isError.value" :title="t('analysis.loadFailed')" />

    <Skeleton v-else-if="routesQuery.isPending.value" class="h-64 w-full" />

    <template v-else>
      <Card v-if="routes.length === 0 && draft === null" class="p-3">
        <EmptyState
          :title="t('analysis.routes.empty.title')"
          :description="t('analysis.routes.empty.description')"
        >
          <template #action>
            <Button size="sm" @click="startNew">
              <Plus class="me-1 size-4" />
              {{ t('analysis.routes.new') }}
            </Button>
          </template>
        </EmptyState>
      </Card>

      <Card v-else class="flex flex-col gap-2 p-3">
        <div class="flex flex-wrap items-center gap-2">
          <template v-if="draft !== null">
            <Input
              v-model="draftName"
              :placeholder="t('analysis.routes.namePlaceholder')"
              class="w-48"
            />
            <label class="flex items-center gap-2 text-caption text-muted-foreground">
              <Switch v-model="snapMode" />
              {{ t('analysis.routes.snap') }}
            </label>
            <span class="text-caption text-muted-foreground">
              {{ t('analysis.routes.points', { n: draft.length }) }}
            </span>
            <Button size="sm" variant="outline" :disabled="draft.length === 0" @click="undoLast">
              {{ t('analysis.routes.undo') }}
            </Button>
            <Button size="sm" :disabled="!canSave || saving" @click="saveDraft">
              {{ t('analysis.routes.save') }}
            </Button>
            <Button size="sm" variant="ghost" @click="cancelEdit">
              <X class="me-1 size-4" />
              {{ t('analysis.routes.cancelEdit') }}
            </Button>
          </template>
          <Button v-else size="sm" variant="outline" class="ms-auto" @click="startNew">
            <Plus class="me-1 size-4" />
            {{ t('analysis.routes.new') }}
          </Button>
        </div>
        <label class="flex items-center gap-2 text-caption text-muted-foreground">
          <Switch v-model="showSegments" />
          {{ t('analysis.routes.showSegments') }}
        </label>
        <RoutesMap
          :routes="routes"
          :selected="selected"
          :label="t('analysis.routes.mapLabel')"
          :draft="draft"
          :editing-id="editingId"
          :segments="networkSegments"
          @select="pick"
          @add-point="addPoint"
          @remove-vertex="removeVertex"
        />
        <p v-if="draft !== null" class="text-caption text-muted-foreground">
          {{ t('analysis.routes.drawHint') }}
          <span v-if="snapMode"> {{ t('analysis.routes.snapHint') }}</span>
          <span v-if="connectFailed" class="text-hr">
            {{ t('analysis.routes.connectFailed') }}</span
          >
        </p>
      </Card>

      <Card v-if="selected !== null && (inventoryQuery.data.value?.length ?? 0) > 0" class="p-3">
        <h3 class="mb-2 text-card-heading">{{ t('analysis.routes.segmentsTitle') }}</h3>
        <ul class="flex flex-col gap-1 text-meta">
          <li
            v-for="row in inventoryQuery.data.value"
            :key="row.segmentId"
            class="flex items-center gap-2"
          >
            <span
              class="inline-block size-2.5 rounded-full"
              :style="{ backgroundColor: themeColor(gradeTrailWay(row.claim).colorToken) }"
            />
            <span class="font-medium">{{ row.name }}</span>
            <span class="text-muted-foreground">
              {{ Math.round(row.coveredM) }} / {{ Math.round(row.lengthM) }} m ·
              {{
                row.direction === 'forward'
                  ? t('analysis.routes.dirForward')
                  : t('analysis.routes.dirReverse')
              }}
            </span>
          </li>
        </ul>
      </Card>

      <Card v-if="routes.length > 0" class="overflow-x-auto p-3">
        <table class="w-full text-body">
          <thead>
            <tr class="border-b text-left text-caption text-muted-foreground">
              <th class="py-2 font-medium">{{ t('analysis.routes.colName') }}</th>
              <th class="py-2 pe-3 text-right font-medium">
                {{ t('analysis.routes.colDistance') }}
              </th>
              <th class="py-2 pe-3 text-right font-medium">{{ t('analysis.routes.colGain') }}</th>
              <th class="py-2 pe-3 text-right font-medium">
                {{ t('analysis.routes.colCreated') }}
              </th>
              <th class="py-2 text-right font-medium">{{ t('analysis.routes.colActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="route in routes"
              :key="route.id"
              class="cursor-pointer border-b last:border-0 hover:bg-muted/50"
              :class="{ 'bg-muted/60': selected === route.id }"
              @click="pick(route.id)"
            >
              <td class="py-2">
                {{ route.name }}
                <span v-if="route.notes" class="block text-caption text-muted-foreground">
                  {{ route.notes }}
                </span>
              </td>
              <td class="py-2 pe-3 text-right tabular-nums">{{ km(route.distanceM) }}</td>
              <td class="py-2 pe-3 text-right tabular-nums">{{ Math.round(route.gainM) }} m</td>
              <td class="py-2 pe-3 text-right text-muted-foreground">
                {{ new Date(route.createdAt).toLocaleDateString(locale) }}
              </td>
              <td class="py-2 text-right">
                <div class="flex items-center justify-end gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    :aria-label="t('analysis.routes.edit')"
                    @click.stop="startEdit(route)"
                  >
                    <Pencil class="size-4" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    :aria-label="t('analysis.routes.delete')"
                    @click.stop="askDelete(route)"
                  >
                    <Trash2 class="size-4" />
                  </Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </template>

    <ConfirmDialog
      v-model:open="isDeleteOpen"
      :title="t('analysis.routes.deleteTitle')"
      :description="t('analysis.routes.deleteBody', { name: routeToDelete?.name ?? '' })"
      :confirm-label="t('analysis.routes.delete')"
      :cancel-label="t('analysis.routes.cancel')"
      :close-label="t('analysis.routes.close')"
      :pending="deleteMutation.isPending.value"
      @confirm="confirmDelete"
    />
  </div>
</template>
