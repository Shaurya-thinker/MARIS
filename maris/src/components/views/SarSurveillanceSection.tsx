/**
 * SarSurveillanceSection — Phase #6 SAR ↔ AIS Dual-Sensor Maritime Surveillance UI.
 *
 * Displays multi-sensor correlation between CA-CFAR radar bright-target detections
 * and authentic AIS transponder positions using neutral, strictly objective terminology.
 *
 * ZERO-ACCUSATION INVARIANT:
 * - Bright radar targets represent physical scattering anomalies, not confirmed vessels.
 * - Associations represent spatiotemporal correspondence, not proof of intent or wrongdoing.
 * - Non-neutral, speculative, or accusatory terminology is strictly forbidden.
 */

import React, { useState } from 'react'
import {
  Radio,
  ChevronDown,
  ChevronUp,
  ShieldAlert,
  Info,
  ExternalLink,
  Radar,
  Anchor,
  HelpCircle,
} from 'lucide-react'
import type {
  SarAisAssociation,
  SarBrightTarget,
  SarSurveillanceResult,
} from '../../real-experiment/experimentTypes'

interface SarSurveillanceSectionProps {
  surveillance?: SarSurveillanceResult | null
  observationTime?: string
  productId?: string
  onConfigureStep?: () => void
}

// Canonical Phase 6 classification labels & style mapping
export const CLASSIFICATION_LABELS: Record<string, string> = {
  COINCIDENT_AIS_MATCH: 'Coincident AIS Match',
  SPATIAL_DISCREPANCY_EXCEEDANCE: 'Spatial Discrepancy Exceedance',
  AIS_OBSERVATION_GAP: 'AIS Observation Gap',
  RADAR_TARGET_UNCORRELATED: 'Radar Target Uncorrelated',
  AIS_VESSEL_NOT_DETECTED: 'AIS Vessel Not Detected',
  AMBIGUOUS_MULTI_TARGET_PROXIMITY: 'Ambiguous Multi-Target Proximity',
}

function getClassificationBadgeStyle(classification: string): {
  color: string
  background: string
  borderColor: string
} {
  switch (classification) {
    case 'COINCIDENT_AIS_MATCH':
      return {
        color: '#4ade80',
        background: 'rgba(74, 222, 128, 0.12)',
        borderColor: 'rgba(74, 222, 128, 0.35)',
      }
    case 'SPATIAL_DISCREPANCY_EXCEEDANCE':
      return {
        color: '#f59e0b',
        background: 'rgba(245, 158, 11, 0.12)',
        borderColor: 'rgba(245, 158, 11, 0.35)',
      }
    case 'AIS_OBSERVATION_GAP':
      return {
        color: '#fb7185',
        background: 'rgba(251, 113, 133, 0.12)',
        borderColor: 'rgba(251, 113, 133, 0.35)',
      }
    case 'RADAR_TARGET_UNCORRELATED':
      return {
        color: '#38bdf8',
        background: 'rgba(56, 189, 248, 0.12)',
        borderColor: 'rgba(56, 189, 248, 0.35)',
      }
    case 'AIS_VESSEL_NOT_DETECTED':
      return {
        color: '#94a3b8',
        background: 'rgba(148, 163, 184, 0.12)',
        borderColor: 'rgba(148, 163, 184, 0.35)',
      }
    case 'AMBIGUOUS_MULTI_TARGET_PROXIMITY':
      return {
        color: '#c084fc',
        background: 'rgba(192, 132, 252, 0.12)',
        borderColor: 'rgba(192, 132, 252, 0.35)',
      }
    default:
      return {
        color: '#cbd5e1',
        background: 'rgba(203, 213, 225, 0.1)',
        borderColor: 'rgba(203, 213, 225, 0.25)',
      }
  }
}

