import { Check, Circle, CircleDot } from 'lucide-react'
import type { PipelineStage } from '../../types/maris'

export function InvestigationPipeline({ stages }: { stages: PipelineStage[] }) {
  return <section className="pipeline" aria-label="Investigation pipeline"><div className="pipeline-header"><span className="section-kicker">Investigation workflow</span><span className="pipeline-note">HISTORICAL CASE</span></div><div className="pipeline-track">{stages.map((stage, index) => <div className="pipeline-stage" key={stage.label}><div className={`pipeline-node pipeline-node--${stage.status}`}>{stage.status === 'completed' ? <Check size={13} /> : stage.status === 'current' ? <CircleDot size={14} /> : <Circle size={9} />}</div><span>{stage.label}</span>{index < stages.length - 1 && <div className={`pipeline-connector${stage.status === 'completed' ? ' is-complete' : ''}`} />}</div>)}</div></section>
}