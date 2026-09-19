export type SimulationMode = 'SIMULATION'

export interface SimulationStageStep {
  id: string
  label: string
  shortLabel: string
  description: string
}

export interface SimulationTrackPoint {
  timestamp: string
  longitude: number
  latitude: number
}

export interface SimulationVessel {
  id: string
  name: string
  mmsi: string
  imo: string
  vesselType: string
  color: string
  track: SimulationTrackPoint[]
  candidateScore: number
  isCandidate: boolean
  spatialConsistency: number
  temporalConsistency: number
  trajectoryConsistency: number
  note: string
}

export interface SimulationScenario {
  id: string
  name: string
  region: string
  timestamp: string
  satelliteScene: string
  spillGeometry: { type: 'Polygon'; coordinates: number[][][] }
  spillAreaKm2: number
  sourceZone: { type: 'Polygon'; coordinates: number[][][] }
  environmentalDrift: {
    wind: string
    current: string
    displacementKm: number
    bearing: string
    sourceZone: string
  }
  vesselTracks: Array<{
    id: string
    label: string
    color: string
    route: SimulationTrackPoint[]
  }>
  candidateVessels: SimulationVessel[]
  evidenceSummary: {
    spatial: number
    temporal: number
    trajectory: number
    availability: string
    primaryFinding: string
  }
  analysis: {
    spill: string
    drift: string
    vesselCorrelation: string
    final: string
  }
  finalExplanation: string
  databaseSummary: {
    region: string
    timeWindow: string
    searchRadiusKm: number
    simulatedRecordsFound: number
    spatiallyRelevant: number
    candidateVessels: number
  }
}

export interface SimulationInvestigation {
  id: string
  name: string
  mode: SimulationMode
  scenarioId: string
  region: string
  timestamp: string
  stageSequence: SimulationStageStep[]
  scenario: SimulationScenario
}
