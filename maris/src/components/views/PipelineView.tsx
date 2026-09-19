import { useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronUp, Compass, Loader2, Play, Radio, Shield, Zap } from 'lucide-react'
import type { InvestigationStatus } from '../../types/investigationApi'

interface PipelineViewProps {
  completedStages?: string[]
  currentStage?: string | null
  workflowStatus?: InvestigationStatus
  onNavigateToView: (view: 'workspace') => void
  onRunWorkflow?: () => void
  isRunningWorkflow?: boolean
}

interface StageDetail {
  id: string
  code: string
  name: string
  category: string
  description: string
  inputArtifacts: string[]
  outputArtifacts: string[]
}

const PIPELINE_STAGES_CATALOG: StageDetail[] = [
  {
    id: 'B1',
    code: 'B1',
    name: 'Satellite Scene Ingestion',
    category: 'Satellite',
    description: 'Retrieves Copernicus Sentinel-1 Level-1 Ground Range Detected (GRD) C-band SAR scenes.',
    inputArtifacts: ['Copernicus Hub Query / AOI Bounding Box'],
    outputArtifacts: ['S1A_GRD_Level1_Product.SAFE'],
  },
  {
    id: 'B2',
    code: 'B2',
    name: 'SAR Calibration & Filtering',
    category: 'Satellite',
    description: 'Applies orbit corrections, radiometric sigma0 backscatter calibration, and speckle filtering.',
    inputArtifacts: ['S1A_GRD_Level1_Product.SAFE'],
    outputArtifacts: ['Calibrated_Sigma0_VV_VH_GeoTIFF'],
  },
  {
    id: 'B3',
    code: 'B3',
    name: 'Spill Detection & Segmentation',
    category: 'Spill Detection',
    description: 'Adaptive thresholding and morphological segmentation to identify dark ocean surface slick polygons.',
    inputArtifacts: ['Calibrated_Sigma0_VV_VH_GeoTIFF'],
    outputArtifacts: ['spill_geometry (GeoJSON Polygon + Centroid)'],
  },
  {
    id: 'C1',
    code: 'C1',
    name: 'Metocean Forcing Ingestion',
    category: 'Environment',
    description: 'Retrieves ECMWF ERA5 10m wind fields and CMEMS ocean surface currents.',
    inputArtifacts: ['Investigation AOI + Time Window'],
    outputArtifacts: ['Metocean_Vector_Grid_NetCDF'],
  },
  {
    id: 'D1',
    code: 'D1',
    name: 'Forward Drift Simulation',
    category: 'Forward Drift',
    description: 'Simulates forward slick trajectory using Runge-Kutta 4th-order particle advection (non-additive cross-check).',
    inputArtifacts: ['spill_geometry', 'Metocean_Vector_Grid_NetCDF'],
    outputArtifacts: ['Forward_Drift_Trajectory_JSON'],
  },
  {
    id: 'D3',
    code: 'D3',
    name: 'Backward Origin Zone Estimation',
    category: 'Backward Origin',
    description: 'Backtracks slick centroid through reversed metocean vectors to compute source candidate zone.',
    inputArtifacts: ['spill_geometry', 'Metocean_Vector_Grid_NetCDF'],
    outputArtifacts: ['drift_product (source_candidate_zone)'],
  },
  {
    id: 'E1',
    code: 'E1',
    name: 'AIS Target Query & Ingestion',
    category: 'AIS Correlation',
    description: 'Queries AIS archives for all vessels intersecting the spatial-temporal origin window.',
    inputArtifacts: ['source_candidate_zone', 'Time Window'],
    outputArtifacts: ['Candidate_Vessel_Registry_JSON'],
  },
  {
    id: 'E2',
    code: 'E2',
    name: 'Trajectory Alignment Analysis',
    category: 'Trajectory',
    description: 'Calculates Closest Point of Approach (CPA) and tests vessel headings against slick major axis.',
    inputArtifacts: ['Candidate_Vessel_Registry_JSON', 'source_candidate_zone'],
    outputArtifacts: ['Vessel_Trajectory_Metrics_JSON'],
  },
  {
    id: 'E3',
    code: 'E3',
    name: 'Contextual Behavioral Observations',
    category: 'AIS Correlation',
    description: 'Identifies transmission gaps, speed drops, and course deviations (0.00 numerical score weight).',
    inputArtifacts: ['Candidate_Vessel_Registry_JSON'],
    outputArtifacts: ['Contextual_Behavioral_Observations_JSON'],
  },
  {
    id: 'F1',
    code: 'F1',
    name: 'Evidence Fusion & Channel Normalization',
    category: 'Evidence Fusion',
    description: 'Normalizes 3 primary evidence channels: Spatial (0.40), Temporal (0.35), Trajectory (0.25).',
    inputArtifacts: ['source_candidate_zone', 'Vessel_Trajectory_Metrics_JSON'],
    outputArtifacts: ['Fused_Evidence_Matrix_JSON'],
  },
  {
    id: 'F2',
    code: 'F2',
    name: 'Candidate Priority Ranking',
    category: 'Attribution',
    description: 'Computes Evidence Consistency Score for each candidate and sorts by consistency level.',
    inputArtifacts: ['Fused_Evidence_Matrix_JSON'],
    outputArtifacts: ['candidate_ranking (Ranked Candidates)'],
  },
  {
    id: 'F3',
    code: 'F3',
    name: 'Explainability & Attribution Report',
    category: 'Attribution',
    description: 'Generates plain-language scientific summaries, channel breakdowns, and mandatory disclaimers.',
    inputArtifacts: ['candidate_ranking', 'Contextual_Behavioral_Observations_JSON'],
    outputArtifacts: ['explainability_report (Final Scientific Artifact)'],
  },
]

