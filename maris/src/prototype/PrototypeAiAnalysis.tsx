import { BrainCircuit, CheckCircle2 } from 'lucide-react'

export function PrototypeAiAnalysis({ active }: { active: boolean }) {
  return <section className="prototype-ai" aria-label="Prototype AI analysis">
    <div className="prototype-ai__heading"><div><span className="section-kicker">Prototype layer</span><h3>AI analysis</h3></div><BrainCircuit size={17} aria-hidden="true" /></div>
    <div className="prototype-ai__row"><span>SAR spill segmentation</span><strong><CheckCircle2 size={13} /> {active ? 'Prepared output' : 'Ready'}</strong></div>
    <div className="prototype-ai__meta"><span>Model</span><b>Prototype Inference</b><span>Output</span><b>Spill mask</b></div>
    <div className="confidence-line"><span>Prototype confidence</span><strong>94%</strong></div><div className="confidence-bar"><span /></div>
    <p>Prepared model output for demonstration; not validated model accuracy.</p>
  </section>
}