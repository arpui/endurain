import { describe, expect, it } from 'vitest'

import { setChartProvider, useChartProvider } from '@/composables/useChartProvider'
import {
  buildChartConfiguration,
  createChartJsChartProvider,
  DEFAULT_CHART_PALETTE,
} from '@/providers/chartJsChartProvider'

describe('buildChartConfiguration', () => {
  it('maps a line chart with smoothing and a flat translucent fill', () => {
    const config = buildChartConfiguration({
      kind: 'line',
      labels: ['a', 'b'],
      series: [{ label: 'Pace', data: [1, 2] }],
    })

    expect(config.type).toBe('line')
    expect(config.data.labels).toEqual(['a', 'b'])

    const dataset = config.data.datasets[0] as Record<string, unknown> | undefined
    expect(dataset?.label).toBe('Pace')
    expect(dataset?.data).toEqual([1, 2])
    expect(dataset?.fill).toBe(true)
    // Flat translucent fill (not a gradient), per the design system.
    expect(dataset?.backgroundColor).toBe('rgba(29, 158, 117, 0.15)')
    expect(dataset?.tension).toBe(0.3)
  })

  it('fills the area for area charts while staying a line type', () => {
    const config = buildChartConfiguration({
      kind: 'area',
      series: [{ label: 'Elevation', data: [3] }],
    })

    expect(config.type).toBe('line')
    const dataset = config.data.datasets[0] as Record<string, unknown> | undefined
    expect(dataset?.fill).toBe(true)
  })

  it('maps bar charts to the bar type', () => {
    const config = buildChartConfiguration({
      kind: 'bar',
      series: [{ label: 'Count', data: [5] }],
    })

    expect(config.type).toBe('bar')
  })

  it('renders a dashed series as a dashed line overlay regardless of chart kind', () => {
    const config = buildChartConfiguration({
      kind: 'bar',
      series: [
        { label: 'Count', data: [1, 2] },
        { label: 'Target', data: [3, 3], dashed: true },
      ],
    })

    expect(config.type).toBe('bar')
    const datasets = config.data.datasets as unknown as Record<string, unknown>[]
    // The data series keeps the chart's bar geometry (no explicit per-dataset type).
    expect(datasets[0]?.type).toBeUndefined()
    // The dashed target is overlaid as a line with a dash pattern and no fill.
    expect(datasets[1]?.type).toBe('line')
    expect(datasets[1]?.borderDash).toEqual([6, 6])
    expect(datasets[1]?.fill).toBe(false)
    expect(datasets[1]?.pointRadius).toBe(0)
  })

  it('renders a line series as a solid overlay on a bar chart', () => {
    const config = buildChartConfiguration({
      kind: 'bar',
      series: [
        { label: 'Fitness', data: [40, 44], line: true },
        { label: 'Load', data: [80, 120] },
      ],
    })

    expect(config.type).toBe('bar')
    const datasets = config.data.datasets as unknown as Record<string, unknown>[]
    // The line overlay is a solid line: no dash, no fill, smoothed.
    expect(datasets[0]?.type).toBe('line')
    expect(datasets[0]?.borderDash).toBeUndefined()
    expect(datasets[0]?.fill).toBe(false)
    expect(datasets[0]?.tension).toBe(0.3)
    // The plain series keeps the bar geometry.
    expect(datasets[1]?.type).toBeUndefined()
  })

  it('puts a secondary-axis series on its own right-hand scale', () => {
    const config = buildChartConfiguration({
      kind: 'bar',
      series: [
        { label: 'Fitness', data: [40, 44], line: true },
        { label: 'Load', data: [90, 170], axis: 'secondary' },
      ],
    })

    const scales = config.options?.scales as Record<string, Record<string, unknown>>
    const datasets = config.data.datasets as unknown as Record<string, unknown>[]
    // The curves keep the left axis; the bars get their own, so a 170-point day
    // does not squash a 44-point fitness reading.
    expect(datasets[0]?.yAxisID).toBe('y')
    expect(datasets[1]?.yAxisID).toBe('y1')
    const y1 = scales.y1 as Record<string, unknown>
    expect(y1.position).toBe('right')
    expect(y1.beginAtZero).toBe(true)
    // Two grids on one plot is unreadable: the extra axis draws no grid.
    expect((y1.grid as Record<string, unknown>).drawOnChartArea).toBe(false)
    expect(scales.y?.display).toBe(true)
  })

  it('adds no second axis when every series shares one scale', () => {
    const config = buildChartConfiguration({
      kind: 'line',
      series: [{ label: 'Pace', data: [300, 295] }],
    })

    const scales = config.options?.scales as Record<string, unknown>
    expect(scales.y1).toBeUndefined()
    const datasets = config.data.datasets as unknown as Record<string, unknown>[]
    expect(datasets[0]?.yAxisID).toBe('y')
  })

  it('fills an area series down to zero in a shade of its own colour', () => {
    const config = buildChartConfiguration({
      kind: 'bar',
      series: [
        { label: 'Fatigue', data: [60, 65], line: true, area: true, color: '#e24b4a' },
        { label: 'Form', data: [-10, -20], line: true, color: '#378add' },
      ],
    })

    const datasets = config.data.datasets as unknown as Record<string, unknown>[]
    expect(datasets[0]?.fill).toBe('origin')
    expect(datasets[0]?.backgroundColor).toBe('rgba(226, 75, 74, 0.15)')
    // A line without `area` stays a plain stroke.
    expect(datasets[1]?.fill).toBe(false)
  })

  it('derives the area shade from an rgba colour too', () => {
    const config = buildChartConfiguration({
      kind: 'line',
      series: [{ label: 'Load', data: [1, 2], color: 'rgba(29, 158, 117, 0.4)' }],
    })

    const datasets = config.data.datasets as unknown as Record<string, unknown>[]
    // A line/area chart fills by default, and the fill keeps the colour's own
    // channels at the area alpha whether it arrived as hex or as rgba().
    expect(datasets[0]?.fill).toBe(true)
    expect(datasets[0]?.backgroundColor).toBe('rgba(29, 158, 117, 0.15)')
  })

  it('enables a shared crosshair tooltip only when asked', () => {
    const withShared = buildChartConfiguration({
      kind: 'bar',
      series: [{ label: 'Load', data: [1, 2] }],
      sharedTooltip: true,
    })
    const without = buildChartConfiguration({
      kind: 'bar',
      series: [{ label: 'Load', data: [1, 2] }],
    })

    expect(withShared.options?.interaction).toEqual({ mode: 'index', intersect: false })
    expect(without.options?.interaction).toBeUndefined()
  })

  it('assigns palette colours by series index and honours explicit colours', () => {
    const config = buildChartConfiguration({
      kind: 'line',
      series: [
        { label: 'A', data: [1] },
        { label: 'B', data: [2], color: '#abcdef' },
      ],
    })

    const datasets = config.data.datasets as unknown as Record<string, unknown>[]
    expect(datasets[0]?.borderColor).toBe(DEFAULT_CHART_PALETTE[0])
    expect(datasets[1]?.borderColor).toBe('#abcdef')
  })

  it('defaults labels to an empty array when omitted', () => {
    const config = buildChartConfiguration({ kind: 'line', series: [] })
    expect(config.data.labels).toEqual([])
  })

  it('shows the legend only for multi-series charts', () => {
    const single = buildChartConfiguration({
      kind: 'line',
      series: [{ label: 'A', data: [1] }],
    })
    const multi = buildChartConfiguration({
      kind: 'line',
      series: [
        { label: 'A', data: [1] },
        { label: 'B', data: [2] },
      ],
    })

    expect(single.options?.plugins?.legend?.display).toBe(false)
    expect(multi.options?.plugins?.legend?.display).toBe(true)
  })

  it('applies the value formatter to y-axis ticks and inverts the axis when requested', () => {
    const config = buildChartConfiguration({
      kind: 'line',
      series: [{ label: 'HR', data: [1] }],
      valueFormat: (value) => `${value} bpm`,
      invertY: true,
    })

    const y = config.options?.scales?.y as Record<string, unknown> | undefined
    expect(y?.reverse).toBe(true)
    const ticks = y?.ticks as { callback?: (value: number) => string } | undefined
    expect(ticks?.callback?.(120)).toBe('120 bpm')
  })

  it('appends the x-axis unit to the tooltip title', () => {
    const config = buildChartConfiguration({
      kind: 'line',
      series: [{ label: 'HR', data: [1] }],
      labels: ['0.7'],
      xUnit: 'km',
    })
    const tooltip = config.options?.plugins?.tooltip as
      { callbacks?: { title?: (items: { label: string }[]) => string } } | undefined
    expect(tooltip?.callbacks?.title?.([{ label: '0.7' }])).toBe('0.7 km')
  })

  it('leaves the y-axis upright without explicit options', () => {
    const config = buildChartConfiguration({ kind: 'line', series: [{ label: 'A', data: [1] }] })
    const y = config.options?.scales?.y as Record<string, unknown> | undefined
    expect(y?.reverse).toBe(false)
  })

  it('adds pan/zoom config when zoom is enabled and omits it otherwise', () => {
    const zoomed = buildChartConfiguration({
      kind: 'line',
      series: [{ label: 'HR', data: [1] }],
      zoom: true,
    })
    const zoom = zoomed.options?.plugins?.zoom as { pan?: { enabled?: boolean } } | undefined
    expect(zoom?.pan?.enabled).toBe(true)

    const plain = buildChartConfiguration({ kind: 'line', series: [{ label: 'A', data: [1] }] })
    expect(plain.options?.plugins?.zoom).toBeUndefined()
  })
})

describe('createChartJsChartProvider', () => {
  it('names the provider and registers as available behind the seam', () => {
    const provider = createChartJsChartProvider()
    expect(provider.name).toBe('chartjs')

    setChartProvider(provider)
    const { provider: active, isAvailable } = useChartProvider()
    expect(active.name).toBe('chartjs')
    expect(isAvailable).toBe(true)
  })
})