function getProvenanceBadge(provenance: string | null | undefined) {
  if (!provenance) return <span style={{ color: '#64748b' }}>UNAVAILABLE</span>
  const upper = provenance.toUpperCase()
  if (upper.includes('GENUINE')) {
    return (
      <span
        style={{
          fontSize: '0.72rem',
          color: '#4ade80',
          background: 'rgba(74, 222, 128, 0.12)',
          padding: '0.15rem 0.45rem',
          borderRadius: '4px',
          border: '1px solid rgba(74, 222, 128, 0.3)',
          fontWeight: 600,
        }}
      >
        GENUINE_OBSERVATION
      </span>
    )
  }
  if (upper.includes('INTERPOLAT') || upper.includes('ALIGNED')) {
    return (
      <span
        style={{
          fontSize: '0.72rem',
          color: '#38bdf8',
          background: 'rgba(56, 189, 248, 0.12)',
          padding: '0.15rem 0.45rem',
          borderRadius: '4px',
          border: '1px solid rgba(56, 189, 248, 0.3)',
          fontWeight: 600,
        }}
      >
        TEMPORALLY_ALIGNED_FIX
      </span>
    )
  }
  return (
    <span
      style={{
        fontSize: '0.72rem',
        color: '#94a3b8',
        background: 'rgba(148, 163, 184, 0.12)',
        padding: '0.15rem 0.45rem',
        borderRadius: '4px',
        border: '1px solid rgba(148, 163, 184, 0.25)',
      }}
    >
      {provenance}
    </span>
  )
}

