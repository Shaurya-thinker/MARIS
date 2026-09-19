import { useEffect, useState } from 'react'
import { AlertCircle, Compass, Globe, Loader2, Radio, Waves, X } from 'lucide-react'
import { getSimulationScenarioById, simulationScenarios } from '../../simulation/simulationEngine'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import type { InvestigationCreateRequest } from '../../types/investigationApi'

type CreateInvestigationPayload = InvestigationCreateRequest | {
  mode: 'SIMULATION'
  scenarioId: string
  name: string
  region: string
  timestamp: string
  scenario: SimulationScenario
}

interface CreateInvestigationModalProps {
  isOpen: boolean
  onClose: () => void
  onSubmit: (payload: CreateInvestigationPayload) => Promise<void>
  onNavigateToEvaluator?: () => void
}

export function CreateInvestigationModal({ isOpen, onClose, onSubmit, onNavigateToEvaluator }: CreateInvestigationModalProps) {
  const [mode, setMode] = useState<'live' | 'simulation' | 'evaluator'>('live')
  const [name, setName] = useState('Gulf of Mexico Spill Case 101')
  const [description, setDescription] = useState('Operational investigation for observed SAR anomaly')
  const [west, setWest] = useState(-90.5)
  const [south, setSouth] = useState(28.0)
  const [east, setEast] = useState(-89.5)
  const [north, setNorth] = useState(29.0)
  const [startDate, setStartDate] = useState('2025-06-01T00:00:00Z')
  const [endDate, setEndDate] = useState('2025-06-02T00:00:00Z')
  const [simulationName, setSimulationName] = useState('Arabian Sea Spill Demo')
  const [simulationRegion, setSimulationRegion] = useState('Arabian Sea')
  const [simulationDate, setSimulationDate] = useState('2025-03-15T05:42:00Z')
  const [simulationScenarioId, setSimulationScenarioId] = useState(simulationScenarios[0]?.id ?? 'alpha')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  // Lock body scroll while modal is active
  useEffect(() => {
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      document.body.style.overflow = originalOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [onClose])

  if (!isOpen) return null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setFormError(null)

    if (mode === 'evaluator') {
      onClose()
      if (onNavigateToEvaluator) {
        onNavigateToEvaluator()
      } else if (typeof window !== 'undefined') {
        window.location.hash = '#evaluator'
        window.dispatchEvent(new HashChangeEvent('hashchange'))
      }
      return
    }

    if (mode === 'simulation') {
      const scenario = getSimulationScenarioById(simulationScenarioId)
      if (!scenario) {
        setFormError('Select a valid simulation scenario.')
        return
      }

      const payload: CreateInvestigationPayload = {
        mode: 'SIMULATION',
        scenarioId: scenario.id,
        name: simulationName.trim() || `${scenario.name} Demo`,
        region: simulationRegion.trim() || scenario.region,
        timestamp: simulationDate.trim() || scenario.timestamp,
        scenario,
      }

      try {
        setIsSubmitting(true)
        await onSubmit(payload)
        onClose()
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        setFormError(msg)
      } finally {
        setIsSubmitting(false)
      }
      return
    }

    if (!name.trim()) {
      setFormError('Investigation name is required.')
      return
    }

    if (isNaN(west) || isNaN(south) || isNaN(east) || isNaN(north)) {
      setFormError('All bounding box coordinates must be valid numbers.')
      return
    }

    if (west >= east || south >= north) {
      setFormError('Invalid bounding box: West must be < East and South must be < North.')
      return
    }

    const payload: InvestigationCreateRequest = {
      name: name.trim(),
      description: description.trim() || undefined,
      area_of_interest: {
        kind: 'bbox',
        bbox: { west, south, east, north },
      },
      time_window: {
        start: startDate,
        end: endDate,
      },
      metadata: {
        created_via: 'MARIS Mission Control Frontend',
      },
    }

    try {
      setIsSubmitting(true)
      await onSubmit(payload)
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setFormError(msg)
    } finally {
      setIsSubmitting(false)
    }
  }

  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget) {
      onClose()
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      onClick={handleBackdropClick}
    >
      <div className="modal-card">
        {/* Header */}
        <div className="modal-header">
          <div>
            <span className="section-kicker">Mission Initialization</span>
            <h2 id="modal-title" className="modal-title">
              {mode === 'simulation' ? 'Initialize Simulation Scenario' : 'Initialize Spill Investigation'}
            </h2>
            <p className="modal-subtitle">
              Define the investigation type, area of interest, and temporal search window.
            </p>
          </div>
          <button
            className="icon-button modal-close-btn"
            type="button"
            onClick={onClose}
            aria-label="Close modal"
          >
            <X size={16} />
          </button>
        </div>

        {formError && (
          <div className="error-callout" role="alert">
            <AlertCircle size={16} />
            <span>{formError}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="modal-form">
          {/* Section 1: CASE TYPE */}
          <div className="modal-section">
            <span className="modal-section-title">Case Type</span>
            <div className="segmented-control" role="tablist" aria-label="Investigation type selector">
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'live'}
                className={`segmented-btn ${mode === 'live' ? 'is-selected' : ''}`}
                onClick={() => setMode('live')}
              >
                Operational Live Case
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'evaluator'}
                className={`segmented-btn ${mode === 'evaluator' ? 'is-selected' : ''}`}
                onClick={() => setMode('evaluator')}
              >
                🎯 Evaluator Case (6-Step)
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'simulation'}
                className={`segmented-btn ${mode === 'simulation' ? 'is-selected' : ''}`}
                onClick={() => setMode('simulation')}
              >
                Synthetic Simulation Showcase
              </button>
            </div>
          </div>

          {mode === 'evaluator' ? (
            <div className="modal-section" style={{ padding: '1.25rem', background: 'rgba(56, 189, 248, 0.08)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(56, 189, 248, 0.25)', marginTop: '0.75rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                <span className="re-badge" style={{ fontSize: '0.7rem', padding: '0.2rem 0.5rem' }}>EVALUATOR WORKFLOW</span>
                <span style={{ fontWeight: 600, color: '#fff', fontSize: '0.95rem' }}>Interactive 6-Step SAR Attribution Pipeline</span>
              </div>
              <p style={{ fontSize: '0.82rem', color: 'var(--color-text-subtle)', lineHeight: 1.5, margin: '0 0 1rem 0' }}>
                Select a verified Sentinel-1 reference SAR acquisition (including historical Corsica demo semantics), adjust wind &amp; ocean-current vectors for D3 backward drift physics, configure strict spatial corridor &amp; temporal AIS candidate filtering, and run ML attribution using the active scaled model (<code>attr_lr_scaled_10k_20260919_183349</code>).
              </p>
              <button
                type="button"
                className="primary-button"
                style={{ width: '100%', justifyContent: 'center', padding: '0.65rem 1rem' }}
                onClick={() => {
                  onClose()
                  if (onNavigateToEvaluator) {
                    onNavigateToEvaluator()
                  } else if (typeof window !== 'undefined') {
                    window.location.hash = '#evaluator'
                    window.dispatchEvent(new HashChangeEvent('hashchange'))
                  }
                }}
              >
                Launch Evaluator Investigation Workflow →
              </button>
            </div>
          ) : mode === 'simulation' ? (
            /* Simulation mode sections */
            <div className="modal-section">
              <span className="modal-section-title">Simulation Specification</span>
              <div className="form-group">
                <label htmlFor="sim-scenario" className="form-label">
                  Scenario Archetype <span className="required-mark">*</span>
                </label>
                <select
                  id="sim-scenario"
                  className="form-select"
                  value={simulationScenarioId}
                  onChange={(e) => {
                    const newId = e.target.value
                    setSimulationScenarioId(newId)
                    const sc = getSimulationScenarioById(newId)
                    if (sc) {
                      setSimulationName(sc.name)
                      setSimulationRegion(sc.region)
                      setSimulationDate(sc.timestamp)
                    }
                  }}
                >
                  {simulationScenarios.map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>
                      {scenario.name} ({scenario.region})
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-grid-2">
                <div className="form-group">
                  <label htmlFor="sim-name" className="form-label">
                    Case Name <span className="required-mark">*</span>
                  </label>
                  <input
                    id="sim-name"
                    type="text"
                    className="form-input"
                    required
                    value={simulationName}
                    onChange={(e) => setSimulationName(e.target.value)}
                    placeholder="Arabian Sea Spill Demo"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="sim-region" className="form-label">
                    Region <span className="required-mark">*</span>
                  </label>
                  <input
                    id="sim-region"
                    type="text"
                    className="form-input"
                    required
                    value={simulationRegion}
                    onChange={(e) => setSimulationRegion(e.target.value)}
                    placeholder="Arabian Sea"
                  />
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="sim-date" className="form-label">
                  Observation Date / Time (UTC) <span className="required-mark">*</span>
                </label>
                <input
                  id="sim-date"
                  type="text"
                  className="form-input mono-num"
                  required
                  value={simulationDate}
                  onChange={(e) => setSimulationDate(e.target.value)}
                  placeholder="2025-03-15T05:42:00Z"
                />
              </div>
            </div>
          ) : (
            /* Live mode sections */
            <>
              {/* Section 2: INVESTIGATION */}
              <div className="modal-section">
                <span className="modal-section-title">Investigation</span>
                <div className="form-group">
                  <label htmlFor="inv-name" className="form-label">
                    Investigation Title <span className="required-mark">*</span>
                  </label>
                  <input
                    id="inv-name"
                    type="text"
                    className="form-input"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Gulf of Mexico Spill Case 101"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="inv-desc" className="form-label">
                    Description <span className="label-optional">(Optional)</span>
                  </label>
                  <textarea
                    id="inv-desc"
                    rows={2}
                    className="form-textarea"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Operational context, notes, or satellite scene reference"
                  />
                </div>
              </div>

              {/* Section 3: AREA OF INTEREST */}
              <div className="modal-section">
                <span className="modal-section-title">Area of Interest</span>
                <div className="form-grid-2">
                  <div className="form-group">
                    <label htmlFor="bbox-west" className="form-label">
                      West Longitude (°)
                    </label>
                    <input
                      id="bbox-west"
                      type="number"
                      step="0.0001"
                      className="form-input mono-num"
                      required
                      value={west}
                      onChange={(e) => setWest(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="bbox-south" className="form-label">
                      South Latitude (°)
                    </label>
                    <input
                      id="bbox-south"
                      type="number"
                      step="0.0001"
                      className="form-input mono-num"
                      required
                      value={south}
                      onChange={(e) => setSouth(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="bbox-east" className="form-label">
                      East Longitude (°)
                    </label>
                    <input
                      id="bbox-east"
                      type="number"
                      step="0.0001"
                      className="form-input mono-num"
                      required
                      value={east}
                      onChange={(e) => setEast(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="bbox-north" className="form-label">
                      North Latitude (°)
                    </label>
                    <input
                      id="bbox-north"
                      type="number"
                      step="0.0001"
                      className="form-input mono-num"
                      required
                      value={north}
                      onChange={(e) => setNorth(parseFloat(e.target.value))}
                    />
                  </div>
                </div>
              </div>

              {/* Section 4: TEMPORAL SEARCH WINDOW */}
              <div className="modal-section">
                <span className="modal-section-title">Temporal Search Window</span>
                <div className="form-grid-2">
                  <div className="form-group">
                    <label htmlFor="time-start" className="form-label">
                      Start Timestamp (UTC)
                    </label>
                    <input
                      id="time-start"
                      type="text"
                      className="form-input mono-num"
                      required
                      value={startDate}
                      onChange={(e) => setStartDate(e.target.value)}
                      placeholder="2025-06-01T00:00:00Z"
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="time-end" className="form-label">
                      End Timestamp (UTC)
                    </label>
                    <input
                      id="time-end"
                      type="text"
                      className="form-input mono-num"
                      required
                      value={endDate}
                      onChange={(e) => setEndDate(e.target.value)}
                      placeholder="2025-06-02T00:00:00Z"
                    />
                  </div>
                </div>
              </div>
            </>
          )}

          {/* Footer Actions */}
          <div className="modal-actions">
            <button
              className="modal-btn-secondary"
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button
              className="modal-btn-primary"
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={15} className="spinner" /> Creating Case...
                </>
              ) : mode === 'evaluator' ? (
                'Open Evaluator Workflow →'
              ) : mode === 'simulation' ? (
                'Create Simulation Investigation'
              ) : (
                'Create Investigation'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
