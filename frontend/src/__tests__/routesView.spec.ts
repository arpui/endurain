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
  default: {
    name: 'RoutesMap',
    props: {
      routes: { type: Array, default: () => [] },
      selected: { type: Number, default: null },
      label: { type: String, default: '' },
      draft: { type: Array, default: null },
      editingId: { type: Number, default: null },
      segments: { type: Array, default: undefined },
      highlight: { type: Array, default: null },
      endpoints: { type: Array, default: null },
      water: { type: Array, default: undefined },
      picking: { type: Boolean, default: false },
      center: { type: Object, default: null },
      preview: { type: Object, default: null },
    },
    template: '<div class="map-stub" />',
  },
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
  let router: ReturnType<typeof createRouter>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        'analysis',
        'analysis-power',
        'analysis-best-times',
        'analysis-trail-network',
        'analysis-routes',
      ].map((name) => ({ path: `/${name}`, name, component: { template: '<div />' } })),
    })
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

  it('replaces the draft array on every edit so the map redraws', async () => {
    const wrapper = mount(RoutesView, {
      global: {
        plugins: [pinia, router, i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]],
        stubs: { teleport: true },
      },
    })
    await flushPromises()
    // open the designer
    wrapper
      .findAll('button')
      .find((b) => b.text().includes('New route'))
      ?.trigger('click')
    await flushPromises()
    const map = wrapper.findComponent({ name: 'RoutesMap' })
    const before = map.props('draft') as unknown[]
    map.vm.$emit('addPoint', { lat: 42.31, lng: 1.51 })
    await flushPromises()
    const after = map.props('draft') as unknown[]
    expect(after).toHaveLength(before.length + 1)
    // the identity must change: the map watches it, a mutation in place draws nothing
    expect(after).not.toBe(before)
    expect(wrapper.text()).toContain('1 point')

    map.vm.$emit('moveVertex', 0, { lat: 42.99, lng: 1.99 })
    await flushPromises()
    expect((map.props('draft') as Array<{ lat: number }>)[0]?.lat).toBe(42.99)
    expect(map.props('draft')).not.toBe(after)

    map.vm.$emit('removeVertex', 0)
    await flushPromises()
    expect(map.props('draft')).toHaveLength(0)
  })

  it('previews the shaped line after a pause, and drops it on the next edit', async () => {
    // teach the connect answer alongside the list
    const answers = routeAnswers()
    vi.mocked(apiFetch).mockImplementation((url: unknown) => {
      const key = String(url)
      if (key === '/analysis/routes/connect') {
        return Promise.resolve({
          geometry: [
            [42.4, 1.4],
            [42.5, 1.5],
            [42.6, 1.6],
          ],
          carried: ['walked', 'mapped'],
          spans: [1, 2],
          walked: [true, false],
        })
      }
      if (key in answers) return Promise.resolve(answers[key])
      return Promise.reject(new Error(`unexpected request: ${key}`))
    })
    const wrapper = mount(RoutesView, {
      global: {
        plugins: [pinia, router, i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]],
        stubs: { teleport: true },
      },
    })
    await flushPromises()
    wrapper
      .findAll('button')
      .find((b) => b.text().includes('New route'))
      ?.trigger('click')
    await flushPromises()
    const map = wrapper.findComponent({ name: 'RoutesMap' })
    vi.useFakeTimers()
    try {
      map.vm.$emit('addPoint', { lat: 42.31, lng: 1.51 })
      map.vm.$emit('addPoint', { lat: 42.32, lng: 1.52 })
      await flushPromises()
      expect(map.props('preview')).toBeNull() // not asked yet: the drawing has not paused
      await vi.advanceTimersByTimeAsync(400)
      await flushPromises()
      const shaped = map.props('preview') as {
        carried: string[]
        spans: number[]
        points: unknown[]
      }
      expect(shaped.carried).toEqual(['walked', 'mapped'])
      expect(shaped.spans).toEqual([1, 2])
      expect(shaped.points).toHaveLength(3)
      // a new edit retires the stale answer, before the next question lands
      map.vm.$emit('addPoint', { lat: 42.33, lng: 1.53 })
      await flushPromises()
      expect(map.props('preview')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
