import { Anchor, Check, ChevronRight, CircleAlert, Compass, Crosshair, Minus, Ship, Wind } from 'lucide-react'
import { driftSummary } from '../../data/driftData'
import type { CandidateAttribution, IncidentData } from '../../types/maris'
import { PrototypeAiAnalysis } from '../../prototype/PrototypeAiAnalysis'

interface AnalysisPanelProps {
  incident: IncidentData
  candidates: CandidateAttribution[]
  selectedCandidate: string
  onSelectCandidate: (id: string) => void
  prototypeActive: boolean
}

function Metric({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return <div className="metric"><span>{label}</span><strong className={tone}>{value}</strong></div>
}

function DriftAnalysis() {
  return <section className="panel-section compact-section drift-analysis">
    <div className="section-title-line"><div><h3>Environmental drift reconstruction</h3><p className="section-caption">Prototype Environmental Reconstruction</p></div><Wind size={15} aria-hidden="true" /></div>
    <div className="metric-grid"><Metric label="Forcing" value={driftSummary.forcing.wind} /><Metric label="Windage" value={`${driftSummary.forcing.windageFraction * 100}% prototype assumption`} /><Metric label="Integration" value={`${driftSummary.forcing.integrationStepMinutes}-minute steps`} /><Metric label="First SAR position" value={`${driftSummary.result.estimatedPositionAtFirstSAR.lat.toFixed(4)} N, ${driftSummary.result.estimatedPositionAtFirstSAR.lon.toFixed(4)} E`} /><Metric label="Displacement" value={`Approx. ${driftSummary.result.displacementKm.toFixed(2)} km`} /></div>
    <p className="limitation-note">Uses nearest-grid environmental forcing and a prototype windage assumption; not an operational spill forecast.</p>
  </section>
}

function AttributionFactors({ candidate }: { candidate: CandidateAttribution }) {
  return <div className="attribution-factors"><div className="factor-heading"><span>Attribution factors</span><span>Weight</span></div>{candidate.factors.map((factor) => <div className="factor-row" key={factor.label}><span className="factor-label">{factor.status === 'calculated' || factor.status === 'documented' ? <Check size={12} /> : <Minus size={12} />}{factor.label}</span><span className="factor-weight">{factor.weight}%</span><small>{factor.detail}</small></div>)}</div>
}

export function AnalysisPanel({ incident, candidates, selectedCandidate, onSelectCandidate, prototypeActive }: AnalysisPanelProps) {
  return <aside className="panel analysis-panel" aria-label="Investigation analysis">
    <div className="panel-heading"><div><span className="section-kicker">Evidence review</span><h2>Analysis</h2></div><Compass size={18} className="heading-icon" aria-hidden="true" /></div>
    <section className="panel-section compact-section"><div className="section-title-line"><h3>Spill analysis</h3><CircleAlert size={15} aria-hidden="true" /></div><div className="metric-grid"><Metric label="Detection" value="Observed slick - reconstructed" tone="metric--accent" /><Metric label="Area" value={incident.estimatedArea} /><Metric label="Coordinates" value={incident.coordinates} /><Metric label="Confidence" value={incident.confidence} tone="metric--accent" /></div></section>
    <PrototypeAiAnalysis active={prototypeActive} />
    <DriftAnalysis />
    <section className="panel-section compact-section"><div className="section-title-line"><h3>Origin analysis</h3><Crosshair size={15} aria-hidden="true" /></div><div className="metric-grid"><Metric label="Status" value="Estimated reference" tone="metric--pending" /><Metric label="Origin" value={incident.origin} /><Metric label="Time window" value={incident.originWindow} /><Metric label="Confidence" value="Not calculated" /></div></section>
    <section className="panel-section compact-section"><div className="section-title-line"><h3>Vessel analysis</h3><Ship size={15} aria-hidden="true" /></div><div className="metric-grid"><Metric label="Analyzed" value={`${incident.vesselCount} vessels`} /><Metric label="Candidates" value={`${incident.relevantCandidates} candidates`} /><Metric label="AIS status" value="Historical AIS - reconstructed" /></div></section>
    <section className="panel-section candidates-section"><div className="section-title-line"><div><h3>Candidate vessels</h3><p className="section-caption">Partial-data attribution</p></div><Anchor size={15} aria-hidden="true" /></div><p className="ranking-notice">Highest-ranked candidate indicates investigation priority, not confirmed responsibility.</p><div className="candidate-list">{candidates.map((candidate) => <button key={candidate.id} className={`candidate-card${selectedCandidate === candidate.id ? ' is-selected' : ''}`} type="button" onClick={() => onSelectCandidate(candidate.id)}><span className={`candidate-rank candidate-rank--${candidate.color}`}>0{candidate.rank}</span><span className="candidate-details"><strong>{candidate.id}</strong><small>{candidate.overallLabel}</small><span className="evidence-placeholder">MMSI {candidate.mmsi} - IMO {candidate.imo}</span></span><span className="candidate-arrow"><ChevronRight size={15} /></span><AttributionFactors candidate={candidate} /></button>)}</div></section>
  </aside>
}
