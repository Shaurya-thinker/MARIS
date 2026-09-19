import { useState } from 'react'
import { AlertCircle, Loader2, X } from 'lucide-react'
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
}

export function CreateInvestigationModal({ isOpen, onClose, onSubmit }: CreateInvestigationModalProps) {
  const [mode, setMode] = useState<'live' | 'simulation'>('live')
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

  if (!isOpen) return null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setFormError(null)

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
        created_via: 'MARIS G2 Frontend',
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

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div className="modal-card">
        <div className="modal-header">
          <div>
            <span className="section-kicker">New Investigation</span>
            <h2 id="modal-title">{mode === 'simulation' ? 'Initialize Simulation Investigation' : 'Initialize Spill Investigation'}</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close modal">
            <X size={18} />
          </button>
        </div>

        {formError && (
          <div className="error-callout" role="alert">
            <AlertCircle size={16} />
            <span>{formError}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-group">
            <label>Investigation Type</label>
            <div className="segmented-control" role="tablist" aria-label="Investigation type selector">
              <button type="button" className={mode === 'live' ? 'is-selected' : ''} onClick={() => setMode('live')}>
                Live / Real
              </button>
              <button type="button" className={mode === 'simulation' ? 'is-selected' : ''} onClick={() => setMode('simulation')}>
                Simulation
              </button>
            </div>
          </div>

          {mode === 'simulation' ? (
            <>
              <div className="form-group">
                <label htmlFor="sim-name">Investigation Name *</label>
                <input
                  id="sim-name"
                  type="text"
                  required
                  value={simulationName}
                  onChange={(e) => setSimulationName(e.target.value)}
                  placeholder="Arabian Sea Spill Demo"
                />
              </div>

              <div className="form-group">
                <label htmlFor="sim-region">Region *</label>
                <input
                  id="sim-region"
                  type="text"
                  required
                  value={simulationRegion}
                  onChange={(e) => setSimulationRegion(e.target.value)}
                  placeholder="Arabian Sea"
                />
              </div>

              <div className="form-group">
                <label htmlFor="sim-date">Date / Time (UTC) *</label>
                <input
                  id="sim-date"
                  type="text"
                  required
                  value={simulationDate}
                  onChange={(e) => setSimulationDate(e.target.value)}
                  placeholder="2025-03-15T05:42:00Z"
                />
              </div>

              <div className="form-group">
                <label htmlFor="sim-scenario">Simulation Scenario *</label>
                <select
                  id="sim-scenario"
                  value={simulationScenarioId}
                  onChange={(e) => setSimulationScenarioId(e.target.value)}
                >
                  {simulationScenarios.map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>
                      {scenario.name}
                    </option>
                  ))}
                </select>
              </div>
            </>
          ) : (
            <>
              <div className="form-group">
                <label htmlFor="inv-name">Investigation Title *</label>
                <input
                  id="inv-name"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Gulf of Mexico Slick 2025"
                />
              </div>

              <div className="form-group">
                <label htmlFor="inv-desc">Description (Optional)</label>
                <textarea
                  id="inv-desc"
                  rows={2}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Operational context, notes, or satellite scene reference"
                />
              </div>

              <fieldset className="form-fieldset">
                <legend>Area of Interest (Bounding Box)</legend>
                <div className="grid-2x2">
                  <div className="form-group">
                    <label htmlFor="bbox-west">West Longitude</label>
                    <input
                      id="bbox-west"
                      type="number"
                      step="0.0001"
                      required
                      value={west}
                      onChange={(e) => setWest(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="bbox-south">South Latitude</label>
                    <input
                      id="bbox-south"
                      type="number"
                      step="0.0001"
                      required
                      value={south}
                      onChange={(e) => setSouth(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="bbox-east">East Longitude</label>
                    <input
                      id="bbox-east"
                      type="number"
                      step="0.0001"
                      required
                      value={east}
                      onChange={(e) => setEast(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="bbox-north">North Latitude</label>
                    <input
                      id="bbox-north"
                      type="number"
                      step="0.0001"
                      required
                      value={north}
                      onChange={(e) => setNorth(parseFloat(e.target.value))}
                    />
                  </div>
                </div>
              </fieldset>

              <fieldset className="form-fieldset">
                <legend>Temporal Window (UTC)</legend>
                <div className="grid-1x2">
                  <div className="form-group">
                    <label htmlFor="time-start">Start Time</label>
                    <input
                      id="time-start"
                      type="text"
                      required
                      value={startDate}
                      onChange={(e) => setStartDate(e.target.value)}
                      placeholder="2025-06-01T00:00:00Z"
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="time-end">End Time</label>
                    <input
                      id="time-end"
                      type="text"
                      required
                      value={endDate}
                      onChange={(e) => setEndDate(e.target.value)}
                      placeholder="2025-06-02T00:00:00Z"
                    />
                  </div>
                </div>
              </fieldset>
            </>
          )}

          <div className="modal-actions">
            <button className="secondary-button" type="button" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </button>
            <button className="primary-button" type="submit" disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 size={15} className="spinner" /> Creating investigation...
                </>
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
