/**
 * SyntheticExperimentSection — Interactive Synthetic Data Generator & ML Attribution view.
 *
 * Allows operators to:
 * 1. Generate physically consistent synthetic scenarios (Sentinel-1 SAR, ERA5 wind, CMEMS currents, AIS vessels).
 * 2. Override physical parameters (wind speed/dir, current speed/dir, backtrack hours, seed).
 * 3. Inspect active ML attribution model metrics, test accuracy, and feature coefficients.
 * 4. Execute real backward drift physics and evaluate candidate attribution probabilities.
 * 5. Display independent binary model probabilities and explicitly labeled scenario-normalized scores.
 */

import React, { useState, useEffect } from 'react'
import {
  generateSyntheticScenario,
  runSyntheticExperiment,
  fetchActiveModelInfo,
  trainAttributionModel,
} from '../../real-experiment/experimentApi'
import type {
  SyntheticScenario,
  SyntheticRunResult,
  ActiveModelInfo,
} from '../../real-experiment/experimentTypes'

function fmt(n: number | null | undefined, dec = 2): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toFixed(dec)
}

export default function SyntheticExperimentSection() {
  // Scenario configuration state
  const [seed, setSeed] = useState<number>(() => Math.floor(100000 + Math.random() * 900000))
  const [originLat, setOriginLat] = useState<string>('43.25')
  const [originLon, setOriginLon] = useState<string>('9.45')
  const [windSpeed, setWindSpeed] = useState<string>('6.5')
  const [windDir, setWindDir] = useState<string>('225')
  const [currentSpeed, setCurrentSpeed] = useState<string>('0.22')
  const [currentDir, setCurrentDir] = useState<string>('240')
  const [candidateCount, setCandidateCount] = useState<number>(4)
  const [backtrackHours, setBacktrackHours] = useState<number>(6)

  // Execution state
  const [scenario, setScenario] = useState<SyntheticScenario | null>(null)
  const [runResult, setRunResult] = useState<SyntheticRunResult | null>(null)
  const [activeModel, setActiveModel] = useState<ActiveModelInfo | null>(null)
  const [loadingScenario, setLoadingScenario] = useState<boolean>(false)
  const [runningExperiment, setRunningExperiment] = useState<boolean>(false)
  const [trainingModel, setTrainingModel] = useState<boolean>(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [trainScenarios, setTrainScenarios] = useState<number>(30)

  // Load model metadata on mount
  useEffect(() => {
    loadModelMetadata()
  }, [])

  async function loadModelMetadata() {
    try {
      const info = await fetchActiveModelInfo()
      setActiveModel(info)
    } catch (err) {
      console.warn('Could not load initial model info:', err)
    }
  }

  function handleRandomizeSeed() {
    const nextSeed = Math.floor(100000 + Math.random() * 900000)
    setSeed(nextSeed)
  }

  async function handleGenerateScenario() {
    setLoadingScenario(true)
    setErrorMsg(null)
    setRunResult(null)
    try {
      const sc = await generateSyntheticScenario({
        seed,
        origin_lat: parseFloat(originLat) || undefined,
        origin_lon: parseFloat(originLon) || undefined,
        wind_speed_ms: parseFloat(windSpeed) || undefined,
        wind_direction_deg: parseFloat(windDir) || undefined,
        current_speed_ms: parseFloat(currentSpeed) || undefined,
        current_direction_deg: parseFloat(currentDir) || undefined,
        candidate_count: candidateCount,
        backtrack_hours: backtrackHours,
      })
      setScenario(sc)
      // Synchronize form with generated values
      setOriginLat(sc.origin_lat.toFixed(4))
      setOriginLon(sc.origin_lon.toFixed(4))
      setWindSpeed(sc.wind_speed_ms.toFixed(1))
      setWindDir(sc.wind_direction_deg.toFixed(0))
      setCurrentSpeed(sc.current_speed_ms.toFixed(2))
      setCurrentDir(sc.current_direction_deg.toFixed(0))
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to generate scenario')
    } finally {
      setLoadingScenario(false)
    }
  }

  async function handleRunExperiment() {
    setRunningExperiment(true)
    setErrorMsg(null)
    try {
      const res = await runSyntheticExperiment({
        seed,
        origin_lat: parseFloat(originLat) || undefined,
        origin_lon: parseFloat(originLon) || undefined,
        wind_speed_ms: parseFloat(windSpeed) || undefined,
        wind_direction_deg: parseFloat(windDir) || undefined,
        current_speed_ms: parseFloat(currentSpeed) || undefined,
        current_direction_deg: parseFloat(currentDir) || undefined,
        candidate_count: candidateCount,
        backtrack_hours: backtrackHours,
      })
      setRunResult(res)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to execute synthetic experiment')
    } finally {
      setRunningExperiment(false)
    }
  }

  async function handleTrainModel() {
    setTrainingModel(true)
    setErrorMsg(null)
    try {
      await trainAttributionModel({
        num_scenarios: trainScenarios,
        base_seed: Math.floor(Math.random() * 10000),
      })
      await loadModelMetadata()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to train attribution model')
    } finally {
      setTrainingModel(false)
    }
  }

  return (
    <div className="syn-experiment-root">
      {/* Top Banner */}
      <div className="syn-banner">
        <div className="syn-banner-badge">SYNTHETIC EXPERIMENT & ML PIPELINE</div>
        <h3>Scenario Simulation & Vessel Attribution Pipeline</h3>
        <p>
          Generate calibrated hydrodynamic oil spill scenarios to evaluate attribution performance.
          Independent binary probabilities and scenario-normalized scores are computed using
          trained Logistic Regression.
        </p>
      </div>

      {errorMsg && (
        <div className="syn-error-alert">
          <span className="syn-error-icon">✕</span>
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Grid: Left Parameter Controls / Right ML Model Diagnostics */}
      <div className="syn-grid-layout">
        {/* Left: Parameter Controls */}
        <div className="syn-card">
          <div className="syn-card-header">
            <h4>1. Scenario & Environmental Controls</h4>
            <button
              type="button"
              className="syn-btn-tiny"
              onClick={handleRandomizeSeed}
              title="Randomize scenario seed"
            >
              Random Seed
            </button>
          </div>

          <div className="syn-form-grid">
            <div className="syn-field">
              <label htmlFor="syn-seed-input">Scenario Seed</label>
              <input
                id="syn-seed-input"
                type="number"
                value={seed}
                onChange={e => setSeed(parseInt(e.target.value, 10) || 0)}
              />
            </div>

            <div className="syn-field">
              <label htmlFor="syn-vessels-count">Candidate Count</label>
              <select
                id="syn-vessels-count"
                value={candidateCount}
                onChange={e => setCandidateCount(parseInt(e.target.value, 10))}
              >
                <option value={3}>3 vessels (1 incident vessel + 2 traffic vessels)</option>
                <option value={4}>4 vessels (1 incident vessel + 3 traffic vessels)</option>
                <option value={5}>5 vessels (1 incident vessel + 4 traffic vessels)</option>
                <option value={6}>6 vessels (1 incident vessel + 5 traffic vessels)</option>
              </select>
            </div>

            <div className="syn-field">
              <label htmlFor="syn-lat-input">Origin Latitude (°N)</label>
              <input
                id="syn-lat-input"
                type="text"
                value={originLat}
                onChange={e => setOriginLat(e.target.value)}
              />
            </div>

            <div className="syn-field">
              <label htmlFor="syn-lon-input">Origin Longitude (°E)</label>
              <input
                id="syn-lon-input"
                type="text"
                value={originLon}
                onChange={e => setOriginLon(e.target.value)}
              />
            </div>

            <div className="syn-field">
              <label htmlFor="syn-wind-spd">Wind Speed (m/s)</label>
              <input
                id="syn-wind-spd"
                type="text"
                value={windSpeed}
                onChange={e => setWindSpeed(e.target.value)}
              />
            </div>

            <div className="syn-field">
              <label htmlFor="syn-wind-dir">Wind Direction (°)</label>
              <input
                id="syn-wind-dir"
                type="text"
                value={windDir}
                onChange={e => setWindDir(e.target.value)}
              />
            </div>

            <div className="syn-field">
              <label htmlFor="syn-curr-spd">Current Speed (m/s)</label>
              <input
                id="syn-curr-spd"
                type="text"
                value={currentSpeed}
                onChange={e => setCurrentSpeed(e.target.value)}
              />
            </div>

            <div className="syn-field">
              <label htmlFor="syn-curr-dir">Current Direction (°)</label>
              <input
                id="syn-curr-dir"
                type="text"
                value={currentDir}
                onChange={e => setCurrentDir(e.target.value)}
              />
            </div>

            <div className="syn-field">
              <label htmlFor="syn-backtrack">Backtrack Window (hours)</label>
              <input
                id="syn-backtrack"
                type="number"
                min={1}
                max={24}
                value={backtrackHours}
                onChange={e => setBacktrackHours(parseInt(e.target.value, 10) || 6)}
              />
            </div>
          </div>

          <div className="syn-actions-row">
            <button
              type="button"
              className="syn-btn-secondary"
              onClick={handleGenerateScenario}
              disabled={loadingScenario || runningExperiment}
            >
              {loadingScenario ? 'Generating…' : 'Generate Scenario'}
            </button>
            <button
              type="button"
              className="syn-btn-primary"
              onClick={handleRunExperiment}
              disabled={runningExperiment}
            >
              {runningExperiment ? 'Computing Drift & Attribution…' : 'Run Drift & ML Attribution'}
            </button>
          </div>
        </div>

        {/* Right: ML Attribution Model Diagnostics */}
        <div className="syn-card">
          <div className="syn-card-header">
            <h4>2. Trained ML Attribution Model</h4>
            <span className="syn-tag-active">Active Model</span>
          </div>

          {activeModel ? (
            <div className="syn-model-details">
              <div className="syn-model-meta-row">
                <div>
                  <span className="syn-meta-label">Model Architecture:</span>{' '}
                  <span className="syn-meta-val">StandardScaler + LogisticRegression</span>
                </div>
                <div>
                  <span className="syn-meta-label">Model ID:</span>{' '}
                  <span className="syn-meta-mono">{activeModel.model_id}</span>
                </div>
              </div>

              {/* Performance Metrics Table */}
              <div className="syn-metrics-grid">
                <div className="syn-metric-card">
                  <span className="syn-metric-val">
                    {activeModel.test_metrics?.scenario_top1_accuracy != null
                      ? `${(Number(activeModel.test_metrics.scenario_top1_accuracy) * 100).toFixed(1)}%`
                      : '—'}
                  </span>
                  <span className="syn-metric-lbl">Scenario Top-1 Accuracy</span>
                </div>
                <div className="syn-metric-card">
                  <span className="syn-metric-val">
                    {activeModel.test_metrics?.candidate_roc_auc != null
                      ? Number(activeModel.test_metrics.candidate_roc_auc).toFixed(3)
                      : '—'}
                  </span>
                  <span className="syn-metric-lbl">Test ROC-AUC</span>
                </div>
                <div className="syn-metric-card">
                  <span className="syn-metric-val">
                    {activeModel.test_metrics?.candidate_f1 != null
                      ? Number(activeModel.test_metrics.candidate_f1).toFixed(3)
                      : '—'}
                  </span>
                  <span className="syn-metric-lbl">Candidate F1-Score</span>
                </div>
                <div className="syn-metric-card">
                  <span className="syn-metric-val">
                    {activeModel.training_scenario_count ?? 0}
                  </span>
                  <span className="syn-metric-lbl">Training Scenarios</span>
                </div>
              </div>

              {/* Feature Coefficients */}
              <div className="syn-coeffs-section">
                <div className="syn-subheading">Feature Coefficients</div>
                <div className="syn-coeffs-list">
                  {Object.entries(activeModel.feature_coefficients || {}).map(([fname, coef]) => {
                    const isPositive = coef >= 0
                    return (
                      <div key={fname} className="syn-coeff-row">
                        <span className="syn-coeff-name" title={fname}>{fname}</span>
                        <div className="syn-coeff-bar-container">
                          <div
                            className={`syn-coeff-bar ${isPositive ? 'pos' : 'neg'}`}
                            style={{
                              width: `${Math.min(100, Math.abs(coef) * 35)}%`,
                            }}
                          />
                        </div>
                        <span className={`syn-coeff-val ${isPositive ? 'pos' : 'neg'}`}>
                          {coef > 0 ? `+${coef.toFixed(3)}` : coef.toFixed(3)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Retrain Trigger */}
              <div className="syn-retrain-row">
                <label htmlFor="syn-train-count">Retrain on scenarios:</label>
                <select
                  id="syn-train-count"
                  value={trainScenarios}
                  onChange={e => setTrainScenarios(parseInt(e.target.value, 10))}
                  disabled={trainingModel}
                >
                  <option value={20}>20 scenarios</option>
                  <option value={35}>35 scenarios</option>
                  <option value={50}>50 scenarios</option>
                </select>
                <button
                  type="button"
                  className="syn-btn-tiny"
                  onClick={handleTrainModel}
                  disabled={trainingModel}
                >
                  {trainingModel ? 'Training…' : 'Retrain Model'}
                </button>
              </div>
            </div>
          ) : (
            <div className="syn-empty-box">Loading model diagnostics…</div>
          )}
        </div>
      </div>

      {/* Generated Scenario Info (if created prior to run) */}
      {scenario && !runResult && (
        <div className="syn-scenario-preview syn-card">
          <div className="syn-card-header">
            <h4>Generated Unseen Scenario Preview</h4>
            <span className="syn-tag-mono">{scenario.scenario_id}</span>
          </div>
          <p className="syn-scenario-desc">
            Slick origin: <strong>{scenario.origin_lat.toFixed(4)}°N, {scenario.origin_lon.toFixed(4)}°E</strong> ·{' '}
            Drift Leeway: <strong>{scenario.wind_speed_ms.toFixed(1)} m/s</strong> at {scenario.wind_direction_deg.toFixed(0)}° ·{' '}
            Current: <strong>{scenario.current_speed_ms.toFixed(2)} m/s</strong> ·{' '}
            Hidden Ground Truth: <strong>{scenario.ground_truth_vessel_id}</strong>
          </p>
          <div className="syn-vessel-previews">
            {scenario.vessels.map((v: any) => (
              <div key={v.id || v.mmsi} className={`syn-vessel-chip ${v.is_ground_truth ? 'gt' : ''}`}>
                <span className="syn-vessel-name">{v.vessel_name}</span>
                <span className="syn-vessel-mmsi">MMSI: {v.mmsi}</span>
                {v.is_ground_truth && <span className="syn-gt-tag">Ground Truth</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Live Experiment Results & Attribution Ranking */}
      {runResult && (
        <div className="syn-results-card syn-card">
          <div className="syn-card-header">
            <div className="syn-header-status-group">
              <h4>Attribution Results & ML Evaluation</h4>
              {runResult.attribution_match ? (
                <span className="syn-badge-match">✓ Correct Ground-Truth Match</span>
              ) : (
                <span className="syn-badge-mismatch">Alternative Candidate Attributed</span>
              )}
            </div>
            <span className="syn-tag-mono">Run: {runResult.run_id}</span>
          </div>

          <div className="syn-summary-stats-bar">
            <div>
              <span className="syn-stat-label">Source Zone:</span>{' '}
              <span className="syn-stat-val">
                {fmt(runResult.source_lat, 4)}°N, {fmt(runResult.source_lon, 4)}°E (±{fmt(runResult.source_radius_m, 0)} m)
              </span>
            </div>
            <div>
              <span className="syn-stat-label">Physics Drift Steps:</span>{' '}
              <span className="syn-stat-val">{runResult.backward_steps?.length ?? 0}</span>
            </div>
            <div>
              <span className="syn-stat-label">Ground Truth ID:</span>{' '}
              <span className="syn-stat-mono">{runResult.ground_truth_vessel_id}</span>
            </div>
            <div>
              <span className="syn-stat-label">Top Ranked Candidate:</span>{' '}
              <span className="syn-stat-mono">{runResult.top_candidate_id || '—'}</span>
            </div>
          </div>

          {/* Scored Candidate Vessels Table */}
          <div className="syn-table-wrapper">
            <table className="syn-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Candidate Vessel</th>
                  <th>MMSI / Type</th>
                  <th>Ground Truth?</th>
                  <th title="Independent binary probability P(responsible | features) from predict_proba(). Does not sum to 1.">
                    Model Probability
                  </th>
                  <th title="Explicitly normalized scenario attribution score scaled across candidates.">
                    Scenario Attribution Score
                  </th>
                  <th>Min Source Dist</th>
                  <th>Track Overlap</th>
                  <th>Heading Consistency</th>
                  <th>Speed Consistency</th>
                </tr>
              </thead>
              <tbody>
                {runResult.vessels.map(cand => {
                  const isGT = cand.is_ground_truth
                  const isTop = cand.rank === 1
                  const probPct = (cand.model_probability * 100).toFixed(1)
                  const scorePct = (cand.scenario_normalized_attribution_score * 100).toFixed(1)

                  return (
                    <tr
                      key={cand.vessel_id}
                      className={`syn-tr ${isGT ? 'syn-gt-row' : ''} ${isTop ? 'syn-top-row' : ''}`}
                    >
                      <td className="syn-rank-col">
                        <span className={`syn-rank-badge ${isTop ? 'gold' : ''}`}>#{cand.rank}</span>
                      </td>
                      <td>
                        <strong>{cand.vessel_name}</strong>
                        {isTop && <span className="syn-top-badge">Top Ranked</span>}
                      </td>
                      <td className="syn-td-sub">
                        {cand.mmsi || cand.vessel_id} <span className="syn-type-hint">({cand.vessel_type})</span>
                      </td>
                      <td>
                        {isGT ? (
                          <span className="syn-gt-badge">YES (Actual Spiller)</span>
                        ) : (
                          <span className="syn-distractor-badge">No (Traffic Vessel)</span>
                        )}
                      </td>
                      <td>
                        <div className="syn-prob-cell">
                          <div className="syn-prob-bar-track">
                            <div
                              className="syn-prob-bar-fill"
                              style={{ width: `${Math.min(100, cand.model_probability * 100)}%` }}
                            />
                          </div>
                          <span className="syn-prob-val">{probPct}%</span>
                        </div>
                      </td>
                      <td>
                        <div className="syn-score-cell">
                          <span className="syn-score-val">{scorePct}%</span>
                          <span className="syn-score-caption">scenario-normalized</span>
                        </div>
                      </td>
                      <td className="syn-mono-td">{fmt(cand.features?.min_source_distance_km, 2)} km</td>
                      <td className="syn-mono-td">{(cand.features?.trajectory_overlap_fraction * 100).toFixed(0)}%</td>
                      <td className="syn-mono-td">{fmt(cand.features?.heading_consistency, 2)}</td>
                      <td className="syn-mono-td">{fmt(cand.features?.speed_consistency, 2)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="syn-footnote">
            <strong>Evaluation Note:</strong> Model Probability is the independent output of{' '}
            <code>predict_proba()</code> on the 10 physically grounded features. The Scenario Attribution
            Score represents the scenario-normalized attribution score scaled across candidates in the scenario envelope.
          </div>
        </div>
      )}
    </div>
  )
}
