import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import React from 'react'
import { InvestigationsView } from '../components/views/InvestigationsView'
import { simulationScenarios } from '../simulation/simulationEngine'
import type { InvestigationListItem } from '../types/investigationApi'

globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.mock('../api/investigationApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/investigationApi')>()
  return {
    ...actual,
    listInvestigations: vi.fn().mockResolvedValue([]),
    fetchExperimentConfig: vi.fn().mockResolvedValue({
      sentinel1_configured: false,
      era5_configured: false,
      cmems_configured: false,
      ais_configured: false,
      warnings: ['CDSE credentials not configured'],
    }),
  }
})

vi.mock('maplibre-gl', () => {
  const mapInstance = {
    addControl: vi.fn(),
    on: vi.fn(),
    remove: vi.fn(),
    resize: vi.fn(),
    fitBounds: vi.fn(),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),
    getLayer: vi.fn(() => true),
    getSource: vi.fn(() => ({ setData: vi.fn() })),
    addSource: vi.fn(),
    addLayer: vi.fn(),
    getCanvas: vi.fn(() => ({ style: { cursor: '' } })),
  }
  function MapConstructor() {
    return mapInstance
  }
  return {
    default: { Map: MapConstructor, NavigationControl: vi.fn() },
    Map: MapConstructor,
    NavigationControl: vi.fn(),
  }
})

const mockLiveInvestigations: InvestigationListItem[] = [
  {
    id: 'live-case-001',
    name: 'Mumbai Anchorage Anomaly',
    status: 'COMPLETED',
    created_at: '2026-03-15T12:00:00Z',
    description: 'Active coastal slick monitoring near Mumbai port.',
    asset_count: 5,
  },
]

