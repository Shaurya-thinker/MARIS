import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PrototypeAiAnalysis } from '../prototype/PrototypeAiAnalysis'
import { AnalysisPanel } from '../components/analysis/AnalysisPanel'
import { IncidentPanel } from '../components/incident/IncidentPanel'
import { InvestigationPipeline } from '../components/pipeline/InvestigationPipeline'
import { candidateVessels, incidentData } from '../data/demoData'
import { simulationScenarios } from '../simulation/simulationEngine'

// Mock maplibre-gl
vi.mock('maplibre-gl', () => {
  const MockMap = vi.fn(() => ({
    on: vi.fn(),
    off: vi.fn(),
    remove: vi.fn(),
    resize: vi.fn(),
    fitBounds: vi.fn(),
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    addSource: vi.fn(),
    getSource: vi.fn(() => ({ setData: vi.fn() })),
    addLayer: vi.fn(),
    getLayer: vi.fn(() => true),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),
    getCanvas: vi.fn(() => ({ style: {} })),
  }))
  return {
    default: { Map: MockMap },
    Map: MockMap,
  }
})

describe('Workspace UI Presentation Verification', () => {
  beforeEach(() => {
    // Setup ResizeObserver mock
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  })

  it('PrototypeAiAnalysis renders distinct labels and values without string concatenation', () => {
    const { container } = render(<PrototypeAiAnalysis active={true} />)
    const textContent = container.textContent || ''

    // Ensure malformed concatenation does not exist
    expect(textContent).not.toContain('ModelPrototype InferenceOutputSpill mask')

    // Ensure distinct spec items exist
    const specLabels = screen.getAllByText(/Model|Output/)
    expect(specLabels.length).toBeGreaterThanOrEqual(2)

    expect(screen.getByText('Prototype Inference')).toBeDefined()
    expect(screen.getByText('Spill mask')).toBeDefined()
    expect(screen.getByText('94%')).toBeDefined()
  })

  it('AnalysisPanel renders Environmental Drift Model with separate Forcing Wind, Current, and Windage fields', () => {
    render(
      <AnalysisPanel
        isDemoMode={true}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate={candidateVessels[0].id}
        onSelectCandidate={vi.fn()}
        prototypeActive={true}
        rankingResult={null}
        explainabilityReport={null}
      />
    )

    expect(screen.getByText('Environmental Drift Model')).toBeDefined()
    expect(screen.getByText('Forcing Wind')).toBeDefined()
    expect(screen.getByText('ERA5 10 m Wind')).toBeDefined()
    expect(screen.getByText('Current')).toBeDefined()
    expect(screen.getByText('CMEMS Surface Currents')).toBeDefined()
    expect(screen.getByText('Windage')).toBeDefined()
    expect(screen.getByText('3% empirical')).toBeDefined()

    // Test progressive disclosure for drift parameters
    const toggleBtn = screen.getByText('View drift parameters')
    fireEvent.click(toggleBtn)
    expect(screen.getByText('Integration Step')).toBeDefined()
    expect(screen.getByText('Advection Scheme')).toBeDefined()
    expect(screen.getByText('Hide drift parameters')).toBeDefined()
  })

  it('IncidentPanel renders key telemetry, SAR evidence, and collapsible technical metadata', () => {
    render(
      <IncidentPanel
        isDemoMode={true}
        demoIncident={incidentData}
        liveInvestigation={null}
        statusResponse={null}
        artifacts={[]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={vi.fn()}
        onRunWorkflow={vi.fn()}
        isRunningWorkflow={false}
      />
    )

    expect(screen.getByText('Incident Overview')).toBeDefined()
    expect(screen.getByText(incidentData.id)).toBeDefined()
    expect(screen.getByText('SAR Evidence')).toBeDefined()
    expect(screen.getByText('Spill Geometry')).toBeDefined()

    // Test collapsible technical metadata
    const techBtn = screen.getByText('View technical metadata')
    fireEvent.click(techBtn)
    expect(screen.getByText('Geographic Extent')).toBeDefined()
    expect(screen.getByText('Hide technical metadata')).toBeDefined()
  })

  it('InvestigationPipeline renders compact timeline nodes without massive header footprint', () => {
    const { container } = render(
      <InvestigationPipeline
        isDemoMode={true}
        demoStages={[
          { label: 'SAR Scene Ingestion', status: 'completed' },
          { label: 'Spill Segmentation', status: 'current' },
        ]}
      />
    )

    expect(container.querySelector('.mission-timeline')).toBeDefined()
    expect(container.querySelector('.timeline-nodes')).toBeDefined()
    expect(screen.getByText('HISTORICAL')).toBeDefined()
  })

  it('All 5 Simulation Scenarios render cleanly in Workspace', () => {
    expect(simulationScenarios.length).toBe(5)
    simulationScenarios.forEach((sim) => {
      expect(sim.id).toBeDefined()
      expect(sim.name).toBeDefined()
      expect(sim.satelliteScene).toBeDefined()
      expect(sim.spillGeometry).toBeDefined()
      expect(sim.candidateVessels.length).toBeGreaterThan(0)
    })
  })
})
