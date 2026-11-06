<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Trash2 } from '@lucide/vue'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Skeleton } from '@/components/ui/skeleton'
import AnalysisSectionsNav from '@/features/analysis/components/AnalysisSectionsNav.vue'
import RoutesMap from '@/features/analysis/components/RoutesMap.vue'
import { useDeleteRouteMutation, useRoutesQuery } from '@/features/analysis/composables/useAnalysis'
import type { DesignRoute } from '@/features/analysis/types'

const { t, locale } = useI18n()

const routesQuery = useRoutesQuery()
const routes = computed(() => routesQuery.data.value ?? [])

/** The route the viewer picked, on the map or in the table. */
const selected = ref<number | null>(null)

function pick(routeId: number): void {
  selected.value = selected.value === routeId ? null : routeId
}

const numberFormat = computed(
  () => new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }),
)

function km(metres: number): string {
  return `${numberFormat.value.format(metres / 1000)} km`
}

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
      <Card v-if="routes.length === 0" class="p-3">
        <EmptyState
          :title="t('analysis.routes.empty.title')"
          :description="t('analysis.routes.empty.description')"
        />
      </Card>
      <Card v-else class="p-3">
        <RoutesMap
          :routes="routes"
          :selected="selected"
          :label="t('analysis.routes.mapLabel')"
          @select="pick"
        />
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
                <Button
                  size="sm"
                  variant="ghost"
                  :aria-label="t('analysis.routes.delete')"
                  @click.stop="askDelete(route)"
                >
                  <Trash2 class="size-4" />
                </Button>
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
