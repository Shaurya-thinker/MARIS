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

  it('2. Renders rationalized primary navigation tabs and all advanced views remain accessible via dropdown', () => {
    const onSelectView = vi.fn()
    render(<Header {...baseProps} onSelectView={onSelectView} />)

    const expectedPrimaryTabs = ['Workspace', 'Evaluator', 'Investigations', 'Advanced']

    expectedPrimaryTabs.forEach((tabName) => {
      const tabBtn = screen.getByRole('button', { name: new RegExp(tabName, 'i') })
      expect(tabBtn).toBeDefined()
    })

    // Test clicking primary navigation tabs
    const evaluatorTab = screen.getByRole('button', { name: /Evaluator/i })
    fireEvent.click(evaluatorTab)
    expect(onSelectView).toHaveBeenCalledWith('evaluator')

    const investigationsTab = screen.getByRole('button', { name: /Investigations/i })
    fireEvent.click(investigationsTab)
    expect(onSelectView).toHaveBeenCalledWith('investigations')

    // Open Advanced dropdown menu
    const advancedTab = screen.getByRole('button', { name: /Advanced/i })
    fireEvent.click(advancedTab)

    // All 4 advanced views must be accessible
    const evidenceItem = screen.getByRole('menuitem', { name: /Evidence & Artifacts/i })
    const vesselsItem = screen.getByRole('menuitem', { name: /AIS Fleet Registry|Candidate Vessels/i })
    const analyticsItem = screen.getByRole('menuitem', { name: /Attribution Analytics/i })
    const pipelineItem = screen.getByRole('menuitem', { name: /Pipeline Architecture/i })

    expect(evidenceItem).toBeDefined()
    expect(vesselsItem).toBeDefined()
    expect(analyticsItem).toBeDefined()
    expect(pipelineItem).toBeDefined()

    // Test clicking advanced items
    fireEvent.click(evidenceItem)
    expect(onSelectView).toHaveBeenCalledWith('evidence')

    // Re-open and click pipeline
    fireEvent.click(advancedTab)
    const pipelineItem2 = screen.getByRole('menuitem', { name: /Pipeline Architecture/i })
    fireEvent.click(pipelineItem2)
    expect(onSelectView).toHaveBeenCalledWith('pipeline')

    // Settings must not be present
    expect(screen.queryByRole('button', { name: /Settings/i })).toBeNull()
  })

  it('3. Confirms New Investigation button is removed from the navbar', () => {
    render(<Header {...baseProps} />)
    expect(screen.queryByRole('button', { name: /New Investigation/i })).toBeNull()
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

  it('5. Applies active styling correctly to primary and advanced views', () => {
    const { rerender } = render(<Header {...baseProps} activeView="pipeline" />)
    const advancedBtn = screen.getByRole('button', { name: /Advanced/i })
    expect(advancedBtn.className).toContain('is-active')

    rerender(<Header {...baseProps} activeView="evidence" />)
    expect(screen.getByRole('button', { name: /Advanced/i }).className).toContain('is-active')

    rerender(<Header {...baseProps} activeView="evaluator" />)
    const evaluatorBtn = screen.getByRole('button', { name: /Evaluator/i })
    expect(evaluatorBtn.className).toContain('is-active')

    rerender(<Header {...baseProps} activeView="overview" />)
    const investigationsBtn = screen.getByRole('button', { name: /Investigations/i })
    expect(investigationsBtn.className).toContain('is-active')
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

    // Primary nav should contain the 4 primary tab buttons
    expect(primaryNav?.querySelectorAll('.nav-tab-btn')).toHaveLength(4)

    // Controls should not contain the New Investigation button
    expect(controls?.querySelector('.header-new-btn')).toBeNull()
  })
})