export function PipelineView({
  completedStages = [],
  currentStage,
  workflowStatus,
  onNavigateToView,
  onRunWorkflow,
  isRunningWorkflow = false,
}: PipelineViewProps) {
  const [selectedStageId, setSelectedStageId] = useState<string | null>(currentStage || 'B1')

  return (
    <div className="view-container">
      {/* Editorial Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Scientific Pipeline Architecture</span>
          <h1 className="view-headline">12-Stage Mission Execution Timeline</h1>
          <p className="view-lead">
            From Copernicus SAR backscatter calibration through Lagrangian drift backtracking to multi-channel vessel attribution.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
            <Compass size={14} /> Open GIS Workspace
          </button>
          {onRunWorkflow && (
            <button
              className="primary-button"
              type="button"
              onClick={onRunWorkflow}
              disabled={isRunningWorkflow || workflowStatus === 'COMPLETED'}
            >
              {isRunningWorkflow ? <Loader2 size={14} className="spinner" /> : <Play size={14} />}
              <span>{isRunningWorkflow ? 'Executing...' : 'Run Pipeline'}</span>
            </button>
          )}
        </div>
      </div>

      {/* Vertical Mission Timeline */}
      <div className="pipeline-timeline">
        {PIPELINE_STAGES_CATALOG.map((stage) => {
          const isCompleted = completedStages.includes(stage.id)
          const isActive = currentStage === stage.id || isRunningWorkflow && currentStage === stage.id
          const isExpanded = selectedStageId === stage.id

          return (
            <div
              key={stage.id}
              className={`timeline-stage-row ${isCompleted ? 'is-completed' : ''} ${isActive ? 'is-active' : ''}`}
            >
              {/* Timeline Node */}
              <div className="timeline-node">
                {isCompleted ? <CheckCircle2 size={18} color="var(--color-accent)" /> : stage.code}
              </div>

              {/* Stage Content Card */}
              <div
                className="timeline-content"
                style={{ cursor: 'pointer' }}
                onClick={() => setSelectedStageId(isExpanded ? null : stage.id)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                    <span className="section-kicker" style={{ fontSize: '0.65rem' }}>{stage.category}</span>
                    <strong style={{ fontSize: '0.95rem', color: '#fff' }}>{stage.name}</strong>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span
                      className={`status-badge ${
                        isCompleted
                          ? 'status-badge--completed'
                          : isActive
                            ? 'status-badge--processing'
                            : 'status-badge--created'
                      }`}
                    >
                      {isCompleted ? 'COMPLETED' : isActive ? 'EXECUTING' : 'PENDING'}
                    </span>
                    {isExpanded ? <ChevronUp size={14} color="var(--color-text-subtle)" /> : <ChevronDown size={14} color="var(--color-text-subtle)" />}
                  </div>
                </div>

                <p style={{ fontSize: '0.78rem', color: 'var(--color-text-muted)', margin: '0.2rem 0 0', lineHeight: 1.45 }}>
                  {stage.description}
                </p>

                {/* Progressive Technical Details */}
                {isExpanded && (
                  <div
                    style={{
                      marginTop: '0.75rem',
                      paddingTop: '0.75rem',
                      borderTop: '1px solid var(--color-border-subtle)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.5rem',
                    }}
                  >
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                      <div className="metric">
                        <span>Required Input Artifacts</span>
                        <strong style={{ fontSize: '0.72rem', fontFamily: 'var(--font-mono)' }}>
                          {stage.inputArtifacts.join(', ')}
                        </strong>
                      </div>
                      <div className="metric">
                        <span>Output Pipeline Artifacts</span>
                        <strong className="metric--accent" style={{ fontSize: '0.72rem', fontFamily: 'var(--font-mono)' }}>
                          {stage.outputArtifacts.join(', ')}
                        </strong>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
