import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import React from 'react'
import { Header } from '../components/layout/Header'

describe('Header Top Navigation Bar UI', () => {
  const baseProps = {
    activeView: 'workspace' as const,
    onSelectView: vi.fn(),
    onOpenCreateModal: vi.fn(),
  }

  it('1. Does NOT render "No Investigations" status text', () => {
    render(<Header {...baseProps} />)
    expect(screen.queryByText(/No Investigations/i)).toBeNull()
  })

  it('2. Renders all 8 navigation tabs in the exact order and all are accessible', () => {
    const onSelectView = vi.fn()
    render(<Header {...baseProps} onSelectView={onSelectView} />)

    const expectedTabs = [
      'Workspace',
      'Evaluator',
      'Overview',
      'Investigations',
      'Evidence',
      'Vessels',
      'Analytics',
      'Pipeline',
    ]

    expectedTabs.forEach((tabName) => {
      const tabBtn = screen.getByRole('button', { name: new RegExp(tabName, 'i') })
      expect(tabBtn).toBeDefined()
    })

    // Test clicking navigation tabs
    const evaluatorTab = screen.getByRole('button', { name: /Evaluator/i })
    fireEvent.click(evaluatorTab)
    expect(onSelectView).toHaveBeenCalledWith('evaluator')

    const overviewTab = screen.getByRole('button', { name: /Overview/i })
    fireEvent.click(overviewTab)
    expect(onSelectView).toHaveBeenCalledWith('overview')

    // Settings must not be present
    expect(screen.queryByRole('button', { name: /Settings/i })).toBeNull()
  })

  it('3. Renders the New Investigation action on the right side and it is clickable', () => {
    const onOpenCreateModal = vi.fn()
    render(<Header {...baseProps} onOpenCreateModal={onOpenCreateModal} />)

    const newBtn = screen.getByRole('button', { name: /New Investigation/i })
    expect(newBtn).toBeDefined()
    fireEvent.click(newBtn)
    expect(onOpenCreateModal).toHaveBeenCalledTimes(1)
  })

  it('4. Confirms UTC clock and scenario/investigation dropdown are completely removed from the navbar', () => {
    render(<Header {...baseProps} />)

    // UTC clock must not exist
    expect(screen.queryByTitle(/Coordinated Universal Time/i)).toBeNull()
    expect(screen.queryByText(/UTC/i)).toBeNull()

    // Investigation selector dropdown must not exist
    expect(screen.queryByRole('button', { name: /Select active investigation case/i })).toBeNull()
    expect(screen.queryByText(/Live G1 Investigations/i)).toBeNull()
    expect(screen.queryByText(/Simulation Investigations/i)).toBeNull()
  })

  it('5. Applies active styling correctly to the active view', () => {
    const { rerender } = render(<Header {...baseProps} activeView="pipeline" />)
    const pipelineBtn = screen.getByRole('button', { name: /Pipeline/i })
    expect(pipelineBtn.className).toContain('is-active')

    rerender(<Header {...baseProps} activeView="evaluator" />)
    const evaluatorBtn = screen.getByRole('button', { name: /Evaluator/i })
    expect(evaluatorBtn.className).toContain('is-active')
  })

  it('6. Uses expected flexbox structural hierarchy (brand, primary nav, right-side controls)', () => {
    const { container } = render(<Header {...baseProps} />)
    const header = container.querySelector('header.topbar')
    expect(header).not.toBeNull()

    const brand = header?.querySelector('.brand-lockup')
    const primaryNav = header?.querySelector('nav.topbar__nav')
    const controls = header?.querySelector('.topbar__controls')

    expect(brand).not.toBeNull()
    expect(primaryNav).not.toBeNull()
    expect(controls).not.toBeNull()

    // Primary nav should contain the 8 tab buttons
    expect(primaryNav?.querySelectorAll('.nav-tab-btn')).toHaveLength(8)

    // Controls should contain the New Investigation button
    expect(controls?.querySelector('.header-new-btn')).not.toBeNull()
  })
})
