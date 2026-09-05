import type { MapLayerVisibility, PipelineStage } from '../types/maris'

export interface PrototypeFlowStage {
  label: string
  shortLabel: string
  reveals: keyof MapLayerVisibility | null
}

export const prototypeFlowStages: PrototypeFlowStage[] = [
  { label: 'Sentinel-1 SAR', shortLabel: 'Satellite scene', reveals: null },
  { label: 'AI Spill Detection', shortLabel: 'Spill detected', reveals: 'spill' },
  { label: 'Environmental Drift', shortLabel: 'Drift reconstructed', reveals: 'drift' },
  { label: 'Origin Estimation', shortLabel: 'Origin zone', reveals: null },
  { label: 'AIS Correlation', shortLabel: 'AIS correlated', reveals: 'vessels' },
  { label: 'Candidate Ranking', shortLabel: 'Candidates ranked', reveals: null },
]

export function getPrototypePipelineStages(activeIndex: number): PipelineStage[] {
  return prototypeFlowStages.map((stage, index) => ({
    label: stage.shortLabel,
    status: index < activeIndex ? 'completed' : index === activeIndex ? 'current' : 'pending',
  }))
}