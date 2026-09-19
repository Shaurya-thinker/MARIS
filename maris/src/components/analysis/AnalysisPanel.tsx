import {
  Activity,
  Anchor,
  Check,
  ChevronRight,
  Circle,
  CircleAlert,
  Compass,
  Crosshair,
  HelpCircle,
  Info,
  Loader2,
  Minus,
  Shield,
  Ship,
  Wind,
} from 'lucide-react'
import { driftSummary } from '../../data/driftData'
import type { CandidateAttribution, IncidentData } from '../../types/maris'
import type {
  CandidateExplanation,
  CandidateRanking,
  ExplainabilityReport,
  RankedCandidate,
} from '../../types/investigationApi'
import { PrototypeAiAnalysis } from '../../prototype/PrototypeAiAnalysis'
import type { SimulationScenario } from '../../simulation/simulationTypes'

interface AnalysisPanelProps {
  isDemoMode: boolean
  isSimulationMode?: boolean
  simulationScenario?: SimulationScenario | null
  simulationStageIndex?: number
  simulationStages?: Array<{ id: string; label: string; shortLabel: string; description: string }>
  demoIncident: IncidentData
  demoCandidates: CandidateAttribution[]
  selectedCandidate: string
  onSelectCandidate: (id: string) => void
  prototypeActive: boolean
  rankingResult: CandidateRanking | null
  explainabilityReport: ExplainabilityReport | null
}

function Metric({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  )
}

function DriftAnalysis() {
  return (
    <section className="panel-section compact-section drift-analysis">
      <div className="section-title-line">
        <div>
          <h3>Environmental drift reconstruction</h3>
          <p className="section-caption">Prototype Environmental Reconstruction</p>
        </div>
        <Wind size={15} aria-hidden="true" />
      </div>
      <div className="metric-grid">
        <Metric label="Forcing" value={driftSummary.forcing.wind} />
        <Metric label="Windage" value={`${driftSummary.forcing.windageFraction * 100}% prototype assumption`} />
        <Metric label="Integration" value={`${driftSummary.forcing.integrationStepMinutes}-minute steps`} />
        <Metric
          label="First SAR position"
          value={`${driftSummary.result.estimatedPositionAtFirstSAR.lat.toFixed(4)} N, ${driftSummary.result.estimatedPositionAtFirstSAR.lon.toFixed(4)} E`}
        />
        <Metric label="Displacement" value={`Approx. ${driftSummary.result.displacementKm.toFixed(2)} km`} />
      </div>
      <p className="limitation-note">
        Uses nearest-grid environmental forcing and a prototype windage assumption; not an operational spill forecast.
      </p>
    </section>
  )
}

function DemoAttributionFactors({ candidate }: { candidate: CandidateAttribution }) {
  return (
    <div className="attribution-factors">
      <div className="factor-heading">
        <span>Attribution factors</span>
        <span>Weight</span>
      </div>
      {candidate.factors.map((factor) => (
        <div className="factor-row" key={factor.label}>
          <span className="factor-label">
            {factor.status === 'calculated' || factor.status === 'documented' ? <Check size={12} /> : <Minus size={12} />}
            {factor.label}
          </span>
          <span className="factor-weight">{factor.weight}%</span>
          <small>{factor.detail}</small>
        </div>
      ))}
    </div>
  )
}