export default function SarSurveillanceSection({
  surveillance,
  observationTime,
  productId,
  onConfigureStep,
}: SarSurveillanceSectionProps) {
  const [isOpen, setIsOpen] = useState(true)

  // Explicit empty state A: surveillance was not run or null
  if (!surveillance) {
    return (
      <div
        className="re-source-zone-card"
        data-testid="sar-surveillance-disabled-card"
        style={{
          marginTop: '1.25rem',
          padding: '1.25rem',
          background: 'rgba(15, 23, 42, 0.6)',
          borderRadius: '8px',
          border: '1px solid rgba(148, 163, 184, 0.2)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <Radio size={18} style={{ color: '#64748b' }} />
          <h4 style={{ margin: 0, fontSize: '0.95rem', color: '#94a3b8', fontWeight: 600 }}>
            SAR ↔ AIS Dual-Sensor Maritime Surveillance
          </h4>
        </div>
        <p
          data-testid="sar-surveillance-disabled-msg"
          style={{ color: '#94a3b8', fontStyle: 'italic', margin: '0.6rem 0 0 0', fontSize: '0.85rem' }}
        >
          Dual-sensor surveillance was not enabled for this experiment.
        </p>
        {onConfigureStep && (
          <div style={{ marginTop: '0.85rem' }}>
            <button
              type="button"
              className="re-btn-ghost"
              data-testid="btn-enable-surveillance-goto-step5"
              onClick={onConfigureStep}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.4rem',
                fontSize: '0.8rem',
                color: '#c084fc',
                borderColor: 'rgba(192, 132, 252, 0.4)',
                padding: '0.4rem 0.8rem',
              }}
            >
              <Radio size={14} />
              Enable & Re-run in Step 5 (Run Configuration) →
            </button>
          </div>
        )}
      </div>
    )
  }

  // Data normalization
  const obsTime = surveillance.observation_time ?? surveillance.observation_time_iso ?? observationTime ?? '—'
  const targetsCount =
    surveillance.total_radar_targets_detected ??
    surveillance.total_sar_targets ??
    (surveillance.targets?.length ?? 0)
  const aisCandidatesCount = surveillance.total_ais_candidates ?? 0
  const coincidentMatches =
    surveillance.correlated_ais_matches ??
    surveillance.matched_coincident_count ??
    0
  const spatialDiscrepancies =
    surveillance.spatial_discrepancies ??
    surveillance.spatial_discrepancy_count ??
    0
  const uncorrelatedTargets =
    surveillance.uncorrelated_radar_targets ??
    surveillance.uncorrelated_target_count ??
    0
  const undetectedVessels =
    surveillance.undetected_ais_vessels ??
    surveillance.undetected_vessel_count ??
    0
  const observationGaps = surveillance.observation_gap_count ?? 0
  const ambiguousProximities = surveillance.ambiguous_count ?? 0

  const associationsList: SarAisAssociation[] =
    surveillance.correlations ?? surveillance.associations ?? []

  const targetsList: SarBrightTarget[] = surveillance.targets ?? []

  return (
    <div
      className="re-source-zone-card"
      data-testid="sar-surveillance-panel"
      style={{
        marginTop: '1.25rem',
        padding: '1.25rem',
        background: 'rgba(15, 23, 42, 0.8)',
        borderRadius: '8px',
        border: '1px solid rgba(56, 189, 248, 0.35)',
        boxShadow: '0 0 16px rgba(56, 189, 248, 0.08)',
      }}
    >
      {/* Header with Collapsible Toggle */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.75rem',
          cursor: 'pointer',
        }}
        onClick={() => setIsOpen(!isOpen)}
        data-testid="sar-surveillance-toggle"
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <Radio size={20} style={{ color: '#38bdf8' }} />
          <div>
            <h4
              style={{
                margin: 0,
                fontSize: '1rem',
                color: '#38bdf8',
                fontWeight: 700,
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
              }}
            >
              SAR ↔ AIS Dual-Sensor Maritime Surveillance
              <span
                style={{
                  fontSize: '0.68rem',
                  padding: '0.15rem 0.5rem',
                  borderRadius: '4px',
                  background: 'rgba(56, 189, 248, 0.15)',
                  color: '#38bdf8',
                  border: '1px solid rgba(56, 189, 248, 0.3)',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                }}
              >
                Phase #6
              </span>
            </h4>
            <div style={{ fontSize: '0.78rem', color: '#94a3b8', marginTop: '0.2rem' }}>
              Observation:{' '}
              <code style={{ color: '#e2e8f0' }}>{obsTime}</code>
              {productId && (
                <span style={{ marginLeft: '0.75rem' }}>
                  Product: <code style={{ color: '#94a3b8' }}>{productId}</code>
                </span>
              )}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          {surveillance.model_version && (
            <span
              style={{
                fontSize: '0.68rem',
                fontFamily: 'monospace',
                padding: '0.15rem 0.45rem',
                borderRadius: '4px',
                background: 'rgba(30, 41, 59, 0.7)',
                color: '#94a3b8',
                border: '1px solid rgba(148, 163, 184, 0.15)',
              }}
            >
              Model: {surveillance.model_version}
            </span>
          )}
          <button
            type="button"
            className="re-btn-ghost"
            style={{ padding: '0.2rem 0.4rem', color: '#94a3b8' }}
            aria-label={isOpen ? 'Collapse surveillance section' : 'Expand surveillance section'}
          >
            {isOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
      </div>

      {isOpen && (
        <div style={{ marginTop: '1.25rem' }}>
          {/* Summary Metric Cards */}
          <div
            className="re-source-grid"
            data-testid="sar-surveillance-summary-grid"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
              gap: '0.6rem',
              marginBottom: '1.25rem',
            }}
          >
            <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.6rem 0.8rem', borderRadius: '6px' }}>
              <div className="re-source-label" style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                Targets detected
              </div>
              <div
                className="re-source-value"
                data-testid="count-targets-detected"
                style={{ fontSize: '1.15rem', color: '#38bdf8', fontWeight: 700 }}
              >
                {targetsCount}
              </div>
            </div>

            <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.6rem 0.8rem', borderRadius: '6px' }}>
              <div className="re-source-label" style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                AIS associations
              </div>
              <div
                className="re-source-value"
                data-testid="count-ais-associations"
                style={{ fontSize: '1.15rem', color: '#4ade80', fontWeight: 700 }}
              >
                {coincidentMatches}
              </div>
            </div>

            <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.6rem 0.8rem', borderRadius: '6px' }}>
              <div className="re-source-label" style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                Spatial discrepancies
              </div>
              <div
                className="re-source-value"
                data-testid="count-spatial-discrepancies"
                style={{ fontSize: '1.15rem', color: '#f59e0b', fontWeight: 700 }}
              >
                {spatialDiscrepancies}
              </div>
            </div>

            <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.6rem 0.8rem', borderRadius: '6px' }}>
              <div className="re-source-label" style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                Uncorrelated radar targets
              </div>
              <div
                className="re-source-value"
                data-testid="count-uncorrelated-targets"
                style={{ fontSize: '1.15rem', color: '#38bdf8', fontWeight: 700 }}
              >
                {uncorrelatedTargets}
              </div>
            </div>

            <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.6rem 0.8rem', borderRadius: '6px' }}>
              <div className="re-source-label" style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                AIS vessels not detected
              </div>
              <div
                className="re-source-value"
                data-testid="count-undetected-vessels"
                style={{ fontSize: '1.15rem', color: '#94a3b8', fontWeight: 700 }}
              >
                {undetectedVessels}
              </div>
            </div>

            {ambiguousProximities > 0 && (
              <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.6rem 0.8rem', borderRadius: '6px' }}>
                <div className="re-source-label" style={{ fontSize: '0.72rem', color: '#c084fc' }}>
                  Ambiguous proximities
                </div>
                <div
                  className="re-source-value"
                  data-testid="count-ambiguous-proximities"
                  style={{ fontSize: '1.15rem', color: '#c084fc', fontWeight: 700 }}
                >
                  {ambiguousProximities}
                </div>
              </div>
            )}
          </div>

          {/* Explicit Empty States B, C, D */}
          {targetsCount === 0 && (
            <div
              className="re-empty-state"
              data-testid="sar-surveillance-zero-targets"
              style={{
                padding: '1rem',
                textAlign: 'center',
                color: '#94a3b8',
                background: 'rgba(30, 41, 59, 0.4)',
                borderRadius: '6px',
                border: '1px dashed rgba(148, 163, 184, 0.25)',
                marginBottom: '1rem',
              }}
            >
              No bright radar targets detected in the configured SAR scene.
            </div>
          )}

          {aisCandidatesCount === 0 && targetsCount > 0 && (
            <div
              className="re-empty-state"
              data-testid="sar-surveillance-zero-ais"
              style={{
                padding: '1rem',
                textAlign: 'center',
                color: '#94a3b8',
                background: 'rgba(30, 41, 59, 0.4)',
                borderRadius: '6px',
                border: '1px dashed rgba(148, 163, 184, 0.25)',
                marginBottom: '1rem',
              }}
            >
              No AIS observations were available within the configured search window.
            </div>
          )}

          {targetsCount > 0 && aisCandidatesCount > 0 && associationsList.length === 0 && (
            <div
              className="re-empty-state"
              data-testid="sar-surveillance-zero-associations"
              style={{
                padding: '1rem',
                textAlign: 'center',
                color: '#94a3b8',
                background: 'rgba(30, 41, 59, 0.4)',
                borderRadius: '6px',
                border: '1px dashed rgba(148, 163, 184, 0.25)',
                marginBottom: '1rem',
              }}
            >
              No AIS association was found within the configured search gate.
            </div>
          )}

          {/* Association Table */}
          {associationsList.length > 0 && (
            <div className="re-results-table-wrap" style={{ overflowX: 'auto', marginBottom: '1.25rem' }}>
              <table className="re-table" data-testid="sar-surveillance-table" style={{ width: '100%', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ background: 'rgba(30, 41, 59, 0.8)', textAlign: 'left' }}>
                    <th style={{ padding: '0.6rem 0.75rem' }}>Target</th>
                    <th style={{ padding: '0.6rem 0.75rem' }}>AIS Vessel</th>
                    <th style={{ padding: '0.6rem 0.75rem' }}>Classification</th>
                    <th style={{ padding: '0.6rem 0.75rem' }}>Distance</th>
                    <th style={{ padding: '0.6rem 0.75rem' }}>Time Δ</th>
                    <th style={{ padding: '0.6rem 0.75rem' }}>AIS Position Provenance</th>
                    <th style={{ padding: '0.6rem 0.75rem' }}>Investigation Flag</th>
                  </tr>
                </thead>
                <tbody>
                  {associationsList.map((assoc, idx) => {
                    const badge = getClassificationBadgeStyle(assoc.classification)
                    const classLabel = CLASSIFICATION_LABELS[assoc.classification] ?? assoc.classification
                    const dist = assoc.spatial_separation_m ?? assoc.distance_meters
                    const timeDelta = assoc.temporal_delta_seconds ?? assoc.ais_time_offset_seconds

                    const targetId = assoc.target?.target_id ?? assoc.target_id
                    const targetPeak = assoc.target?.peak_backscatter_db ?? assoc.target_peak_db
                    const targetTcr = assoc.target?.target_to_clutter_ratio_db ?? assoc.target_tcr_db

                    const targetDisplay = targetId ? (
                      <div>
                        <strong>Target {targetId}</strong>
                        {targetPeak != null && (
                          <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                            {targetPeak.toFixed(1)} dBσ₀ | TCR: {targetTcr?.toFixed(1) ?? '—'} dB
                          </div>
                        )}
                      </div>
                    ) : (
                      <span style={{ color: '#64748b', fontStyle: 'italic' }}>No SAR bright target</span>
                    )

                    const vesselDisplay =
                      assoc.vessel_name || assoc.mmsi || assoc.vessel_id ? (
                        <div>
                          <strong>{assoc.vessel_name ?? (assoc.mmsi ? `MMSI: ${assoc.mmsi}` : assoc.vessel_id)}</strong>
                          {assoc.mmsi && assoc.vessel_name && (
                            <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>MMSI: {assoc.mmsi}</div>
                          )}
                        </div>
                      ) : (
                        <span style={{ color: '#64748b', fontStyle: 'italic' }}>No AIS association</span>
                      )

                    const provenanceStr =
                      assoc.ais_position_provenance ?? assoc.ais_alignment_method ?? null

                    return (
                      <tr
                        key={assoc.correlation_id ?? assoc.association_id ?? `assoc-${idx}`}
                        style={{
                          borderBottom: '1px solid rgba(148, 163, 184, 0.1)',
                          background: idx % 2 === 0 ? 'rgba(15, 23, 42, 0.2)' : 'transparent',
                        }}
                      >
                        <td style={{ padding: '0.6rem 0.75rem' }}>{targetDisplay}</td>
                        <td style={{ padding: '0.6rem 0.75rem' }}>{vesselDisplay}</td>
                        <td style={{ padding: '0.6rem 0.75rem' }}>
                          <span
                            style={{
                              fontSize: '0.72rem',
                              fontWeight: 600,
                              padding: '0.2rem 0.5rem',
                              borderRadius: '4px',
                              color: badge.color,
                              background: badge.background,
                              border: `1px solid ${badge.borderColor}`,
                              display: 'inline-block',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {classLabel}
                          </span>
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', fontFamily: 'monospace' }}>
                          {dist != null ? (
                            `${dist.toFixed(0)} m`
                          ) : assoc.classification === 'AIS_VESSEL_NOT_DETECTED' || !targetId ? (
                            <span
                              style={{ color: '#94a3b8', fontStyle: 'italic', fontSize: '0.74rem' }}
                              title="No bright radar target detected within the 3,000m association gate"
                            >
                              Unassociated (&gt;3,000m)
                            </span>
                          ) : assoc.classification === 'RADAR_TARGET_UNCORRELATED' ? (
                            <span
                              style={{ color: '#94a3b8', fontStyle: 'italic', fontSize: '0.74rem' }}
                              title="No AIS transponder fix within the 3,000m association gate"
                            >
                              Unassociated (&gt;3,000m)
                            </span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', fontFamily: 'monospace' }}>
                          {timeDelta != null ? `${Math.abs(timeDelta).toFixed(0)} s` : '—'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem' }}>{getProvenanceBadge(provenanceStr)}</td>
                        <td style={{ padding: '0.6rem 0.75rem', fontSize: '0.75rem', color: '#cbd5e1' }}>
                          {assoc.classification === 'AMBIGUOUS_MULTI_TARGET_PROXIMITY' ? (
                            <span style={{ color: '#c084fc', fontWeight: 600 }}>
                              Ambiguous Multi-Target Proximity ({assoc.ambiguous_candidate_ids?.length ?? 2} candidates)
                            </span>
                          ) : (
                            assoc.investigation_flag?.toString() || assoc.findings_summary || assoc.notes || '—'
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Explanatory Scientific Note */}
          <div
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '0.6rem',
              background: 'rgba(30, 41, 59, 0.4)',
              padding: '0.75rem 0.9rem',
              borderRadius: '6px',
              border: '1px solid rgba(148, 163, 184, 0.15)',
              marginBottom: '0.75rem',
              fontSize: '0.78rem',
              color: '#94a3b8',
              lineHeight: 1.5,
            }}
          >
            <Info size={16} style={{ color: '#38bdf8', flexShrink: 0, marginTop: '2px' }} />
            <div>
              <strong>Scientific interpretation guidance:</strong> Bright radar targets are SAR scattering detections
              and are not automatically classified as vessels. Association with an AIS record indicates
              spatial/temporal correspondence, not proof of causation, identity, intent, or illegal activity.
            </div>
          </div>

          {/* Verbatim Backend Scientific Disclaimer */}
          <div
            data-testid="sar-surveillance-disclaimer"
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '0.6rem',
              background: 'rgba(239, 68, 68, 0.06)',
              padding: '0.75rem 0.9rem',
              borderRadius: '6px',
              border: '1px solid rgba(239, 68, 68, 0.25)',
              fontSize: '0.75rem',
              color: '#fca5a5',
              lineHeight: 1.5,
            }}
          >
            <ShieldAlert size={16} style={{ color: '#ef4444', flexShrink: 0, marginTop: '2px' }} />
            <div>
              <strong>SCIENTIFIC OBSERVATIONAL DISCLAIMER:</strong>{' '}
              {surveillance.scientific_disclaimer ||
                'Dual-sensor observational comparison only. Radar scatterer detections represent physical reflectivity anomalies and do NOT constitute confirmed vessel identifications. Observational discrepancies indicate surveillance anomalies, not proof of intentional AIS manipulation or illicit activity.'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
