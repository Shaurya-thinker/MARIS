import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import React from 'react'
import {
  TIMINGS,
  EASINGS,
  MOTION_PRESETS,
  isReducedMotion,
  animatePageEnter,
  animateStaggerCards,
} from '../lib/motion'
import { Header } from '../components/layout/Header'

describe('MARIS Centralized Motion System', () => {
  it('1. Exports valid timing constants and easings', () => {
    expect(TIMINGS.fast).toBe(0.15)
    expect(TIMINGS.base).toBe(0.22)
    expect(TIMINGS.page).toBe(0.25)
    expect(EASINGS.out).toBe('power2.out')
    expect(MOTION_PRESETS.pageEnter.animate.duration).toBe(0.25)
  })

  it('2. isReducedMotion safely evaluates without crashing', () => {
    const result = isReducedMotion()
    expect(typeof result).toBe('boolean')
  })

  it('3. animatePageEnter gracefully handles null targets', () => {
    const onComplete = vi.fn()
    const tween = animatePageEnter(null, onComplete)
    expect(tween).toBeNull()
  })

  it('4. animatePageEnter triggers on HTMLElement', () => {
    const div = document.createElement('div')
    document.body.appendChild(div)
    const tween = animatePageEnter(div)
    expect(tween).toBeDefined()
    document.body.removeChild(div)
  })

  it('5. animateStaggerCards triggers without crashing', () => {
    const div1 = document.createElement('div')
    const div2 = document.createElement('div')
    document.body.appendChild(div1)
    document.body.appendChild(div2)
    const tween = animateStaggerCards([div1, div2])
    expect(tween).toBeDefined()
    document.body.removeChild(div1)
    document.body.removeChild(div2)
  })

  it('6. Header component renders with the nav-active-indicator', () => {
    const { container } = render(
      <Header
        activeView="workspace"
        onSelectView={vi.fn()}
        onOpenCreateModal={vi.fn()}
      />
    )
    const indicator = container.querySelector('.nav-active-indicator')
    expect(indicator).not.toBeNull()
  })
})
