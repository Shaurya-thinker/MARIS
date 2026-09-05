import type { Feature, Point, Polygon } from 'geojson'

export type InvestigationStatus = 'completed' | 'current' | 'pending'

export type MapLayerVisibility = { spill: boolean; drift: boolean; vessels: boolean }

export type SpillProperties = Record<string, unknown>
export interface OriginProperties { kind: 'origin-zone' | 'origin-point' }
export interface DriftProperties { direction: 'backward' | 'forward' }
export interface VesselProperties { vesselId: string; candidate: boolean }

export interface MapDemoData {
  spillGeoJsonUrl: string
  aisJsonUrl: string
  driftJsonUrl: string
  originZone: Feature<Polygon, OriginProperties>
  originPoint: Feature<Point, OriginProperties>
}

export interface DriftReconstructionData {
  status: string
  origin: { lat: number; lon: number; timestamp: string; source: string }
  observationTarget: { timestamp: string; source: string }
  forcing: { wind: string; current: string; windageFraction: number; integrationStepMinutes: number; method: string }
  result: { estimatedPositionAtFirstSAR: { lat: number; lon: number }; displacementKm: number; bearingFromOrigin_deg: number }
  trajectory: Array<{ timestamp: string; lat: number; lon: number; stage: string }>
  limitations: string[]
}

export interface ReconstructedAisVessel {
  name: 'ULYSSE' | 'CSL VIRGINIA'
  imo: string
  mmsi: string
  callsign: string
  flag: string
  vesselType: string
  role: string
  track: Array<{ timestamp: string; lat: number; lon: number; sogKn: number; event: string; dataType: string; confidence: string; source: string }>
  approach?: { courseDeg: number; dataType: string; confidence: string; source: string; note: string }
  anchor?: { swingCircleDiameterM: number; chainLengthMApprox: number; dataType: string; source: string }
}

export interface ReconstructedAisData {
  incident: { name: string; date: string; location: { lat: number; lon: number }; dataType: string; source: string; note: string }
  vessels: ReconstructedAisVessel[]
}

export interface IncidentData {
  id: string
  region: string
  status: string
  detectionTime: string
  lastUpdated: string
  satelliteSource: string
  satelliteImage: string
  acquisitionTime: string
  sceneStatus: string
  estimatedArea: string
  confidence: string
  coordinates: string
  origin: string
  originWindow: string
  vesselCount: number
  relevantCandidates: number
  aisWindow: string
}

export interface CandidateVessel {
  id: string
  mmsi: string
  imo: string
  role: string
  priority: string
  color: 'accent' | 'warning' | 'muted'
}

export type AttributionEvidenceStatus = 'calculated' | 'documented' | 'insufficient'

export interface AttributionFactor {
  label: string
  weight: number
  status: AttributionEvidenceStatus
  detail: string
}

export interface CandidateAttribution extends CandidateVessel {
  rank: number
  factors: AttributionFactor[]
  overallLabel: 'Highest-Ranked Candidate' | 'Candidate'
}

export interface PipelineStage {
  label: string
  status: InvestigationStatus
}