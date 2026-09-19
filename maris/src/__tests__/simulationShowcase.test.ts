import { describe, expect, it } from 'vitest'
import {
  SIMULATION_STAGE_SEQUENCE,
  createSimulationInvestigation,
  getSimulationScenarioById,
  simulationScenarios,
} from '../simulation/simulationEngine'

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
})
