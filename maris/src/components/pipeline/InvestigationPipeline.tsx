import { Check, Circle, CircleDot, ShieldAlert } from 'lucide-react'
import type { PipelineStage } from '../../types/maris'
import type { InvestigationStatus } from '../../types/investigationApi'

const G1_PIPELINE_STAGES: Array<{ id: string; label: string; shortLabel: string }> = [
  { id: 'B1', label: 'SAR Ingestion', shortLabel: 'B1 Ingest' },
  { id: 'B2', label: 'SAR Calibration', shortLabel: 'B2 Calibrate' },
  { id: 'B3', label: 'Spill Detection', shortLabel: 'B3 Spill' },
  { id: 'C1', label: 'Metocean Data', shortLabel: 'C1 Metocean' },
  { id: 'D1', label: 'Forward Drift', shortLabel: 'D1 Fwd Drift' },
  { id: 'D3', label: 'Source Estimation', shortLabel: 'D3 Origin' },
  { id: 'E1', label: 'Candidate Vessels', shortLabel: 'E1 AIS Query' },
  { id: 'E2', label: 'Trajectory Analysis', shortLabel: 'E2 Trajectory' },
  { id: 'E3', label: 'Behavioral Intel', shortLabel: 'E3 Behavior' },
  { id: 'F1', label: 'Evidence Fusion', shortLabel: 'F1 Fusion' },
  { id: 'F2', label: 'Candidate Ranking', shortLabel: 'F2 Ranking' },
  { id: 'F3', label: 'Explainability', shortLabel: 'F3 Report' },
]

interface InvestigationPipelineProps {
  isDemoMode: boolean
  isSimulationMode?: boolean
  simulationStages?: Array<{ id: string; label: string; shortLabel: string; description: string }>
  demoStages?: PipelineStage[]
  completedStages?: string[]
  currentStage?: string | null
  workflowStatus?: InvestigationStatus
  isExecuting?: boolean
}

export function InvestigationPipeline({
  isDemoMode,
  isSimulationMode = false,
  simulationStages = [],
  demoStages = [],
  completedStages = [],
  currentStage,
  workflowStatus,
  isExecuting = false,
}: InvestigationPipelineProps) {
  if (isDemoMode || isSimulationMode) {
    const stageList = isSimulationMode ? simulationStages : demoStages
    const note = isSimulationMode ? 'SIMULATION SHOWCASE MODE' : 'HISTORICAL DEMO CASE'
    return (
      <section className="pipeline" aria-label="Investigation pipeline">
        <div className="pipeline-header">
          <span className="section-kicker">Investigation workflow</span>
          <span className="pipeline-note">{note}</span>
        </div>
        <div className="pipeline-track">
          {stageList.map((stage, index) => (
            <div className="pipeline-stage" key={stage.label || stage.id}>
              <div className={`pipeline-node ${isSimulationMode ? 'pipeline-node--completed' : `pipeline-node--${stage.status}`}`}>
                {isSimulationMode ? (
                  index < simulationStages.length - 1 ? <Check size={13} /> : <CircleDot size={14} />
                ) : stage.status === 'completed' ? (
                  <Check size={13} />
                ) : stage.status === 'current' ? (
                  <CircleDot size={14} />
                ) : (
                  <Circle size={9} />
                )}
              </div>
              <span>{stage.label}</span>
              {index < stageList.length - 1 && (
                <div className={`pipeline-connector${isSimulationMode ? ' is-complete' : stage.status === 'completed' ? ' is-complete' : ''}`} />
              )}
            </div>
          ))}
        </div>
      </section>
    )
  }

  // Live G1 Investigation Pipeline
  const isFailed = workflowStatus === 'FAILED'
  const isRunning = isExecuting || workflowStatus === 'PROCESSING'

  return (
    <section className="pipeline" aria-label="Investigation pipeline">
      <div className="pipeline-header">
        <span className="section-kicker">Scientific Pipeline</span>
        <span className="pipeline-note">
          {workflowStatus === 'COMPLETED'
            ? 'ALL STAGES COMPLETED (B1 → F3)'
            : isRunning
              ? 'PROCESSING (SYNCHRONOUS RUN IN PROGRESS B1 → F3)'
              : isFailed
                ? `PIPELINE HALTED AT STAGE ${currentStage || 'UNKNOWN'}`
                : 'READY FOR WORKFLOW EXECUTION'}
        </span>
      </div>

      <div className="pipeline-track">
        {G1_PIPELINE_STAGES.map((stage, index) => {
          const isCompleted = completedStages.includes(stage.id)
          const isStageFailed = isFailed && currentStage === stage.id
          const isNodeProcessing = isRunning && !isCompleted && !isStageFailed

          let nodeClass = 'pipeline-node--pending'
          if (isCompleted) nodeClass = 'pipeline-node--completed'
          else if (isStageFailed) nodeClass = 'pipeline-node--failed'
          else if (isNodeProcessing) nodeClass = 'pipeline-node--in-progress'

          return (
            <div className="pipeline-stage" key={stage.id} title={stage.label}>
              <div className={`pipeline-node ${nodeClass}`}>
                {isCompleted ? (
                  <Check size={13} />
                ) : isStageFailed ? (
                  <ShieldAlert size={13} />
                ) : isNodeProcessing ? (
                  <CircleDot size={14} className="node-icon--pulse" />
                ) : (
                  <Circle size={9} />
                )}
              </div>
              <span>{stage.shortLabel}</span>
              {index < G1_PIPELINE_STAGES.length - 1 && (
                <div className={`pipeline-connector${isCompleted ? ' is-complete' : ''}`} />
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}