describe('InvestigationsView Component', () => {
  it('renders without runtime exceptions and displays case catalog', () => {
    const onSelect = vi.fn()
    const onNavigate = vi.fn()
    const onOpenModal = vi.fn()

    render(
      <InvestigationsView
        investigations={mockLiveInvestigations}
        activeId="corsica-2018-demo"
        onSelectInvestigation={onSelect}
        onNavigateToView={onNavigate}
        onOpenCreateModal={onOpenModal}
        simulationScenarios={simulationScenarios}
      />
    )

    // Heading and description
    expect(screen.getByText(/Case Catalog/i)).toBeTruthy()
    expect(screen.getByText(/Access active investigations/i)).toBeTruthy()

    // Historical Corsica
    expect(screen.getByText(/Corsica 2018 Reconstruction/i)).toBeTruthy()

    // All 5 Simulation scenarios
    expect(screen.getByText(/Arabian Sea — Scenario Alpha/i)).toBeTruthy()
    expect(screen.getByText(/Arabian Sea — Scenario Beta/i)).toBeTruthy()
    expect(screen.getByText(/Bay of Bengal — Scenario Gamma/i)).toBeTruthy()
    expect(screen.getByText(/Gulf of Kutch — Scenario Delta/i)).toBeTruthy()
    expect(screen.getByText(/Mediterranean — Scenario Epsilon/i)).toBeTruthy()

    // Live investigation
    expect(screen.getByText(/Mumbai Anchorage Anomaly/i)).toBeTruthy()
  })

  it('filters by category pills: Live, Simulation, Historical', () => {
    const onSelect = vi.fn()
    const onNavigate = vi.fn()

    render(
      <InvestigationsView
        investigations={mockLiveInvestigations}
        activeId="corsica-2018-demo"
        onSelectInvestigation={onSelect}
        onNavigateToView={onNavigate}
        onOpenCreateModal={vi.fn()}
        simulationScenarios={simulationScenarios}
      />
    )

    // Filter to Simulation only
    const simButton = screen.getByRole('button', { name: /Simulation/i })
    fireEvent.click(simButton)

    expect(screen.getByText(/Arabian Sea — Scenario Alpha/i)).toBeTruthy()
    expect(screen.queryByText(/Corsica 2018 Reconstruction/i)).toBeNull()
    expect(screen.queryByText(/Mumbai Anchorage Anomaly/i)).toBeNull()

    // Filter to Historical only
    const histButton = screen.getByRole('button', { name: /Historical/i })
    fireEvent.click(histButton)

    expect(screen.getByText(/Corsica 2018 Reconstruction/i)).toBeTruthy()
    expect(screen.queryByText(/Arabian Sea — Scenario Alpha/i)).toBeNull()

    // Filter to Live only
    const liveButton = screen.getByRole('button', { name: /Live G1/i })
    fireEvent.click(liveButton)

    expect(screen.getByText(/Mumbai Anchorage Anomaly/i)).toBeTruthy()
    expect(screen.queryByText(/Corsica 2018 Reconstruction/i)).toBeNull()
  })

  it('filters by search input query', () => {
    render(
      <InvestigationsView
        investigations={mockLiveInvestigations}
        activeId="corsica-2018-demo"
        onSelectInvestigation={vi.fn()}
        onNavigateToView={vi.fn()}
        onOpenCreateModal={vi.fn()}
        simulationScenarios={simulationScenarios}
      />
    )

    const searchInput = screen.getByPlaceholderText(/Search by case name or region/i)
    fireEvent.change(searchInput, { target: { value: 'Kutch' } })

    expect(screen.getByText(/Gulf of Kutch — Scenario Delta/i)).toBeTruthy()
    expect(screen.queryByText(/Arabian Sea — Scenario Alpha/i)).toBeNull()
    expect(screen.queryByText(/Corsica 2018 Reconstruction/i)).toBeNull()
  })

  it('navigates to workspace when Open Workspace button is clicked', () => {
    const onSelect = vi.fn()
    const onNavigate = vi.fn()

    render(
      <InvestigationsView
        investigations={mockLiveInvestigations}
        activeId=""
        onSelectInvestigation={onSelect}
        onNavigateToView={onNavigate}
        onOpenCreateModal={vi.fn()}
        simulationScenarios={simulationScenarios}
      />
    )

    const openButtons = screen.getAllByRole('button', { name: /Open Workspace/i })
    expect(openButtons.length).toBeGreaterThan(0)

    // Click the first one (Corsica)
    fireEvent.click(openButtons[0])
    expect(onSelect).toHaveBeenCalledWith('corsica-2018-demo')
    expect(onNavigate).toHaveBeenCalledWith('workspace')
  })

  it('expands technical metadata disclosure on click', () => {
    render(
      <InvestigationsView
        investigations={mockLiveInvestigations}
        activeId="corsica-2018-demo"
        onSelectInvestigation={vi.fn()}
        onNavigateToView={vi.fn()}
        onOpenCreateModal={vi.fn()}
        simulationScenarios={simulationScenarios}
      />
    )

    const detailButtons = screen.getAllByText(/View technical details/i)
    fireEvent.click(detailButtons[0])

    expect(screen.getByText(/Hide technical details/i)).toBeTruthy()
    expect(screen.getByText(/Case ID/i)).toBeTruthy()
  })

  it('renders correctly via App header navigation tab click', async () => {
    const { App } = await import('../App')
    render(<App />)

    // Initially in workspace
    expect(screen.getByRole('button', { name: /Workspace/i })).toBeTruthy()

    // Click "Investigations" tab in header
    const investigationsTab = screen.getByRole('button', { name: /Investigations/i })
    fireEvent.click(investigationsTab)

    // Verify InvestigationsView renders Case Catalog
    expect(screen.getByText(/Case Catalog/i)).toBeTruthy()
    expect(screen.getAllByText(/Corsica 2018 Reconstruction/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Arabian Sea — Scenario Alpha/i).length).toBeGreaterThan(0)

    // Click Open Workspace on Scenario Alpha card
    const alphaHeading = screen.getByRole('heading', { name: /Arabian Sea — Scenario Alpha/i })
    const alphaCard = alphaHeading.closest('article')!
    expect(alphaCard).toBeTruthy()
    const openBtn = alphaCard.querySelector('.catalog-card-footer button') as HTMLButtonElement
    fireEvent.click(openBtn)

    // Workspace should now be active
    expect(screen.getByLabelText(/Investigation workspace/i)).toBeTruthy()
  })

  it('switches to interactive real-data experiment wizard on tab click', () => {
    render(
      <InvestigationsView
        investigations={mockLiveInvestigations}
        activeId="corsica-2018-demo"
        onSelectInvestigation={vi.fn()}
        onNavigateToView={vi.fn()}
        onOpenCreateModal={vi.fn()}
        simulationScenarios={simulationScenarios}
      />
    )

    // Click "Interactive Real-Data Experiment" tab
    const experimentTab = screen.getByRole('button', { name: /Interactive Real-Data Experiment/i })
    fireEvent.click(experimentTab)

    // Wizard header and Step 1 components should now be visible
    expect(screen.getByText(/REAL DATA/i)).toBeTruthy()
    expect(screen.getByText(/Interactive Attribution Experiment/i)).toBeTruthy()
    expect(screen.getByText(/Select Sentinel-1 Observation/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Search CDSE Catalogue/i })).toBeTruthy()

    // Can switch back to Case Catalog
    const catalogTab = screen.getByRole('button', { name: /Catalog & Benchmarks/i })
    fireEvent.click(catalogTab)
    expect(screen.getByText(/Corsica 2018 Reconstruction/i)).toBeTruthy()
  })
})

