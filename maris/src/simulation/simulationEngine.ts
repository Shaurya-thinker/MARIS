import type { SimulationInvestigation, SimulationScenario, SimulationStageStep } from './simulationTypes'
import { scenarioAlpha } from './scenarios/scenarioAlpha'
import { scenarioBeta } from './scenarios/scenarioBeta'
import { scenarioGamma } from './scenarios/scenarioGamma'
import { scenarioDelta } from './scenarios/scenarioDelta'
import { scenarioEpsilon } from './scenarios/scenarioEpsilon'

export const SIMULATION_STAGE_SEQUENCE: SimulationStageStep[] = [
  { id: 'created', label: 'Investigation Created', shortLabel: 'Investigation created', description: 'Simulation investigation initialized.' },
  { id: 'loading-satellite', label: 'Simulation — Loading satellite evidence', shortLabel: 'Loading satellite evidence', description: 'Loading the demonstration scene and spill context.' },
  { id: 'detecting-spill', label: 'Simulation — Detecting suspected spill', shortLabel: 'Suspected spill', description: 'The synthetic spill anomaly is identified in the scene.' },
  { id: 'drift', label: 'Simulation — Reconstructing environmental drift', shortLabel: 'Environmental drift', description: 'Current and wind forcing are reconstructed for the scenario.' },
  { id: 'searching-vessels', label: 'Simulation — Searching vessel database', shortLabel: 'Searching vessel records', description: 'Scenario-specific vessel records are queried and filtered.' },
  { id: 'correlating', label: 'Simulation — Correlating vessel trajectories', shortLabel: 'Correlating trajectories', description: 'Candidate vessels are matched to the spill timeline.' },
  { id: 'fusion', label: 'Simulation — Fusing evidence', shortLabel: 'Fusing evidence', description: 'Spatial, temporal, and trajectory evidence are combined.' },
  { id: 'explanation', label: 'Simulation — Generating explanation', shortLabel: 'Generating explanation', description: 'The narrative explanation is assembled from the scenario evidence.' },
  { id: 'ready', label: 'Simulation — Analysis ready', shortLabel: 'Analysis ready', description: 'The synthetic case is ready for review.' },
]

export const simulationScenarios: SimulationScenario[] = [
  scenarioAlpha,
  scenarioBeta,
  scenarioGamma,
  scenarioDelta,
  scenarioEpsilon,
]

export function getSimulationScenarioById(id: string): SimulationScenario | undefined {
  return simulationScenarios.find((scenario) => scenario.id === id)
}

export function createSimulationInvestigation(
  name: string,
  scenarioId: string,
  scenario: SimulationScenario
): SimulationInvestigation {
  return {
    id: `simulation-${scenarioId}`,
    name: name || `${scenario.name} Demo`,
    mode: 'SIMULATION',
    scenarioId,
    region: scenario.region,
    timestamp: scenario.timestamp,
    stageSequence: SIMULATION_STAGE_SEQUENCE,
    scenario,
  }
}
