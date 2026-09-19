import { BrainCircuit, CheckCircle2 } from 'lucide-react'

export function PrototypeAiAnalysis({ active }: { active: boolean }) {
  return (
    <section className="prototype-ai" aria-label="Prototype AI analysis">
      <div className="prototype-ai__heading">
        <div>
          <span className="section-kicker">Prototype layer</span>
          <h3>AI Analysis</h3>
        </div>
        <BrainCircuit size={17} aria-hidden="true" />
      </div>

      <div className="prototype-ai__row">
        <span>SAR spill segmentation</span>
        <strong className="status-badge status-badge--completed" style={{ fontSize: '0.7rem' }}>
          <CheckCircle2 size={12} /> {active ? 'Prepared output' : 'Ready'}
        </strong>
      </div>

      <div className="prototype-ai__spec-grid">
        <div className="spec-item">
          <span className="spec-label">Model: </span>
          <strong className="spec-value">Prototype Inference</strong>
        </div>
        <div className="spec-item">
          <span className="spec-label">Output: </span>
          <strong className="spec-value">Spill mask</strong>
        </div>
      </div>

      <div className="confidence-line">
        <span>Prototype confidence</span>
        <strong>94%</strong>
      </div>
      <div className="confidence-bar">
        <span style={{ width: '94%' }} />
      </div>

      <p className="limitation-note" style={{ margin: '0.5rem 0 0', fontSize: '0.62rem', color: 'var(--color-text-subtle)' }}>
        Prepared model output for demonstration; not validated model accuracy.
      </p>
    </section>
  )
}