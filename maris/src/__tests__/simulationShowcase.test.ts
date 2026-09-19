import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InvestigationWorkspace } from '../components/layout/InvestigationWorkspace'
import {
  SIMULATION_STAGE_SEQUENCE,
  createSimulationInvestigation,
  getSimulationScenarioById,
  simulationScenarios,
} from '../simulation/simulationEngine'

globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

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

describe('Simulation showcase', () => {
  it('loads deterministic simulation scenarios', () => {
    expect(simulationScenarios).toHaveLength(5)
    expect(getSimulationScenarioById('alpha')?.name).toBe('Arabian Sea — Scenario Alpha')
    expect(getSimulationScenarioById('beta')?.region).toBe('Arabian Sea')
  })

  it('creates a simulation investigation with the expected mode and stage order', () => {
    const scenario = getSimulationScenarioById('gamma')
    expect(scenario).toBeTruthy()

    const created = createSimulationInvestigation('Gamma Demo', 'gamma', scenario!)
    expect(created.mode).toBe('SIMULATION')
    expect(created.scenarioId).toBe('gamma')
    expect(created.name).toBe('Gamma Demo')
    expect(created.stageSequence.map((stage) => stage.id)).toEqual(SIMULATION_STAGE_SEQUENCE.map((stage) => stage.id))
  })

  it('renders the simulation showcase instead of the live loading state', () => {
    const scenario = getSimulationScenarioById('alpha')
    expect(scenario).toBeTruthy()

    render(
      React.createElement(InvestigationWorkspace, {
        activeId: 'simulation-alpha',
        isDemoMode: false,
        isSimulationMode: true,
        simulationScenario: scenario ?? null,
        investigations: [],
        onSelectInvestigation: () => {},
        onOpenCreateModal: () => {},
        onCloseCreateModal: () => {},
      })
    )

    expect(screen.getAllByText(/simulation showcase/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/synthetic scenario active/i)).toBeTruthy()
    expect(screen.queryByText(/loading investigation details/i)).toBeNull()
    expect(screen.getAllByText(/arabian sea/i).length).toBeGreaterThan(0)
  })
})
