import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

import { apiFetch } from '@/services/http'
import i18n from '@/i18n'
import { useAuthStore } from '@/features/auth/stores/auth'
import RoutesView from '@/features/analysis/views/RoutesView.vue'

vi.mock('@/services/http', () => {
  class HttpError extends Error {
    constructor(
      public status: number,
      message?: string,
    ) {
      super(message)
    }
  }
  return { apiFetch: vi.fn<typeof apiFetch>(), HttpError }
})

// the map is a provider seam over real Leaflet: irrelevant for the view's own
// rendering and unavailable under jsdom
vi.mock('@/features/analysis/components/RoutesMap.vue', () => ({
  default: { name: 'RoutesMap', template: '<div class="map-stub" />' },
}))

/**
 * Routes the view fires at on mount: the list, plus the segment corridors
 * underneath the map (kept off by default: showSegments starts false, so only
 * the list is answered here).
 */
function routeAnswers(): Record<string, unknown> {
  return {
    '/analysis/routes': [
      {
        id: 6,
        name: 'Cursa smoke',
        distance_m: 13345.5,
        gain_m: 900.0,
        notes: null,
        created_at: '2026-11-07T19:00:00Z',
        updated_at: '2026-11-07T19:00:00Z',
      },
    ],
  }
}

describe('routes view', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    useAuthStore().isAuthenticated = true
    vi.mocked(apiFetch).mockReset()
    vi.mocked(apiFetch).mockImplementation((url: unknown) => {
      const answers = routeAnswers()
      const key = String(url)
      if (key in answers) return Promise.resolve(answers[key])
      return Promise.reject(new Error(`unexpected request: ${key}`))
    })
  })

  it('renders the saved routes without crashing', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        'analysis',
        'analysis-power',
        'analysis-best-times',
        'analysis-trail-network',
        'analysis-routes',
      ].map((name) => ({ path: `/${name}`, name, component: { template: '<div />' } })),
    })
    const wrapper = mount(RoutesView, {
      global: {
        plugins: [
          pinia,
          router,
          i18n,
          [
            VueQueryPlugin,
            { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
          ],
        ],
        stubs: { teleport: true },
      },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Cursa smoke')
  })
})
