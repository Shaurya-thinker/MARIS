import type { IncidentData, PipelineStage } from '../types/maris'
import { candidateAttributions } from './attributionData'

export const incidentData: IncidentData = {
  id: 'MARIS-HIST-2018-001', region: 'North of Cap Corse, Mediterranean', status: 'Historical case review',
  detectionTime: '7 Oct 2018 - source incident', lastUpdated: '8 Oct 2018 - 05:28 UTC',
  satelliteSource: 'Sentinel-1A / C-band SAR', satelliteImage: '/satellite/corsica_2018_s1.jpg', acquisitionTime: '8 Oct 2018 - 05:28 UTC', sceneStatus: 'Evidence available',
  estimatedArea: 'Derived later', confidence: 'Not calculated', coordinates: '43.24833 N, 9.47833 E', origin: 'Collision reference',
  originWindow: '7 Oct 2018 - historical case', vesselCount: 2, relevantCandidates: 2, aisWindow: 'Historical AIS - reconstructed',
}

export const candidateVessels = candidateAttributions

export const pipelineStages: PipelineStage[] = [
  { label: 'Satellite', status: 'completed' }, { label: 'Spill Detection', status: 'completed' },
  { label: 'Drift Analysis', status: 'completed' }, { label: 'Origin Estimation', status: 'completed' },
  { label: 'AIS Correlation', status: 'completed' }, { label: 'Candidate Ranking', status: 'current' },
]