import { Check, Circle, CircleDot, ShieldAlert } from 'lucide-react'
import type { PipelineStage } from '../../types/maris'
import type { InvestigationStatus } from '../../types/investigationApi'

const G1_PIPELINE_STAGES: Array<{ id: string; label: string; shortLabel: string }> = [
  { id: 'B1', label: 'SAR Scene Ingestion', shortLabel: 'B1 Ingest' },
  { id: 'B2', label: 'SAR Calibration', shortLabel: 'B2 Calibrate' },
  { id: 'B3', label: 'Spill Detection', shortLabel: 'B3 Spill' },
  { id: 'C1', label: 'Metocean Retrieval', shortLabel: 'C1 Metocean' },
  { id: 'D1', label: 'Forward Advection', shortLabel: 'D1 Fwd Drift' },
  { id: 'D3', label: 'Origin Estimation', shortLabel: 'D3 Origin' },
  { id: 'E1', label: 'AIS Target Query', shortLabel: 'E1 AIS' },
  { id: 'E2', label: 'Trajectory Metrics', shortLabel: 'E2 Trajectory' },
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
    const modeLabel = isSimulationMode ? 'SIMULATION' : 'HISTORICAL'

    return (
      <section className="mission-timeline" aria-label="Investigation pipeline timeline">
        <div className="timeline-badge">
          <span className="status-dot status-dot--active" />
          <span>{modeLabel}</span>
        </div>

        <div className="timeline-nodes">
          {stageList.map((stage, index) => {
            const isCompleted = isSimulationMode
              ? index < simulationStages.length - 1
              : stage.status === 'completed'
            const isCurrent = isSimulationMode
              ? index === simulationStages.length - 1
              : stage.status === 'current'

            const stageId = (stage as any).id || `S${index + 1}`
            const stageLabel = (stage as any).shortLabel || stage.label

            return (
              <div
                key={stage.label || stageId}
                className={`timeline-step ${isCurrent ? 'is-current' : isCompleted ? 'is-completed' : 'is-pending'}`}
                title={`${stage.label} — ${isCompleted ? 'Completed' : isCurrent ? 'Active Stage' : 'Pending'}`}
              >
                <div className="timeline-node">
                  {isCompleted ? (
                    <Check size={10} />
                  ) : isCurrent ? (
                    <CircleDot size={11} className="node-icon--pulse" />
                  ) : (
                    <span className="timeline-node-dot" />
                  )}
                </div>
                <span className="timeline-label">{stageId.length <= 3 ? stageId : `S${index + 1}`}</span>
                {index < stageList.length - 1 && <div className={`timeline-connector ${isCompleted ? 'is-complete' : ''}`} />}
              </div>
            )
          })}
        </div>
      </section>
    )
  }

  // Live G1 Investigation Pipeline
  const isFailed = workflowStatus === 'FAILED'
  const isRunning = isExecuting || workflowStatus === 'PROCESSING'

  return (
    <section className="mission-timeline" aria-label="Investigation pipeline timeline">
      <div className="timeline-badge">
        <span className={`status-dot ${isFailed ? 'status-dot--failed' : isRunning ? 'status-dot--pulse' : 'status-dot--active'}`} />
        <span>
          {workflowStatus === 'COMPLETED'
            ? 'B1–F3 VERIFIED'
            : isRunning
              ? 'EXECUTING'
              : isFailed
                ? 'HALTED'
                : 'PIPELINE'}
        </span>
      </div>

      <div className="timeline-nodes">
        {G1_PIPELINE_STAGES.map((stage, index) => {
          const isCompleted = completedStages.includes(stage.id)
          const isStageFailed = isFailed && currentStage === stage.id
          const isCurrent = currentStage === stage.id || (isRunning && !isCompleted && !isStageFailed)

          return (
            <div
              key={stage.id}
              className={`timeline-step ${isStageFailed ? 'is-failed' : isCurrent ? 'is-current' : isCompleted ? 'is-completed' : 'is-pending'}`}
              title={`${stage.id}: ${stage.label} — ${isCompleted ? 'Completed' : isStageFailed ? 'Failed' : isCurrent ? 'In Progress' : 'Pending'}`}
            >
              <div className="timeline-node">
                {isCompleted ? (
                  <Check size={10} />
                ) : isStageFailed ? (
                  <ShieldAlert size={10} />
                ) : isCurrent ? (
                  <CircleDot size={11} className="node-icon--pulse" />
                ) : (
                  <span className="timeline-node-dot" />
                )}
              </div>
              <span className="timeline-label">{stage.id}</span>
              {index < G1_PIPELINE_STAGES.length - 1 && (
                <div className={`timeline-connector ${isCompleted ? 'is-complete' : ''}`} />
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}