export function AnalysisPanel({
  isDemoMode,
  isSimulationMode = false,
  simulationScenario,
  simulationStageIndex = 0,
  simulationStages = [],
  demoIncident,
  demoCandidates,
  selectedCandidate,
  onSelectCandidate,
  prototypeActive,
  rankingResult,
  explainabilityReport,
}: AnalysisPanelProps) {
  // 1. Demo Mode
  if (isDemoMode) {
    return (
      <aside className="panel analysis-panel" aria-label="Investigation analysis">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Evidence review (Demo)</span>
            <h2>Analysis</h2>
          </div>
          <Compass size={18} className="heading-icon" aria-hidden="true" />
        </div>
        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Spill analysis</h3>
            <CircleAlert size={15} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Detection" value="Observed slick - reconstructed" tone="metric--accent" />
            <Metric label="Area" value={demoIncident.estimatedArea} />
            <Metric label="Coordinates" value={demoIncident.coordinates} />
            <Metric label="Confidence" value={demoIncident.confidence} tone="metric--accent" />
          </div>
        </section>
        <PrototypeAiAnalysis active={prototypeActive} />
        <DriftAnalysis />
        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Origin analysis</h3>
            <Crosshair size={15} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Status" value="Estimated reference" tone="metric--pending" />
            <Metric label="Origin" value={demoIncident.origin} />
            <Metric label="Time window" value={demoIncident.originWindow} />
            <Metric label="Confidence" value="Not calculated" />
          </div>
        </section>
        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Vessel analysis</h3>
            <Ship size={15} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Analyzed" value={`${demoIncident.vesselCount} vessels`} />
            <Metric label="Candidates" value={`${demoIncident.relevantCandidates} candidates`} />
            <Metric label="AIS status" value="Historical AIS - reconstructed" />
          </div>
        </section>
        <section className="panel-section candidates-section">
          <div className="section-title-line">
            <div>
              <h3>Candidate vessels</h3>
              <p className="section-caption">Historical demo ranking</p>
            </div>
            <Anchor size={15} aria-hidden="true" />
          </div>
          <p className="ranking-notice">
            Highest-ranked candidate indicates investigation priority, not confirmed responsibility.
          </p>
          <div className="candidate-list">
            {demoCandidates.map((candidate) => (
              <button
                key={candidate.id}
                className={`candidate-card${selectedCandidate === candidate.id ? ' is-selected' : ''}`}
                type="button"
                onClick={() => onSelectCandidate(candidate.id)}
              >
                <span className={`candidate-rank candidate-rank--${candidate.color}`}>0{candidate.rank}</span>
                <span className="candidate-details">
                  <strong>{candidate.id}</strong>
                  <small>{candidate.overallLabel}</small>
                  <span className="evidence-placeholder">
                    MMSI {candidate.mmsi} - IMO {candidate.imo}
                  </span>
                </span>
                <span className="candidate-arrow">
                  <ChevronRight size={15} />
                </span>
                <DemoAttributionFactors candidate={candidate} />
              </button>
            ))}
          </div>
        </section>
      </aside>
    )
  }

  if (isSimulationMode && simulationScenario) {
    const selectedSimulationCandidate =
      simulationScenario.candidateVessels.find((candidate) => candidate.id === selectedCandidate) ??
      simulationScenario.candidateVessels[0]

    const analysisSteps = (simulationStages.length > 0 ? simulationStages.slice(1) : []).map((stage) => ({
      id: stage.id,
      label: stage.label,
      shortLabel: stage.shortLabel,
      description: stage.description,
    }))

    const isFinalSimulationState = simulationStageIndex >= Math.max(simulationStages.length - 1, 0)

    if (!isFinalSimulationState) {
      const activeIndex = Math.min(Math.max(simulationStageIndex, 0), Math.max(analysisSteps.length - 1, 0))

      return (
        <aside className="panel analysis-panel" aria-label="Investigation analysis">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Simulation Analysis</span>
              <h2>{simulationScenario.name}</h2>
            </div>
            <Compass size={18} className="heading-icon" aria-hidden="true" />
          </div>

          <section className="panel-section compact-section">
            <div className="section-title-line">
              <div>
                <h3>Analysis in progress</h3>
                <p className="section-caption">Deterministic simulation workflow</p>
              </div>
              <Loader2 size={15} aria-hidden="true" className="spinner" />
            </div>
            <div className="metric-grid">
              <Metric label="Current step" value={analysisSteps[activeIndex]?.shortLabel || 'Preparing analysis'} tone="metric--accent" />
              <Metric label="Progress" value={`${Math.round(((activeIndex + 1) / Math.max(analysisSteps.length, 1)) * 100)}%`} />
            </div>
          </section>

          <section className="panel-section">
            <div className="section-title-line">
              <div>
                <h3>Processing chain</h3>
                <p className="section-caption">Pending → processing → completed</p>
              </div>
              <Activity size={15} aria-hidden="true" />
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {analysisSteps.map((step, index) => {
                const isCompleted = index < activeIndex
                const isActive = index === activeIndex
                const status = isCompleted ? 'completed' : isActive ? 'processing' : 'pending'

                return (
                  <div
                    key={step.id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 10,
                      padding: '10px 12px',
                      borderRadius: 10,
                      border: `1px solid ${status === 'completed' ? 'rgba(57, 197, 141, 0.45)' : status === 'processing' ? 'rgba(96, 165, 250, 0.6)' : 'rgba(148, 163, 184, 0.3)'}`,
                      background: status === 'completed' ? 'rgba(27, 94, 76, 0.18)' : status === 'processing' ? 'rgba(30, 64, 175, 0.18)' : 'rgba(15, 23, 42, 0.18)',
                      opacity: status === 'pending' ? 0.7 : 1,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 22, minWidth: 22, height: 22, marginTop: 2 }}>
                      {status === 'completed' ? <Check size={14} color="#58d68d" /> : status === 'processing' ? <Loader2 size={14} className="spinner" /> : <Circle size={10} color="#94a3b8" />}
                    </div>
                    <div style={{ flex: 1 }}>
                      <strong style={{ display: 'block', fontSize: 13, marginBottom: 2 }}>{step.shortLabel}</strong>
                      <small style={{ display: 'block', color: 'rgba(226,232,240,0.72)' }}>{step.description}</small>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="panel-section compact-section">
            <div className="section-title-line">
              <div>
                <h3>Current operation</h3>
                <p className="section-caption">Live simulation detail</p>
              </div>
              <Activity size={15} aria-hidden="true" />
            </div>
            <div className="metric-grid">
              <Metric label="Task" value={analysisSteps[activeIndex]?.shortLabel || 'Preparing analysis'} tone="metric--accent" />
              <Metric label="Evidence" value={simulationScenario.evidenceSummary.availability} />
            </div>
          </section>

          <section className="panel-section compact-section">
            <div className="section-title-line">
              <div>
                <h3>Investigation report</h3>
                <p className="section-caption">Simulation case summary</p>
              </div>
              <Info size={15} aria-hidden="true" />
            </div>
            <div className="metric-grid">
              <Metric label="Records found" value={String(simulationScenario.databaseSummary.simulatedRecordsFound)} />
              <Metric label="Relevant matches" value={String(simulationScenario.databaseSummary.spatiallyRelevant)} />
              <Metric label="Mode" value="Simulation Mode" tone="metric--accent" />
            </div>
          </section>

          <div className="scientific-disclaimer">
            <Shield size={14} className="disclaimer-icon" />
            <p>Simulation Mode · Synthetic data only · Analysis is running in the frontend demonstration layer.</p>
          </div>
        </aside>
      )
    }

    return (
      <aside className="panel analysis-panel" aria-label="Investigation analysis">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Simulation Analysis</span>
            <h2>{simulationScenario.name}</h2>
          </div>
          <Compass size={18} className="heading-icon" aria-hidden="true" />
        </div>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Spill evidence</h3>
            <CircleAlert size={15} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Detection" value="Synthetic spill anomaly" tone="metric--accent" />
            <Metric label="Area" value={`${simulationScenario.spillAreaKm2.toFixed(1)} km²`} />
            <Metric label="Drift" value={`${simulationScenario.environmentalDrift.displacementKm.toFixed(1)} km`} />
            <Metric label="Bearing" value={simulationScenario.environmentalDrift.bearing} />
            <Metric label="Confidence" value={`${Math.round(simulationScenario.evidenceSummary.spatial * 100)}%`} tone="metric--accent" />
          </div>
        </section>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Source and drift</h3>
            <Crosshair size={15} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Wind" value={simulationScenario.environmentalDrift.wind} />
            <Metric label="Current" value={simulationScenario.environmentalDrift.current} />
            <Metric label="Source zone" value={simulationScenario.environmentalDrift.sourceZone} />
            <Metric label="Availability" value={simulationScenario.evidenceSummary.availability} />
          </div>
        </section>

        <section className="panel-section candidates-section">
          <div className="section-title-line">
            <div>
              <h3>Candidate vessels</h3>
              <p className="section-caption">Synthetic ranking</p>
            </div>
            <Anchor size={15} aria-hidden="true" />
          </div>
          <p className="ranking-notice">Synthetic candidate ranking for frontend showcase only. This is not a scientific attribution result.</p>
          <div className="candidate-list">
            {simulationScenario.candidateVessels.map((candidate) => (
              <button
                key={candidate.id}
                className={`candidate-card${selectedCandidate === candidate.id ? ' is-selected' : ''}`}
                type="button"
                onClick={() => onSelectCandidate(candidate.id)}
              >
                <span className="candidate-rank" style={{ background: candidate.color }}>{candidate.isCandidate ? '01' : '99'}</span>
                <span className="candidate-details">
                  <strong>{candidate.name}</strong>
                  <small>{candidate.vesselType}</small>
                  <span className="evidence-placeholder">{candidate.note}</span>
                </span>
                <span className="candidate-arrow">
                  <ChevronRight size={15} />
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <div>
              <h3>Final analysis conclusion</h3>
              <p className="section-caption">Investigation report</p>
            </div>
            <Shield size={15} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Leading candidate" value={selectedSimulationCandidate.name} tone="metric--accent" />
            <Metric label="Evidence score" value={`${Math.round(selectedSimulationCandidate.candidateScore * 100)}%`} />
            <Metric label="Spatial match" value={`${Math.round(selectedSimulationCandidate.spatialConsistency * 100)}%`} />
            <Metric label="Temporal match" value={`${Math.round(selectedSimulationCandidate.temporalConsistency * 100)}%`} />
          </div>
          <p className="ranking-notice">{simulationScenario.analysis.final}</p>
        </section>

        <section className="panel-section candidate-explainability">
          <div className="section-title-line">
            <div>
              <h3>Evidence &amp; explainability</h3>
              <p className="section-caption">Synthetic review narrative</p>
            </div>
            <Shield size={15} aria-hidden="true" />
          </div>
          <div className="explanation-block">
            <h4>Investigation summary</h4>
            <p className="channel-desc">{simulationScenario.analysis.spill}</p>
            <p className="channel-desc">{simulationScenario.analysis.drift}</p>
            <p className="channel-desc">{simulationScenario.analysis.vesselCorrelation}</p>
          </div>
          <div className="explanation-block contextual-block">
            <h4>Attribution perspective</h4>
            <p className="channel-desc">{simulationScenario.finalExplanation}</p>
          </div>
          <div className="scientific-disclaimer">
            <Shield size={14} className="disclaimer-icon" />
            <p>Simulation Mode · Synthetic data only · This is a frontend-only demonstration and not a scientific attribution result.</p>
          </div>
        </section>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <div>
              <h3>Investigation report</h3>
              <p className="section-caption">Simulation case summary</p>
            </div>
            <Info size={15} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Records found" value={String(simulationScenario.databaseSummary.simulatedRecordsFound)} />
            <Metric label="Relevant matches" value={String(simulationScenario.databaseSummary.spatiallyRelevant)} />
            <Metric label="Search radius" value={`${simulationScenario.databaseSummary.searchRadiusKm} km`} />
            <Metric label="Mode" value="Simulation Mode" tone="metric--accent" />
          </div>
        </section>
      </aside>
    )
  }

  // 2. Live G1 Investigation Mode
  const rankedCandidates: RankedCandidate[] = rankingResult?.candidates || []
  const selectedRanked = rankedCandidates.find(
    (c) => c.candidate_id === selectedCandidate || c.vessel_id === selectedCandidate
  ) || rankedCandidates[0]

  const selectedExplanation: CandidateExplanation | undefined = explainabilityReport?.candidates.find(
    (e) => e.candidate_id === selectedCandidate || e.vessel_id === selectedCandidate
  ) || explainabilityReport?.candidates[0]

  return (
    <aside className="panel analysis-panel" aria-label="Investigation analysis">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Stage F2 &amp; F3</span>
          <h2>Candidate Ranking &amp; Explainability</h2>
        </div>
        <Compass size={18} className="heading-icon" aria-hidden="true" />
      </div>

      {/* Candidate Ranking List */}
      <section className="panel-section candidates-section">
        <div className="section-title-line">
          <div>
            <h3>Candidate vessels</h3>
            <p className="section-caption">
              {rankedCandidates.length > 0
                ? `${rankedCandidates.length} candidate(s) ranked by physical evidence consistency`
                : 'No candidates ranked'}
            </p>
          </div>
          <Anchor size={15} aria-hidden="true" />
        </div>

        <p className="ranking-notice">
          Ranked by comparative consistency with available physical evidence. Does NOT establish legal responsibility or culpability.
        </p>

        {rankedCandidates.length === 0 ? (
          <div className="empty-results-box">
            <HelpCircle size={20} />
            <p>Candidate ranking not yet available.</p>
            <small>Run the investigation workflow to execute stages B1 through F3.</small>
          </div>
        ) : (
          <div className="candidate-list">
            {rankedCandidates.map((cand) => {
              const isSelected =
                (selectedRanked && (selectedRanked.candidate_id === cand.candidate_id || selectedRanked.vessel_id === cand.vessel_id)) ||
                selectedCandidate === cand.candidate_id ||
                selectedCandidate === cand.vessel_id

              const scoreDisplay =
                cand.evidence_consistency_score !== null
                  ? cand.evidence_consistency_score.toFixed(2)
                  : 'Insufficient data'

              return (
                <button
                  key={cand.candidate_id || cand.vessel_id}
                  className={`candidate-card${isSelected ? ' is-selected' : ''}`}
                  type="button"
                  onClick={() => onSelectCandidate(cand.candidate_id || cand.vessel_id)}
                >
                  <span className="candidate-rank">0{cand.rank}</span>
                  <div className="candidate-details">
                    <div className="candidate-name-row">
                      <strong>{cand.name || cand.vessel_id}</strong>
                      {cand.vessel_type && <span className="vessel-tag">{cand.vessel_type}</span>}
                    </div>
                    <div className="evidence-score-row">
                      <span className="score-label">Evidence Consistency Score:</span>
                      <span className="score-value">{scoreDisplay}</span>
                    </div>
                    <div className="evidence-availability-row">
                      <span>Evidence Availability:</span>
                      <strong>
                        {cand.valid_primary_channels} / {cand.total_primary_channels}
                      </strong>
                    </div>
                    {(cand.mmsi || cand.imo) && (
                      <span className="evidence-placeholder">
                        {cand.mmsi ? `MMSI: ${cand.mmsi}` : ''} {cand.imo ? `· IMO: ${cand.imo}` : ''}
                      </span>
                    )}
                  </div>
                  <span className="candidate-arrow">
                    <ChevronRight size={15} />
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </section>

      {/* Selected Candidate Detailed Explainability */}
      {selectedExplanation && (
        <section className="panel-section candidate-explainability">
          <div className="section-title-line">
            <div>
              <h3>Candidate Evaluation</h3>
              <p className="section-caption">
                Rank #{selectedExplanation.rank} — {selectedExplanation.name || selectedExplanation.vessel_id}
              </p>
            </div>
            <Shield size={16} aria-hidden="true" />
          </div>

          <div className="metric-grid">
            <Metric
              label="Evidence Consistency Score"
              value={
                selectedExplanation.evidence_consistency_score !== null
                  ? selectedExplanation.evidence_consistency_score.toFixed(2)
                  : 'N/A'
              }
              tone="metric--accent"
            />
            <Metric
              label="Consistency Level"
              value={selectedExplanation.evidence_consistency_level}
              tone={
                selectedExplanation.evidence_consistency_level === 'HIGH'
                  ? 'metric--accent'
                  : selectedExplanation.evidence_consistency_level === 'MODERATE'
                    ? 'metric--pending'
                    : 'metric--muted'
              }
            />
            <Metric
              label="Evidence Availability"
              value={`${selectedExplanation.valid_primary_channels} / ${selectedExplanation.total_primary_channels} (${selectedExplanation.evidence_availability_band})`}
            />
          </div>

          {/* Primary Evidence Explanations */}
          <div className="explanation-block">
            <h4>Primary Evidence Channels</h4>
            <div className="channel-row">
              <div className="channel-heading">
                <span>Spatial Proximity:</span>
                <strong>
                  {selectedExplanation.spatial_score !== null
                    ? selectedExplanation.spatial_score.toFixed(2)
                    : 'Insufficient data'}
                </strong>
              </div>
              <p className="channel-desc">{selectedExplanation.spatial_explanation}</p>
            </div>

            <div className="channel-row">
              <div className="channel-heading">
                <span>Temporal Proximity:</span>
                <strong>
                  {selectedExplanation.temporal_score !== null
                    ? selectedExplanation.temporal_score.toFixed(2)
                    : 'Insufficient data'}
                </strong>
              </div>
              <p className="channel-desc">{selectedExplanation.temporal_explanation}</p>
            </div>

            <div className="channel-row">
              <div className="channel-heading">
                <span>Trajectory Consistency:</span>
                <strong>
                  {selectedExplanation.trajectory_score !== null
                    ? selectedExplanation.trajectory_score.toFixed(2)
                    : 'Insufficient data'}
                </strong>
              </div>
              <p className="channel-desc">{selectedExplanation.trajectory_explanation}</p>
            </div>
          </div>

          {/* Contextual Behavioral Intelligence (strictly 0.00 contribution) */}
          <div className="explanation-block contextual-block">
            <h4>Contextual behavioral observations</h4>
            <p className="non-contribution-note">
              {selectedExplanation.behavioral_context.numerical_contribution_statement}
            </p>
            {selectedExplanation.behavioral_context.observations.length > 0 ? (
              <ul className="observations-list">
                {selectedExplanation.behavioral_context.observations.map((obs, idx) => (
                  <li key={idx}>• {obs}</li>
                ))}
              </ul>
            ) : (
              <p className="no-anomalies-note">No anomalous behavioral patterns observed.</p>
            )}
          </div>

          {/* Forward Drift Cross-Check (strictly non-additive) */}
          <div className="explanation-block crosscheck-block">
            <div className="crosscheck-header">
              <h4>Forward drift cross-check</h4>
              <span className={`crosscheck-badge crosscheck-badge--${selectedExplanation.drift_cross_check.status.toLowerCase()}`}>
                {selectedExplanation.drift_cross_check.status}
              </span>
            </div>
            <p className="channel-desc">{selectedExplanation.drift_cross_check.explanation}</p>
            <small className="non-contribution-note">
              {selectedExplanation.drift_cross_check.non_additive_statement}
            </small>
          </div>

          {/* Limitations */}
          {selectedExplanation.limitations.length > 0 && (
            <div className="explanation-block limitations-block">
              <div className="section-title-line">
                <h4>Observational &amp; Scientific Limitations</h4>
                <Info size={13} />
              </div>
              <ul className="limitations-list">
                {selectedExplanation.limitations.map((limit, idx) => (
                  <li key={idx}>{limit}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Mandatory Scientific Disclaimer */}
          <div className="scientific-disclaimer">
            <Shield size={14} className="disclaimer-icon" />
            <p>{selectedExplanation.scientific_disclaimer}</p>
          </div>
        </section>
      )}
    </aside>
  )
}
