<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import { Button } from '@/components/ui/button'

const route = useRoute()
const { t } = useI18n()

/** The analysis sections, in menu order. */
const SECTIONS = [
  { name: 'analysis', key: 'analysis.sectionLoad' },
  { name: 'analysis-power', key: 'analysis.sectionPower' },
  { name: 'analysis-best-times', key: 'analysis.sectionBest' },
] as const
</script>

<template>
  <nav class="flex gap-1" :aria-label="t('analysis.sections')">
    <RouterLink
      v-for="section in SECTIONS"
      v-slot="{ href, navigate }"
      :key="section.name"
      :to="{ name: section.name }"
      custom
    >
      <Button
        as="a"
        :href="href"
        :variant="route.name === section.name ? 'default' : 'ghost'"
        @click="navigate"
      >
        {{ t(section.key) }}
      </Button>
    </RouterLink>
  </nav>
</template>
