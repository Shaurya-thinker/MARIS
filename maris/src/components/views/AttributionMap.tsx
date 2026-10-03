import React, { useEffect, useId, useRef, useState, useMemo, useCallback } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Map as MapLibreMap, Marker as MapLibreMarker } from 'maplibre-gl'
import {
  Crosshair,
  MapPin,
  Maximize2,
  Ship,
  ZoomIn,
  ZoomOut,
  Compass,
} from 'lucide-react'
import type {
  AisPosition,
  BackwardStep,
  ForwardDriftStep,
  ForwardPredictionResult,
  SarAisAssociation,
  SarBrightTarget,
  SarSurveillanceResult,
  VesselFeatures,
} from '../../real-experiment/experimentTypes'

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

export const CANDIDATE_COLORS = [
  '#38bdf8', // 1. Sky blue (MV Ulysse)
  '#fbbf24', // 2. Bright Amber/Yellow (Mediterranean Star)
  '#a855f7', // 3. Purple
  '#10b981', // 4. Emerald
  '#f43f5e', // 5. Rose
  '#06b6d4', // 6. Cyan
]

export interface AttributionMapProps {
  observation: {
    lat: number
    lon: number
    timestamp: string
    title?: string
    footprint?: Record<string, unknown> | null
    slickGeometry?: Record<string, unknown> | null
    areaKm2?: number | null
    confidence?: number | null
    dampingContrastDb?: number | null
    detectionStatus?: string
  }
  reconstructedSource: {
    lat: number
    lon: number
    radiusM: number
    geojson?: Record<string, unknown> | null
  }
  backwardSteps: BackwardStep[]
  vessels: VesselFeatures[]
  selectedVesselId: string | null
  onSelectVessel: (vesselId: string | null) => void
  vesselPositionsMap?: Record<string, AisPosition[]>
  forwardSteps?: ForwardDriftStep[]
  forwardPrediction?: ForwardPredictionResult | null
  sarSurveillance?: SarSurveillanceResult | null
}

export function buildCirclePolygon(
  centerLon: number,
  centerLat: number,
  radiusM: number,
  steps = 64
): GeoJSON.Polygon {
  const coords: [number, number][] = []
  const latRad = (centerLat * Math.PI) / 180
  const cosLat = Math.abs(Math.cos(latRad)) > 1e-6 ? Math.cos(latRad) : 1e-6
  const mPerDegLat = 111320

  for (let i = 0; i <= steps; i++) {
    const angle = (2 * Math.PI * (i % steps)) / steps
    const dx = radiusM * Math.cos(angle)
    const dy = radiusM * Math.sin(angle)
    const pLon = centerLon + dx / (cosLat * mPerDegLat)
    const pLat = centerLat + dy / mPerDegLat
    coords.push([Number(pLon.toFixed(6)), Number(pLat.toFixed(6))])
  }

  return {
    type: 'Polygon',
    coordinates: [coords],
  }
}

export default function AttributionMap({
  observation,
  reconstructedSource,
  backwardSteps,
  vessels,
  selectedVesselId,
  onSelectVessel,
  vesselPositionsMap = {},
  forwardSteps = [],
  forwardPrediction = null,
  sarSurveillance = null,
}: AttributionMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef<MapLibreMarker[]>([])
  const popupIdPrefix = useId()

  // Layer visibility toggles
  const [showDrift, setShowDrift] = useState(true)
  const [showForwardDrift, setShowForwardDrift] = useState(true)
  const [showAis, setShowAis] = useState(true)
  const [showWind, setShowWind] = useState(false)
  const [showCurrents, setShowCurrents] = useState(false)
  const [showUncertainty, setShowUncertainty] = useState(true)
  const [showSurveillance, setShowSurveillance] = useState(true)

  // Map state
  const [_mapLoaded, setMapLoaded] = useState(false)
  const [useFallbackSvg, setUseFallbackSvg] = useState(false)
  const [activePopup, setActivePopup] = useState<
    | { type: 'observation' }
    | { type: 'source' }
    | { type: 'forward_step'; step: ForwardDriftStep; index: number }
    | { type: 'vessel'; vessel: VesselFeatures }
    | { type: 'radar_target'; target: SarBrightTarget }
    | { type: 'sar_ais_obs'; association: SarAisAssociation }
    | { type: 'sar_ais_assoc'; association: SarAisAssociation }
    | null
  >(null)

  // Surveillance data memoization
  const associationsList = useMemo(() => {
    return sarSurveillance?.associations ?? sarSurveillance?.correlations ?? []
  }, [sarSurveillance])

  const surveillanceTargets = useMemo(() => {
    if (!sarSurveillance) return []
    const seen = new Set<string>()
    const list: SarBrightTarget[] = []

    // 1. Direct targets array on surveillance result
    if (sarSurveillance.targets && Array.isArray(sarSurveillance.targets)) {
      sarSurveillance.targets.forEach((t) => {
        if (!seen.has(t.target_id) && Number.isFinite(t.lat) && Number.isFinite(t.lon)) {
          seen.add(t.target_id)
          list.push(t)
        }
      })
    }

    // 2. Targets attached to associations
    associationsList.forEach((c) => {
      if (c.target && !seen.has(c.target.target_id) && Number.isFinite(c.target.lat) && Number.isFinite(c.target.lon)) {
        seen.add(c.target.target_id)
        list.push(c.target)
      } else if (c.target_id && c.target_lat != null && c.target_lon != null && !seen.has(c.target_id)) {
        seen.add(c.target_id)
        list.push({
          target_id: c.target_id,
          pixel_x: 0,
          pixel_y: 0,
          lon: c.target_lon,
          lat: c.target_lat,
          peak_backscatter_db: c.target_peak_db ?? 0,
          local_clutter_mean_db: 0,
          target_to_clutter_ratio_db: c.target_tcr_db ?? 0,
          pixel_count: 1,
        })
      }
    })
    return list
  }, [sarSurveillance, associationsList])

  const surveillanceAisObs = useMemo(() => {
    return associationsList.filter(
      (c) => c.ais_lon != null && c.ais_lat != null && Number.isFinite(c.ais_lon) && Number.isFinite(c.ais_lat)
    )
  }, [associationsList])

  const surveillanceAssociations = useMemo(() => {
    return associationsList.filter((c) => {
      const hasTarget =
        (c.target && Number.isFinite(c.target.lon) && Number.isFinite(c.target.lat)) ||
        (c.target_lon != null && c.target_lat != null && Number.isFinite(c.target_lon) && Number.isFinite(c.target_lat))
      const hasAis =
        c.ais_lon != null && c.ais_lat != null && Number.isFinite(c.ais_lon) && Number.isFinite(c.ais_lat)
      return hasTarget && hasAis
    })
  }, [associationsList])

  // Compute effective vessel positions per candidate
  const candidateTrackData = useMemo(() => {
    return vessels.map((v, idx) => {
      const color = CANDIDATE_COLORS[idx % CANDIDATE_COLORS.length]
      const positions =
        (v.positions && v.positions.length > 0
          ? v.positions
          : vesselPositionsMap[v.vessel_id] || vesselPositionsMap[v.mmsi ?? '']) ?? []
      return {
        vessel: v,
        color,
        positions,
        hasPositions: positions.length > 0,
      }
    })
  }, [vessels, vesselPositionsMap])

  // Backward drift line coordinates [lon, lat]
  const driftLineCoords = useMemo(() => {
    const coords: [number, number][] = [[observation.lon, observation.lat]]
    backwardSteps.forEach((s) => {
      if (Number.isFinite(s.lon) && Number.isFinite(s.lat)) {
        coords.push([s.lon, s.lat])
      }
    })
    if (
      coords.length === 1 ||
      coords[coords.length - 1][0] !== reconstructedSource.lon ||
      coords[coords.length - 1][1] !== reconstructedSource.lat
    ) {
      coords.push([reconstructedSource.lon, reconstructedSource.lat])
    }
    return coords
  }, [observation, backwardSteps, reconstructedSource])

  // Forward predicted drift line coordinates [lon, lat] starting exactly from observation
  const forwardLineCoords = useMemo(() => {
    const coords: [number, number][] = [[observation.lon, observation.lat]]
    const steps = forwardSteps.length > 0 ? forwardSteps : (forwardPrediction?.steps ?? [])
    steps.forEach((s) => {
      if (Number.isFinite(s.lon) && Number.isFinite(s.lat)) {
        if (s.step === 0 && Math.abs(s.lon - observation.lon) < 1e-5 && Math.abs(s.lat - observation.lat) < 1e-5) {
          return
        }
        coords.push([s.lon, s.lat])
      }
    })
    return coords
  }, [observation, forwardSteps, forwardPrediction])

  // Uncertainty polygon (6.5 km)
  const uncertaintyPolygon = useMemo(() => {
    if (
      reconstructedSource.geojson &&
      typeof reconstructedSource.geojson === 'object' &&
      reconstructedSource.geojson.type === 'Polygon'
    ) {
      return reconstructedSource.geojson as GeoJSON.Polygon
    }
    return buildCirclePolygon(
      reconstructedSource.lon,
      reconstructedSource.lat,
      reconstructedSource.radiusM || 6500
    )
  }, [reconstructedSource])

  // Compute tight bounding box enclosing ALL scientific evidence
  const evidenceBounds = useMemo(() => {
    let minLat = Math.min(observation.lat, reconstructedSource.lat)
    let maxLat = Math.max(observation.lat, reconstructedSource.lat)
    let minLon = Math.min(observation.lon, reconstructedSource.lon)
    let maxLon = Math.max(observation.lon, reconstructedSource.lon)

    // Include uncertainty radius in degrees
    const latRadiusDeg = (reconstructedSource.radiusM || 6500) / 111320
    const lonRadiusDeg = latRadiusDeg / Math.cos((reconstructedSource.lat * Math.PI) / 180)
    minLat = Math.min(minLat, reconstructedSource.lat - latRadiusDeg)
    maxLat = Math.max(maxLat, reconstructedSource.lat + latRadiusDeg)
    minLon = Math.min(minLon, reconstructedSource.lon - lonRadiusDeg)
    maxLon = Math.max(maxLon, reconstructedSource.lon + lonRadiusDeg)

    backwardSteps.forEach((s) => {
      if (Number.isFinite(s.lat) && Number.isFinite(s.lon)) {
        minLat = Math.min(minLat, s.lat)
        maxLat = Math.max(maxLat, s.lat)
        minLon = Math.min(minLon, s.lon)
        maxLon = Math.max(maxLon, s.lon)
      }
    })

    candidateTrackData.forEach((ct) => {
      ct.positions.forEach((p) => {
        if (Number.isFinite(p.lat) && Number.isFinite(p.lon)) {
          minLat = Math.min(minLat, p.lat)
          maxLat = Math.max(maxLat, p.lat)
          minLon = Math.min(minLon, p.lon)
          maxLon = Math.max(maxLon, p.lon)
        }
      })
    })

    if (forwardLineCoords.length > 1) {
      forwardLineCoords.forEach((pt) => {
        minLat = Math.min(minLat, pt[1])
        maxLat = Math.max(maxLat, pt[1])
        minLon = Math.min(minLon, pt[0])
        maxLon = Math.max(maxLon, pt[0])
      })
    }

    if (sarSurveillance) {
      surveillanceTargets.forEach((t) => {
        if (Number.isFinite(t.lat) && Number.isFinite(t.lon)) {
          minLat = Math.min(minLat, t.lat)
          maxLat = Math.max(maxLat, t.lat)
          minLon = Math.min(minLon, t.lon)
          maxLon = Math.max(maxLon, t.lon)
        }
      })
      surveillanceAisObs.forEach((c) => {
        if (c.ais_lat != null && c.ais_lon != null && Number.isFinite(c.ais_lat) && Number.isFinite(c.ais_lon)) {
          minLat = Math.min(minLat, c.ais_lat)
          maxLat = Math.max(maxLat, c.ais_lat)
          minLon = Math.min(minLon, c.ais_lon)
          maxLon = Math.max(maxLon, c.ais_lon)
        }
      })
    }

    // Add 18% padding so all markers, pills, and tracks fit comfortably
    const padLat = Math.max((maxLat - minLat) * 0.18, 0.05)
    const padLon = Math.max((maxLon - minLon) * 0.18, 0.05)

    return {
      south: minLat - padLat,
      north: maxLat + padLat,
      west: minLon - padLon,
      east: maxLon + padLon,
    }
  }, [observation, reconstructedSource, backwardSteps, candidateTrackData, forwardLineCoords, sarSurveillance])

  // Metocean vector availability check
  const hasWindVectors = useMemo(() => {
    return backwardSteps.some((s) => s.u_wind_ms != null && s.v_wind_ms != null)
  }, [backwardSteps])

  const hasCurrentVectors = useMemo(() => {
    return backwardSteps.some((s) => s.u_current_ms != null && s.v_current_ms != null)
  }, [backwardSteps])

  // Clear existing MapLibre HTML markers
  const clearMarkers = useCallback(() => {
    markersRef.current.forEach((m) => m.remove())
    markersRef.current = []
  }, [])

  // Add prominent permanent HTML markers to MapLibre
  const syncHtmlMarkers = useCallback(
    (map: MapLibreMap) => {
      clearMarkers()

      // 1. OBSERVED SPILL / SLICK MARKER
      const obsEl = document.createElement('div')
      obsEl.className = 're-map-marker-obs-wrap'
      const areaTag = observation.areaKm2 ? ` (${observation.areaKm2.toFixed(1)} km²)` : ''
      obsEl.innerHTML = `
        <div class="re-marker-pill re-marker-pill-obs">🔵 DETECTED SLICK${areaTag}</div>
        <div class="re-marker-dot re-marker-dot-obs">
          <div class="re-marker-pulse"></div>
        </div>
      `
      obsEl.onclick = (e) => {
        e.stopPropagation()
        setActivePopup({ type: 'observation' })
      }

      const obsMarker = new maplibregl.Marker({ element: obsEl, anchor: 'bottom' })
        .setLngLat([observation.lon, observation.lat])
        .addTo(map)
      markersRef.current.push(obsMarker)

      // 2. RECONSTRUCTED SOURCE MARKER
      const srcEl = document.createElement('div')
      srcEl.className = 're-map-marker-src-wrap'
      srcEl.innerHTML = `
        <div class="re-marker-pill re-marker-pill-src">🔴 RECONSTRUCTED SOURCE</div>
        <div class="re-marker-dot re-marker-dot-src">
          <div class="re-marker-pulse-red"></div>
        </div>
      `
      srcEl.onclick = (e) => {
        e.stopPropagation()
        setActivePopup({ type: 'source' })
      }

      const srcMarker = new maplibregl.Marker({ element: srcEl, anchor: 'bottom' })
        .setLngLat([reconstructedSource.lon, reconstructedSource.lat])
        .addTo(map)
      markersRef.current.push(srcMarker)

      // 3. SOURCE UNCERTAINTY BADGE (Positioned at northern perimeter rim of circle)
      if (showUncertainty) {
        const uncEl = document.createElement('div')
        uncEl.className = 're-marker-pill-unc'
        uncEl.innerText = `SOURCE UNCERTAINTY — ${(reconstructedSource.radiusM / 1000).toFixed(1)} km`

        // Position on the northern perimeter rim so it never overlaps the source center marker
        const latRadiusDeg = (reconstructedSource.radiusM || 6500) / 111320
        const uncMarker = new maplibregl.Marker({ element: uncEl, anchor: 'bottom' })
          .setLngLat([reconstructedSource.lon, reconstructedSource.lat + latRadiusDeg * 1.02])
          .addTo(map)
        markersRef.current.push(uncMarker)
      }

      // 4. BACKWARD DRIFT LABELS & SUBTLE STEP BADGES
      if (showDrift && backwardSteps.length > 0) {
        // Trajectory middle label: 🟠 BACKWARD DRIFT
        const midIdx = Math.floor(backwardSteps.length / 2)
        const midStep = backwardSteps[midIdx]
        if (midStep && Number.isFinite(midStep.lon) && Number.isFinite(midStep.lat)) {
          const driftEl = document.createElement('div')
          driftEl.className = 're-marker-pill'
          driftEl.style.cssText =
            'background: rgba(40, 20, 5, 0.92); color: #fbbf24; border: 1.5px solid #f59e0b; font-size: 0.72rem; box-shadow: 0 0 14px rgba(245, 158, 11, 0.45); pointer-events: none;'
          driftEl.innerText = '🟠 BACKWARD DRIFT'
          const driftMarker = new maplibregl.Marker({ element: driftEl, anchor: 'center' })
            .setLngLat([midStep.lon, midStep.lat + 0.012])
            .addTo(map)
          markersRef.current.push(driftMarker)
        }

        // Only small subtle badges at intermediate steps away from source & observation
        backwardSteps.forEach((step, idx) => {
          const stepNum = idx + 1
          const dObs = Math.hypot(step.lon - observation.lon, step.lat - observation.lat)
          const dSrc = Math.hypot(step.lon - reconstructedSource.lon, step.lat - reconstructedSource.lat)
          // Avoid cluttering near slick observation (< 0.06°) or source (< 0.06°)
          if ((stepNum === 3 || stepNum === 6) && dObs > 0.06 && dSrc > 0.06) {
            const stepEl = document.createElement('div')
            stepEl.className = 're-drift-step-pill'
            stepEl.innerText = `t−${stepNum}h`
            const stepMarker = new maplibregl.Marker({ element: stepEl, anchor: 'left' })
              .setLngLat([step.lon, step.lat])
              .addTo(map)
            markersRef.current.push(stepMarker)
          }
        })
      }

      // 4b. FORWARD DRIFT FINAL POSITION MARKER
      const effectiveForwardSteps = forwardSteps.length > 0 ? forwardSteps : (forwardPrediction?.steps ?? [])
      if (showForwardDrift && effectiveForwardSteps.length > 0) {
        const lastStep = effectiveForwardSteps[effectiveForwardSteps.length - 1]
        if (lastStep && Number.isFinite(lastStep.lon) && Number.isFinite(lastStep.lat)) {
          const fwdEl = document.createElement('div')
          fwdEl.className = 're-map-marker-fwd-wrap'
          fwdEl.style.cursor = 'pointer'
          const durationH = forwardPrediction?.prediction_hours ?? lastStep.step
          fwdEl.innerHTML = `
            <div class="re-marker-pill" style="background: rgba(6, 40, 50, 0.92); color: #22d3ee; border: 1.5px solid #06b6d4; font-size: 0.72rem; box-shadow: 0 0 14px rgba(6, 182, 212, 0.45); white-space: nowrap;">
              🔮 FORWARD PROJECTION (+${durationH}h)
            </div>
            <div class="re-marker-dot" style="width: 12px; height: 12px; border-radius: 50%; background: #06b6d4; border: 2px solid #ffffff; box-shadow: 0 0 8px #22d3ee; margin: 2px auto 0 auto;"></div>
          `
          fwdEl.onclick = (e) => {
            e.stopPropagation()
            setActivePopup({ type: 'forward_step', step: lastStep, index: effectiveForwardSteps.length - 1 })
          }
          // Position cleanly to North-West so it never overlaps the Detected Slick marker at North
          const isNearObs = Math.hypot(lastStep.lon - observation.lon, lastStep.lat - observation.lat) < 0.04
          const fwdMarker = new maplibregl.Marker({
            element: fwdEl,
            anchor: 'bottom-right',
            offset: isNearObs ? [-38, -14] : [-16, -12],
          })
            .setLngLat([lastStep.lon, lastStep.lat])
            .addTo(map)
          markersRef.current.push(fwdMarker)
        }
      }

      // 5. AIS VESSEL LABELS (Positioned at track position farthest from the reconstructed source)
      if (showAis) {
        candidateTrackData.forEach((ct) => {
          if (!ct.hasPositions || ct.positions.length === 0) return

          // Pick the position along the vessel's track that is farthest from the reconstructed source
          let bestPos = ct.positions[0]
          let maxDistSq = -1
          const srcLat = reconstructedSource.lat
          const srcLon = reconstructedSource.lon
          const cosLat = Math.cos((srcLat * Math.PI) / 180)

          ct.positions.forEach((p) => {
            const dLat = p.lat - srcLat
            const dLon = (p.lon - srcLon) * cosLat
            const distSq = dLat * dLat + dLon * dLon
            if (distSq > maxDistSq) {
              maxDistSq = distSq
              bestPos = p
            }
          })

          const isUlysse = (ct.vessel.vessel_name ?? '').toUpperCase().includes('ULYSSE')
          const isNearOrigin = Math.hypot(bestPos.lon - observation.lon, bestPos.lat - observation.lat) < 0.012

          const vesselEl = document.createElement('div')
          vesselEl.className = 're-marker-vessel-wrap'
          const icon = isUlysse ? '🔷' : '🟡'
          vesselEl.innerHTML = `
            <div class="re-marker-pill re-marker-pill-vessel" style="border-color: ${ct.color}; color: ${ct.color}; white-space: nowrap;">
              ${icon} ${ct.vessel.vessel_name ?? ct.vessel.mmsi} (${(ct.vessel.evidence_consistency_score * 100).toFixed(1)}%)
            </div>
          `
          vesselEl.onclick = (e) => {
            e.stopPropagation()
            onSelectVessel(ct.vessel.vessel_id)
            setActivePopup({ type: 'vessel', vessel: ct.vessel })
          }

          // Non-overlapping quadrant: If co-located near collision origin, position at South / Center-Bottom
          // (underneath the surveillance AIS fan) with enough clearance so it never overlaps any marker
          const vMarker = new maplibregl.Marker({
            element: vesselEl,
            anchor: isNearOrigin ? 'top' : 'left',
            offset: isNearOrigin ? (showSurveillance ? [0, 60] : [24, 24]) : [10, 0],
          })
            .setLngLat([bestPos.lon, bestPos.lat])
            .addTo(map)
          markersRef.current.push(vMarker)
        })
      }

      // 6. SAR ↔ AIS Surveillance Markers (Bright Radar Targets & AIS Positions)
      if (showSurveillance && sarSurveillance) {
        // A. Bright Radar Targets (◆)
        surveillanceTargets.forEach((target, tIdx) => {
          if (!Number.isFinite(target.lat) || !Number.isFinite(target.lon)) return
          const el = document.createElement('div')
          el.className = 're-map-marker-radar-target'
          el.style.cursor = 'pointer'
          el.innerHTML = `
            <div class="re-marker-pill" style="background: rgba(45, 26, 3, 0.95); color: #fbbf24; border: 1.5px solid #f59e0b; font-size: 0.7rem; box-shadow: 0 0 10px rgba(245, 158, 11, 0.5); white-space: nowrap;">
              ◆ Bright Radar Target (${target.target_id})
            </div>
            <div style="width: 10px; height: 10px; background: #f59e0b; border: 2px solid #ffffff; transform: rotate(45deg); margin: 2px auto 0 auto; box-shadow: 0 0 8px #fbbf24;"></div>
          `
          el.onclick = (e) => {
            e.stopPropagation()
            setActivePopup({ type: 'radar_target', target })
          }
          // Position at North-East quadrant
          const tMarker = new maplibregl.Marker({
            element: el,
            anchor: 'bottom-left',
            offset: [24 + tIdx * 16, -16],
          })
            .setLngLat([target.lon, target.lat])
            .addTo(map)
          markersRef.current.push(tMarker)
        })

        // B. AIS Surveillance Positions (○)
        // Radial / staggered layout so multiple AIS fixes near collision origin don't occlude each other
        surveillanceAisObs.forEach((assoc, sIdx) => {
          if (assoc.ais_lat == null || assoc.ais_lon == null || !Number.isFinite(assoc.ais_lat) || !Number.isFinite(assoc.ais_lon)) return
          const el = document.createElement('div')
          el.className = 're-map-marker-surv-ais'
          el.style.cursor = 'pointer'
          el.innerHTML = `
            <div class="re-marker-pill" style="background: rgba(12, 34, 56, 0.95); color: #38bdf8; border: 1.5px solid #38bdf8; font-size: 0.7rem; box-shadow: 0 0 10px rgba(56, 189, 248, 0.45); white-space: nowrap;">
              ○ AIS: ${assoc.vessel_name ?? assoc.mmsi ?? 'Track'}
            </div>
            <div style="width: 10px; height: 10px; border-radius: 50%; background: #0284c7; border: 2px solid #38bdf8; margin: 2px auto 0 auto; box-shadow: 0 0 8px #38bdf8;"></div>
          `
          el.onclick = (e) => {
            e.stopPropagation()
            setActivePopup({ type: 'sar_ais_obs', association: assoc })
          }

          // Fan out below origin:
          // Left branch (MV Ulysse) to South-West, Right branch (CSL Virginia) to South-East
          const isLeft = sIdx % 2 === 0
          const tier = Math.floor(sIdx / 2)
          const isNearObs = Math.hypot(assoc.ais_lon - observation.lon, assoc.ais_lat - observation.lat) < 0.012

          const offX = isNearObs
            ? (isLeft ? -(38 + tier * 20) : (38 + tier * 20))
            : (isLeft ? -(26 + tier * 16) : (26 + tier * 16))
          const offY = isNearObs
            ? (20 + tier * 28)
            : (16 + tier * 28)

          const marker = new maplibregl.Marker({
            element: el,
            anchor: isLeft ? 'top-right' : 'top-left',
            offset: [offX, offY],
          })
            .setLngLat([assoc.ais_lon, assoc.ais_lat])
            .addTo(map)
          markersRef.current.push(marker)
        })
      }
    },
    [
      observation,
      reconstructedSource,
      candidateTrackData,
      showUncertainty,
      showDrift,
      showForwardDrift,
      backwardSteps,
      forwardSteps,
      forwardPrediction,
      forwardLineCoords,
      showAis,
      showSurveillance,
      sarSurveillance,
      surveillanceTargets,
      surveillanceAisObs,
      onSelectVessel,
      clearMarkers,
    ]
  )

  // Register and sync MapLibre GeoJSON layers
  const syncMapLayers = useCallback(
    (map: MapLibreMap) => {
      // 0. Source: Detected Slick / Anomaly Polygon (Step 10)
      const slickGeo = observation.slickGeometry ?? observation.footprint
      if (slickGeo && typeof slickGeo === 'object' && ('type' in slickGeo || 'coordinates' in slickGeo)) {
        const slickGeoData = (
          slickGeo.type === 'Feature' || slickGeo.type === 'Polygon' || slickGeo.type === 'MultiPolygon'
            ? slickGeo
            : { type: 'Feature', geometry: slickGeo, properties: {} }
        ) as GeoJSON.GeoJSON
        const slickSrc = map.getSource('slick-detected-geo') as maplibregl.GeoJSONSource | undefined
        if (slickSrc) {
          slickSrc.setData(slickGeoData)
        } else {
          map.addSource('slick-detected-geo', {
            type: 'geojson',
            data: slickGeoData,
          })
          map.addLayer({
            id: 'slick-detected-fill',
            type: 'fill',
            source: 'slick-detected-geo',
            paint: {
              'fill-color': '#06b6d4',
              'fill-opacity': 0.35,
            },
          })
          map.addLayer({
            id: 'slick-detected-line',
            type: 'line',
            source: 'slick-detected-geo',
            paint: {
              'line-color': '#22d3ee',
              'line-width': 2.5,
            },
          })
        }
      }

      // 1. Source: Uncertainty Zone Polygon
      const uncSrc = map.getSource('src-unc-geo') as maplibregl.GeoJSONSource | undefined
      if (uncSrc) {
        uncSrc.setData(uncertaintyPolygon)
      } else {
        map.addSource('src-unc-geo', {
          type: 'geojson',
          data: uncertaintyPolygon,
        })
        map.addLayer({
          id: 'src-unc-fill',
          type: 'fill',
          source: 'src-unc-geo',
          paint: {
            'fill-color': '#ef4444',
            'fill-opacity': 0.28,
          },
        })
        map.addLayer({
          id: 'src-unc-line',
          type: 'line',
          source: 'src-unc-geo',
          paint: {
            'line-color': '#ef4444',
            'line-width': 3,
            'line-dasharray': [4, 3],
          },
        })
      }
      if (map.getLayer('src-unc-fill')) {
        map.setLayoutProperty('src-unc-fill', 'visibility', showUncertainty ? 'visible' : 'none')
      }
      if (map.getLayer('src-unc-line')) {
        map.setLayoutProperty('src-unc-line', 'visibility', showUncertainty ? 'visible' : 'none')
      }

      // 2. Source: Backward Drift Trajectory Line & Step Breadcrumbs
      const driftFeatures: GeoJSON.Feature[] = [
        {
          type: 'Feature',
          properties: {},
          geometry: {
            type: 'LineString',
            coordinates: driftLineCoords,
          },
        },
      ]
      driftLineCoords.forEach((pt, i) => {
        driftFeatures.push({
          type: 'Feature',
          properties: { stepIndex: i },
          geometry: {
            type: 'Point',
            coordinates: pt,
          },
        })
      })

      const driftSrc = map.getSource('drift-traj-src') as maplibregl.GeoJSONSource | undefined
      if (driftSrc) {
        driftSrc.setData({
          type: 'FeatureCollection',
          features: driftFeatures,
        })
      } else {
        map.addSource('drift-traj-src', {
          type: 'geojson',
          data: {
            type: 'FeatureCollection',
            features: driftFeatures,
          },
        })
        // Black casing for high-contrast separation
        map.addLayer({
          id: 'drift-casing',
          type: 'line',
          source: 'drift-traj-src',
          filter: ['==', '$type', 'LineString'],
          paint: {
            'line-color': '#000000',
            'line-width': 8,
            'line-opacity': 0.8,
          },
        })
        // Thick bright amber drift line
        map.addLayer({
          id: 'drift-line',
          type: 'line',
          source: 'drift-traj-src',
          filter: ['==', '$type', 'LineString'],
          paint: {
            'line-color': '#f59e0b',
            'line-width': 5,
          },
        })
        // Step markers
        map.addLayer({
          id: 'drift-steps',
          type: 'circle',
          source: 'drift-traj-src',
          filter: ['==', '$type', 'Point'],
          paint: {
            'circle-color': '#fbbf24',
            'circle-radius': 5,
            'circle-stroke-color': '#000000',
            'circle-stroke-width': 2,
          },
        })
      }
      if (map.getLayer('drift-casing')) {
        map.setLayoutProperty('drift-casing', 'visibility', showDrift ? 'visible' : 'none')
      }
      if (map.getLayer('drift-line')) {
        map.setLayoutProperty('drift-line', 'visibility', showDrift ? 'visible' : 'none')
      }
      if (map.getLayer('drift-steps')) {
        map.setLayoutProperty('drift-steps', 'visibility', showDrift ? 'visible' : 'none')
      }

      // 2b. Source: Forward Drift Trajectory Line & Step Markers (Step 11)
      if (forwardLineCoords.length > 1) {
        const fwdFeatures: GeoJSON.Feature[] = [
          {
            type: 'Feature',
            properties: {},
            geometry: {
              type: 'LineString',
              coordinates: forwardLineCoords,
            },
          },
        ]
        forwardLineCoords.forEach((pt, i) => {
          fwdFeatures.push({
            type: 'Feature',
            properties: { stepIndex: i },
            geometry: {
              type: 'Point',
              coordinates: pt,
            },
          })
        })

        const fwdSrc = map.getSource('fwd-traj-src') as maplibregl.GeoJSONSource | undefined
        if (fwdSrc) {
          fwdSrc.setData({
            type: 'FeatureCollection',
            features: fwdFeatures,
          })
        } else {
          map.addSource('fwd-traj-src', {
            type: 'geojson',
            data: {
              type: 'FeatureCollection',
              features: fwdFeatures,
            },
          })
          // Casing
          map.addLayer({
            id: 'fwd-drift-casing',
            type: 'line',
            source: 'fwd-traj-src',
            filter: ['==', '$type', 'LineString'],
            paint: {
              'line-color': '#000000',
              'line-width': 7,
              'line-opacity': 0.8,
            },
          })
          // Vibrant cyan dashed forward line
          map.addLayer({
            id: 'fwd-drift-line',
            type: 'line',
            source: 'fwd-traj-src',
            filter: ['==', '$type', 'LineString'],
            paint: {
              'line-color': '#06b6d4',
              'line-width': 4.5,
              'line-dasharray': [3, 2],
            },
          })
          // Step circles
          map.addLayer({
            id: 'fwd-drift-steps',
            type: 'circle',
            source: 'fwd-traj-src',
            filter: ['==', '$type', 'Point'],
            paint: {
              'circle-color': '#22d3ee',
              'circle-radius': 4.5,
              'circle-stroke-color': '#000000',
              'circle-stroke-width': 2,
            },
          })
        }
        if (map.getLayer('fwd-drift-casing')) {
          map.setLayoutProperty('fwd-drift-casing', 'visibility', showForwardDrift ? 'visible' : 'none')
        }
        if (map.getLayer('fwd-drift-line')) {
          map.setLayoutProperty('fwd-drift-line', 'visibility', showForwardDrift ? 'visible' : 'none')
        }
        if (map.getLayer('fwd-drift-steps')) {
          map.setLayoutProperty('fwd-drift-steps', 'visibility', showForwardDrift ? 'visible' : 'none')
        }
      }

      // 3. Source: AIS Candidate Tracks & Position Breadcrumbs
      candidateTrackData.forEach((ct, idx) => {
        if (!ct.hasPositions) return
        const srcId = `vessel-track-src-${idx}`
        const vFeatures: GeoJSON.Feature[] = [
          {
            type: 'Feature',
            properties: { vesselId: ct.vessel.vessel_id },
            geometry: {
              type: 'LineString',
              coordinates: ct.positions.map((p) => [p.lon, p.lat]),
            },
          },
        ]
        ct.positions.forEach((p, pIdx) => {
          vFeatures.push({
            type: 'Feature',
            properties: { vesselId: ct.vessel.vessel_id, pIndex: pIdx },
            geometry: {
              type: 'Point',
              coordinates: [p.lon, p.lat],
            },
          })
        })

        const vSrc = map.getSource(srcId) as maplibregl.GeoJSONSource | undefined
        if (vSrc) {
          vSrc.setData({ type: 'FeatureCollection', features: vFeatures })
        } else {
          map.addSource(srcId, {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: vFeatures },
          })
          // Black casing
          map.addLayer({
            id: `vessel-casing-${idx}`,
            type: 'line',
            source: srcId,
            filter: ['==', '$type', 'LineString'],
            paint: {
              'line-color': '#000000',
              'line-width': 7,
              'line-opacity': 0.8,
            },
          })
          // Vibrant track line
          map.addLayer({
            id: `vessel-line-${idx}`,
            type: 'line',
            source: srcId,
            filter: ['==', '$type', 'LineString'],
            paint: {
              'line-color': ct.color,
              'line-width': 4.5,
            },
          })
          // Position breadcrumbs
          map.addLayer({
            id: `vessel-points-${idx}`,
            type: 'circle',
            source: srcId,
            filter: ['==', '$type', 'Point'],
            paint: {
              'circle-color': ct.color,
              'circle-radius': 5,
              'circle-stroke-color': '#0f172a',
              'circle-stroke-width': 2,
            },
          })
        }

        const vis = showAis ? 'visible' : 'none'
        if (map.getLayer(`vessel-casing-${idx}`)) map.setLayoutProperty(`vessel-casing-${idx}`, 'visibility', vis)
        if (map.getLayer(`vessel-line-${idx}`)) map.setLayoutProperty(`vessel-line-${idx}`, 'visibility', vis)
        if (map.getLayer(`vessel-points-${idx}`)) map.setLayoutProperty(`vessel-points-${idx}`, 'visibility', vis)
      })

      // 4. Metocean Vectors along drift
      if (hasWindVectors || hasCurrentVectors) {
        const vectorFeatures: GeoJSON.Feature[] = []
        backwardSteps.forEach((s) => {
          if (showWind && s.u_wind_ms != null && s.v_wind_ms != null) {
            // Scale: 1 m/s = 0.006 degrees
            const endLon = s.lon + s.u_wind_ms * 0.006
            const endLat = s.lat + s.v_wind_ms * 0.006
            vectorFeatures.push({
              type: 'Feature',
              properties: { type: 'wind' },
              geometry: {
                type: 'LineString',
                coordinates: [
                  [s.lon, s.lat],
                  [endLon, endLat],
                ],
              },
            })
          }
          if (showCurrents && s.u_current_ms != null && s.v_current_ms != null) {
            // Scale: 1 m/s = 0.06 degrees
            const endLon = s.lon + s.u_current_ms * 0.06
            const endLat = s.lat + s.v_current_ms * 0.06
            vectorFeatures.push({
              type: 'Feature',
              properties: { type: 'current' },
              geometry: {
                type: 'LineString',
                coordinates: [
                  [s.lon, s.lat],
                  [endLon, endLat],
                ],
              },
            })
          }
        })

        const metSrc = map.getSource('metocean-vec-src') as maplibregl.GeoJSONSource | undefined
        if (metSrc) {
          metSrc.setData({ type: 'FeatureCollection', features: vectorFeatures })
        } else {
          map.addSource('metocean-vec-src', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: vectorFeatures },
          })
          map.addLayer({
            id: 'metocean-wind-lines',
            type: 'line',
            source: 'metocean-vec-src',
            filter: ['==', ['get', 'type'], 'wind'],
            paint: {
              'line-color': '#38bdf8',
              'line-width': 2.5,
            },
          })
          map.addLayer({
            id: 'metocean-curr-lines',
            type: 'line',
            source: 'metocean-vec-src',
            filter: ['==', ['get', 'type'], 'current'],
            paint: {
              'line-color': '#34d399',
              'line-width': 2.5,
            },
          })
        }
      }

      // 5. SAR ↔ AIS Surveillance Association Vectors
      if (sarSurveillance && surveillanceAssociations.length > 0) {
        const assocFeatures: GeoJSON.Feature[] = surveillanceAssociations.map((assoc, idx) => {
          const tLon = assoc.target?.lon ?? assoc.target_lon!
          const tLat = assoc.target?.lat ?? assoc.target_lat!
          return {
            type: 'Feature',
            properties: {
              id: assoc.association_id || assoc.correlation_id || `assoc-${idx}`,
              classification: assoc.classification,
              separation_m: assoc.spatial_separation_m ?? assoc.distance_meters,
            },
            geometry: {
              type: 'LineString',
              coordinates: [
                [tLon, tLat],
                [assoc.ais_lon!, assoc.ais_lat!],
              ],
            },
          }
        })

        const assocSrc = map.getSource('sar-ais-assoc-src') as maplibregl.GeoJSONSource | undefined
        if (assocSrc) {
          assocSrc.setData({ type: 'FeatureCollection', features: assocFeatures })
        } else {
          map.addSource('sar-ais-assoc-src', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: assocFeatures },
          })
          map.addLayer({
            id: 'sar-ais-assoc-casing',
            type: 'line',
            source: 'sar-ais-assoc-src',
            paint: {
              'line-color': '#000000',
              'line-width': 5,
              'line-opacity': 0.7,
            },
          })
          map.addLayer({
            id: 'sar-ais-assoc-lines',
            type: 'line',
            source: 'sar-ais-assoc-src',
            paint: {
              'line-color': '#c084fc',
              'line-width': 2.5,
              'line-dasharray': [3, 2],
            },
          })
        }

        const vis = showSurveillance ? 'visible' : 'none'
        if (map.getLayer('sar-ais-assoc-casing')) map.setLayoutProperty('sar-ais-assoc-casing', 'visibility', vis)
        if (map.getLayer('sar-ais-assoc-lines')) map.setLayoutProperty('sar-ais-assoc-lines', 'visibility', vis)
      }
    },
    [
      uncertaintyPolygon,
      showUncertainty,
      driftLineCoords,
      showDrift,
      candidateTrackData,
      showAis,
      hasWindVectors,
      hasCurrentVectors,
      backwardSteps,
      showWind,
      showCurrents,
      showSurveillance,
      sarSurveillance,
      surveillanceAssociations,
    ]
  )

  // Focus Evidence Button Handler: fits camera directly to scientific evidence bounding box
  const handleFocusEvidence = useCallback(() => {
    if (!mapRef.current) return
    try {
      mapRef.current.fitBounds(
        [
          [evidenceBounds.west, evidenceBounds.south],
          [evidenceBounds.east, evidenceBounds.north],
        ],
        { padding: 50, duration: 1000 }
      )
    } catch {
      // no-op
    }
  }, [evidenceBounds])

  const mapLoadedRef = useRef(false)

  // Initialize MapLibre ONCE on component mount
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    let mapInstance: MapLibreMap | null = null
    try {
      mapInstance = new maplibregl.Map({
        container: containerRef.current,
        style: MAP_STYLE,
        bounds: [
          [evidenceBounds.west, evidenceBounds.south],
          [evidenceBounds.east, evidenceBounds.north],
        ],
        fitBoundsOptions: { padding: 50 },
        attributionControl: false,
      })
      mapRef.current = mapInstance

      // Add scale control to bottom-left (metric km)
      mapInstance.addControl(
        new maplibregl.ScaleControl({ maxWidth: 160, unit: 'metric' }),
        'bottom-left'
      )

      // Add compass navigation control
      mapInstance.addControl(
        new maplibregl.NavigationControl({ showCompass: true, showZoom: false }),
        'top-right'
      )

      mapInstance.on('error', () => {
        // Fallback to SVG if WebGL or style failed
        setUseFallbackSvg(true)
      })

      mapInstance.on('load', () => {
        mapLoadedRef.current = true
        setMapLoaded(true)
      })
    } catch {
      setUseFallbackSvg(true)
    }

    return () => {
      clearMarkers()
      if (mapInstance) {
        try {
          mapInstance.remove()
        } catch {
          // ignore
        }
        mapRef.current = null
      }
      mapLoadedRef.current = false
      setMapLoaded(false)
    }
  }, []) // Mount once!

  // Re-sync layers and markers whenever map is loaded and data/toggles change
  useEffect(() => {
    const map = mapRef.current
    if (!map || !_mapLoaded) return

    const applySync = () => {
      try {
        syncMapLayers(map)
        syncHtmlMarkers(map)
      } catch {
        // ignore
      }
    }

    if (!map.isStyleLoaded()) {
      map.once('styledata', applySync)
      return () => {
        map.off('styledata', applySync)
      }
    }

    applySync()
  }, [_mapLoaded, syncMapLayers, syncHtmlMarkers])

  // Automatically fit bounds whenever evidence bounds change and map is ready
  useEffect(() => {
    const map = mapRef.current
    if (!map || !_mapLoaded) return
    try {
      map.fitBounds(
        [
          [evidenceBounds.west, evidenceBounds.south],
          [evidenceBounds.east, evidenceBounds.north],
        ],
        { padding: 50, duration: 600 }
      )
    } catch {
      // ignore
    }
  }, [_mapLoaded, evidenceBounds])

  const handleZoomIn = () => {
    if (mapRef.current) {
      try {
        mapRef.current.zoomIn()
      } catch {
        // no-op
      }
    }
  }

  const handleZoomOut = () => {
    if (mapRef.current) {
      try {
        mapRef.current.zoomOut()
      } catch {
        // no-op
      }
    }
  }

  // SVG Fallback Projection Helper (for headless JSDOM / unit tests)
  const project = (lon: number, lat: number, width = 800, height = 480) => {
    const x = ((lon - evidenceBounds.west) / (evidenceBounds.east - evidenceBounds.west || 0.001)) * width
    const y = ((evidenceBounds.north - lat) / (evidenceBounds.north - evidenceBounds.south || 0.001)) * height
    return { x, y }
  }

  return (
    <div className="re-attribution-map-card" data-testid="attribution-map">
      {/* Map Header with Title & Layer Toggles */}
      <div className="re-map-toolbar">
        <div className="re-map-heading">
          <div className="re-map-title">
            <MapPin size={18} className="re-accent-icon" />
            <span>Interactive Attribution Map &amp; Evidence Reconstruction</span>
          </div>
          <span className="re-badge re-badge-live">STAGE D3 + F3 SCIENTIFIC TRACE</span>
        </div>

        {/* Layer Visibility Toggles */}
        <div className="re-layer-toggles" role="toolbar" aria-label="Map layer controls">
          <label className="re-toggle-label">
            <input
              type="checkbox"
              checked={showDrift}
              onChange={(e) => setShowDrift(e.target.checked)}
            />
            <span className="re-toggle-text">Show drift trajectory</span>
          </label>

          {forwardLineCoords.length > 1 && (
            <label className="re-toggle-label" data-testid="forward-drift-toggle">
              <input
                type="checkbox"
                checked={showForwardDrift}
                onChange={(e) => setShowForwardDrift(e.target.checked)}
              />
              <span className="re-toggle-text" style={{ color: '#22d3ee' }}>Show forward prediction</span>
            </label>
          )}

          <label className="re-toggle-label">
            <input
              type="checkbox"
              checked={showAis}
              onChange={(e) => setShowAis(e.target.checked)}
            />
            <span className="re-toggle-text">Show AIS tracks</span>
          </label>

          <label className="re-toggle-label">
            <input
              type="checkbox"
              checked={showUncertainty}
              onChange={(e) => setShowUncertainty(e.target.checked)}
            />
            <span className="re-toggle-text">Show source uncertainty</span>
          </label>

          <label className="re-toggle-label" title={!hasWindVectors ? 'Wind vectors not exposed in current run' : ''}>
            <input
              type="checkbox"
              checked={showWind}
              onChange={(e) => setShowWind(e.target.checked)}
            />
            <span className="re-toggle-text">Show wind</span>
          </label>

          <label className="re-toggle-label" title={!hasCurrentVectors ? 'Current vectors not exposed in current run' : ''}>
            <input
              type="checkbox"
              checked={showCurrents}
              onChange={(e) => setShowCurrents(e.target.checked)}
            />
            <span className="re-toggle-text">Show currents</span>
          </label>

          {sarSurveillance && (
            <label className="re-toggle-label" data-testid="surveillance-layer-toggle">
              <input
                type="checkbox"
                checked={showSurveillance}
                onChange={(e) => setShowSurveillance(e.target.checked)}
              />
              <span className="re-toggle-text" style={{ color: '#c084fc' }}>Show surveillance</span>
            </label>
          )}

          <button
            type="button"
            className="re-btn-toolbar-focus"
            onClick={handleFocusEvidence}
            title="Focus camera directly on scientific evidence"
          >
            <Compass size={14} />
            <span>Focus Evidence</span>
          </button>
        </div>
      </div>

      {/* Main Map Viewport & Overlays */}
      <div className="re-map-viewport-container">
        {/* MapLibre DOM Container */}
        <div
          ref={containerRef}
          className="re-maplibre-canvas"
          style={{ display: useFallbackSvg ? 'none' : 'block' }}
        />

        {/* Robust SVG Fallback Canvas for JSDOM and Headless Environments */}
        {useFallbackSvg && (
          <div className="re-svg-map-fallback">
            <svg
              viewBox="0 0 800 480"
              className="re-svg-canvas"
              aria-label="Reconstructed source and AIS overlay"
            >
              <defs>
                <pattern id={`grid-${popupIdPrefix}`} width="50" height="50" patternUnits="userSpaceOnUse">
                  <path d="M 50 0 L 0 0 0 50" fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
                </pattern>
              </defs>

              <rect width="800" height="480" fill="#09141d" />
              <rect width="800" height="480" fill={`url(#grid-${popupIdPrefix})`} />

              {/* 0. Detected Slick Polygon (SVG fallback) */}
              {(() => {
                const slickGeo = observation.slickGeometry
                if (slickGeo && typeof slickGeo === 'object' && 'coordinates' in slickGeo) {
                  const rings = (slickGeo as { coordinates: [number, number][][] }).coordinates
                  if (rings && rings.length > 0 && Array.isArray(rings[0])) {
                    const pts = rings[0].map((c) => {
                      const pt = project(c[0], c[1])
                      return `${pt.x},${pt.y}`
                    })
                    return (
                      <g data-testid="slick-polygon-layer">
                        <polygon
                          points={pts.join(' ')}
                          fill="rgba(6, 182, 212, 0.35)"
                          stroke="#22d3ee"
                          strokeWidth="2.5"
                        />
                      </g>
                    )
                  }
                }
                return null
              })()}

              {/* 1. Reconstructed Source Uncertainty Zone */}
              {showUncertainty && (
                <g data-testid="source-uncertainty-zone">
                  {(() => {
                    const centerPt = project(reconstructedSource.lon, reconstructedSource.lat)
                    const edgeLon =
                      reconstructedSource.lon +
                      (reconstructedSource.radiusM / 1000 / (111.32 * Math.cos((reconstructedSource.lat * Math.PI) / 180)))
                    const edgePt = project(edgeLon, reconstructedSource.lat)
                    const radiusPx = Math.max(Math.abs(edgePt.x - centerPt.x), 25)

                    return (
                      <>
                        <circle
                          cx={centerPt.x}
                          cy={centerPt.y}
                          r={radiusPx}
                          fill="rgba(239, 68, 68, 0.28)"
                          stroke="#ef4444"
                          strokeWidth="3"
                          strokeDasharray="4 3"
                        />
                        <text
                          x={centerPt.x}
                          y={centerPt.y - radiusPx - 8}
                          textAnchor="middle"
                          fill="#f87171"
                          fontSize="11"
                          fontWeight="bold"
                          fontFamily="sans-serif"
                        >
                          SOURCE UNCERTAINTY — {(reconstructedSource.radiusM / 1000).toFixed(1)} km
                        </text>
                      </>
                    )
                  })()}
                </g>
              )}

              {/* 2. Backward Drift Trajectory Line */}
              {showDrift && (
                <g data-testid="backward-drift-trajectory">
                  {(() => {
                    const points = driftLineCoords.map((c) => {
                      const pt = project(c[0], c[1])
                      return `${pt.x},${pt.y}`
                    })
                    return (
                      <>
                        <polyline
                          points={points.join(' ')}
                          fill="none"
                          stroke="#000000"
                          strokeWidth="8"
                          strokeOpacity="0.8"
                        />
                        <polyline
                          points={points.join(' ')}
                          fill="none"
                          stroke="#f59e0b"
                          strokeWidth="5"
                        />
                        {driftLineCoords.map((c, i) => {
                          const pt = project(c[0], c[1])
                          return (
                            <circle
                              key={i}
                              cx={pt.x}
                              cy={pt.y}
                              r={i === 0 || i === driftLineCoords.length - 1 ? 6 : 4}
                              fill="#fbbf24"
                              stroke="#000000"
                              strokeWidth="2"
                            />
                          )
                        })}
                      </>
                    )
                  })()}
                </g>
              )}

              {/* 2b. Forward Drift Predicted Trajectory (SVG fallback) */}
              {showForwardDrift && forwardLineCoords.length > 1 && (
                <g data-testid="forward-drift-layer" className="re-svg-fwd-drift">
                  {(() => {
                    const points = forwardLineCoords.map((c) => {
                      const pt = project(c[0], c[1])
                      return `${pt.x},${pt.y}`
                    })
                    const effectiveSteps = forwardSteps.length > 0 ? forwardSteps : (forwardPrediction?.steps ?? [])
                    return (
                      <>
                        <polyline
                          points={points.join(' ')}
                          fill="none"
                          stroke="#000000"
                          strokeWidth="7"
                          strokeOpacity="0.8"
                        />
                        <polyline
                          data-testid="forward-drift-polyline"
                          points={points.join(' ')}
                          fill="none"
                          stroke="#06b6d4"
                          strokeWidth="4.5"
                          strokeDasharray="6,4"
                        />
                        {forwardLineCoords.map((c, i) => {
                          const pt = project(c[0], c[1])
                          const isLast = i === forwardLineCoords.length - 1
                          const stepObj = effectiveSteps[i - 1] ?? null
                          return (
                            <circle
                              key={`fwd-pt-${i}`}
                              data-testid={`forward-step-marker-${i}`}
                              cx={pt.x}
                              cy={pt.y}
                              r={isLast ? 6.5 : 4}
                              fill={isLast ? '#22d3ee' : '#0891b2'}
                              stroke="#ffffff"
                              strokeWidth="1.5"
                              style={{ cursor: stepObj ? 'pointer' : 'default' }}
                              onClick={() => {
                                if (stepObj) {
                                  setActivePopup({ type: 'forward_step', step: stepObj, index: i - 1 })
                                }
                              }}
                            />
                          )
                        })}
                        {(() => {
                          const lastPt = project(
                            forwardLineCoords[forwardLineCoords.length - 1][0],
                            forwardLineCoords[forwardLineCoords.length - 1][1]
                          )
                          return (
                            <g
                              data-testid="forward-predicted-destination"
                              onClick={() => {
                                if (effectiveSteps.length > 0) {
                                  setActivePopup({
                                    type: 'forward_step',
                                    step: effectiveSteps[effectiveSteps.length - 1],
                                    index: effectiveSteps.length - 1,
                                  })
                                }
                              }}
                              style={{ cursor: 'pointer' }}
                            >
                              <circle cx={lastPt.x} cy={lastPt.y} r="12" fill="rgba(6, 182, 212, 0.3)" />
                              <circle cx={lastPt.x} cy={lastPt.y} r="6" fill="#06b6d4" stroke="#ffffff" strokeWidth="2" />
                              <text
                                x={lastPt.x + 10}
                                y={lastPt.y - 6}
                                fill="#22d3ee"
                                fontSize="11"
                                fontWeight="bold"
                                fontFamily="sans-serif"
                              >
                                PREDICTED FUTURE
                              </text>
                            </g>
                          )
                        })()}
                      </>
                    )
                  })()}
                </g>
              )}

              {/* 3. AIS Candidate Tracks */}
              {showAis &&
                candidateTrackData.map((ct) => {
                  if (!ct.hasPositions) return null
                  const isSelected = selectedVesselId === ct.vessel.vessel_id
                  const points = ct.positions.map((p) => {
                    const pt = project(p.lon, p.lat)
                    return `${pt.x},${pt.y}`
                  })

                  return (
                    <g
                      key={ct.vessel.vessel_id}
                      data-testid={`vessel-track-${ct.vessel.vessel_id}`}
                      className="re-svg-vessel-track"
                      onClick={() => {
                        onSelectVessel(ct.vessel.vessel_id)
                        setActivePopup({ type: 'vessel', vessel: ct.vessel })
                      }}
                      style={{ cursor: 'pointer' }}
                    >
                      <polyline
                        points={points.join(' ')}
                        fill="none"
                        stroke="#000000"
                        strokeWidth="8"
                        strokeOpacity="0.8"
                      />
                      <polyline
                        points={points.join(' ')}
                        fill="none"
                        stroke={ct.color}
                        strokeWidth={isSelected ? 6 : 4.5}
                      />
                      {ct.positions.map((p, pIdx) => {
                        const pt = project(p.lon, p.lat)
                        return (
                          <circle
                            key={pIdx}
                            cx={pt.x}
                            cy={pt.y}
                            r={5}
                            fill={ct.color}
                            stroke="#0f172a"
                            strokeWidth="2"
                          />
                        )
                      })}
                      {ct.positions.length > 0 && (() => {
                        const lastPt = project(
                          ct.positions[ct.positions.length - 1].lon,
                          ct.positions[ct.positions.length - 1].lat
                        )
                        return (
                          <text
                            x={lastPt.x + 8}
                            y={lastPt.y - 6}
                            fill={ct.color}
                            fontSize="11"
                            fontWeight="bold"
                            fontFamily="monospace"
                          >
                            {ct.vessel.vessel_name ?? ct.vessel.mmsi}
                          </text>
                        )
                      })()}
                    </g>
                  )
                })}

              {/* 4. Reconstructed Source Center Marker */}
              {(() => {
                const pt = project(reconstructedSource.lon, reconstructedSource.lat)
                return (
                  <g
                    data-testid="reconstructed-source-marker"
                    onClick={() => setActivePopup({ type: 'source' })}
                    style={{ cursor: 'pointer' }}
                  >
                    <circle cx={pt.x} cy={pt.y} r="10" fill="#ef4444" stroke="#ffffff" strokeWidth="2.5" />
                    <circle cx={pt.x} cy={pt.y} r="4" fill="#ffffff" />
                    <text
                      x={pt.x + 14}
                      y={pt.y + 4}
                      fill="#fca5a5"
                      fontSize="12"
                      fontWeight="bold"
                      fontFamily="sans-serif"
                    >
                      RECONSTRUCTED SOURCE
                    </text>
                  </g>
                )
              })()}

              {/* 5. Observed Spill / Slick Marker */}
              {(() => {
                const pt = project(observation.lon, observation.lat)
                return (
                  <g
                    data-testid="observation-marker"
                    onClick={() => setActivePopup({ type: 'observation' })}
                    style={{ cursor: 'pointer' }}
                  >
                    <circle cx={pt.x} cy={pt.y} r="14" fill="rgba(56, 189, 248, 0.35)" />
                    <circle cx={pt.x} cy={pt.y} r="8" fill="#0284c7" stroke="#ffffff" strokeWidth="2.5" />
                    <text
                      x={pt.x + 14}
                      y={pt.y + 4}
                      fill="#38bdf8"
                      fontSize="12"
                      fontWeight="bold"
                      fontFamily="sans-serif"
                    >
                      OBSERVED SPILL
                    </text>
                  </g>
                )
              })()}

              {/* 6. SAR ↔ AIS Surveillance (SVG fallback) */}
              {showSurveillance && sarSurveillance && (
                <g data-testid="sar-surveillance-svg-layer">
                  {/* Association lines */}
                  {surveillanceAssociations.map((assoc, idx) => {
                    const tLon = assoc.target?.lon ?? assoc.target_lon!
                    const tLat = assoc.target?.lat ?? assoc.target_lat!
                    const tPt = project(tLon, tLat)
                    const aPt = project(assoc.ais_lon!, assoc.ais_lat!)
                    return (
                      <g
                        key={`sar-line-${assoc.association_id || assoc.correlation_id || idx}`}
                        data-testid="sar-ais-association-line"
                        onClick={() => setActivePopup({ type: 'sar_ais_assoc', association: assoc })}
                        style={{ cursor: 'pointer' }}
                      >
                        <line
                          x1={tPt.x}
                          y1={tPt.y}
                          x2={aPt.x}
                          y2={aPt.y}
                          stroke="#000000"
                          strokeWidth="5"
                          strokeOpacity="0.7"
                        />
                        <line
                          x1={tPt.x}
                          y1={tPt.y}
                          x2={aPt.x}
                          y2={aPt.y}
                          stroke="#c084fc"
                          strokeWidth="2.5"
                          strokeDasharray="4 3"
                        />
                      </g>
                    )
                  })}

                  {/* Bright Radar Targets */}
                  {surveillanceTargets.map((target) => {
                    const pt = project(target.lon, target.lat)
                    const d = 9
                    return (
                      <g
                        key={`svg-target-${target.target_id}`}
                        data-testid="bright-radar-target-marker"
                        onClick={() => setActivePopup({ type: 'radar_target', target })}
                        style={{ cursor: 'pointer' }}
                      >
                        <polygon
                          points={`${pt.x},${pt.y - d} ${pt.x + d},${pt.y} ${pt.x},${pt.y + d} ${pt.x - d},${pt.y}`}
                          fill="#f59e0b"
                          stroke="#ffffff"
                          strokeWidth="2"
                        />
                        <text
                          x={pt.x + 12}
                          y={pt.y + 4}
                          fill="#fbbf24"
                          fontSize="11"
                          fontWeight="bold"
                          fontFamily="sans-serif"
                        >
                          ◆ Bright Radar Target ({target.target_id})
                        </text>
                      </g>
                    )
                  })}

                  {/* AIS Surveillance Positions */}
                  {surveillanceAisObs.map((assoc, idx) => {
                    const pt = project(assoc.ais_lon!, assoc.ais_lat!)
                    return (
                      <g
                        key={`svg-ais-obs-${assoc.correlation_id || idx}`}
                        data-testid="ais-observation-marker"
                        onClick={() => setActivePopup({ type: 'sar_ais_obs', association: assoc })}
                        style={{ cursor: 'pointer' }}
                      >
                        <circle cx={pt.x} cy={pt.y} r="8" fill="rgba(56, 189, 248, 0.2)" stroke="#38bdf8" strokeWidth="2.5" />
                        <circle cx={pt.x} cy={pt.y} r="3" fill="#38bdf8" />
                        <text
                          x={pt.x + (idx % 2 === 0 ? -120 : 12)}
                          y={pt.y + (idx % 2 === 0 ? -10 : 16)}
                          fill="#38bdf8"
                          fontSize="11"
                          fontWeight="bold"
                          fontFamily="sans-serif"
                        >
                          ○ AIS ({assoc.vessel_name ?? assoc.mmsi ?? 'Track'})
                        </text>
                      </g>
                    )
                  })}
                </g>
              )}
            </svg>
          </div>
        )}

        {/* Map Control Buttons (Focus Evidence, Zoom, Scale) */}
        <div className="re-map-controls-floating">
          <button
            type="button"
            className="re-control-btn re-btn-focus-evidence"
            onClick={handleFocusEvidence}
            title="Focus Evidence (Zoom to observation, source, and AIS tracks)"
            aria-label="Focus Evidence"
          >
            <Compass size={15} />
            <span>Focus Evidence</span>
          </button>
          <button
            type="button"
            className="re-control-btn"
            onClick={handleZoomIn}
            title="Zoom In"
            aria-label="Zoom In"
          >
            <ZoomIn size={16} />
          </button>
          <button
            type="button"
            className="re-control-btn"
            onClick={handleZoomOut}
            title="Zoom Out"
            aria-label="Zoom Out"
          >
            <ZoomOut size={16} />
          </button>
          <button
            type="button"
            className="re-control-btn"
            onClick={handleFocusEvidence}
            title="Reset to Evidence Bounds"
            aria-label="Reset View"
          >
            <Maximize2 size={16} />
          </button>
        </div>

        {/* Interactive Popup Modal */}
        {activePopup && (
          <div className="re-map-popup-card" role="dialog" aria-modal="false">
            <button
              type="button"
              className="re-popup-close"
              onClick={() => setActivePopup(null)}
              aria-label="Close details"
            >
              ×
            </button>

            {activePopup.type === 'observation' && (
              <div className="re-popup-content">
                <div className="re-popup-header re-popup-obs">
                  <MapPin size={14} />
                  <strong>Detected Oil Slick (SAR Observation)</strong>
                </div>
                <div className="re-popup-body">
                  <div className="re-popup-row">
                    <span>Observation Time:</span>
                    <strong>{observation.timestamp.replace('T', ' ')} UTC</strong>
                  </div>
                  <div className="re-popup-row">
                    <span>Coordinates:</span>
                    <code>
                      {observation.lat.toFixed(4)}°N, {observation.lon.toFixed(4)}°E
                    </code>
                  </div>
                  {observation.areaKm2 != null && (
                    <div className="re-popup-row">
                      <span>Estimated Area:</span>
                      <strong style={{ color: '#38bdf8' }}>{observation.areaKm2.toFixed(1)} km²</strong>
                    </div>
                  )}
                  {observation.dampingContrastDb != null && (
                    <div className="re-popup-row">
                      <span>Damping Contrast:</span>
                      <strong>{observation.dampingContrastDb.toFixed(1)} dB</strong>
                    </div>
                  )}
                  {observation.confidence != null && (
                    <div className="re-popup-row">
                      <span>Confidence:</span>
                      <strong>{(observation.confidence * 100).toFixed(0)}%</strong>
                    </div>
                  )}
                  {observation.detectionStatus && (
                    <div className="re-popup-row">
                      <span>Status:</span>
                      <span>{observation.detectionStatus}</span>
                    </div>
                  )}
                  <div className="re-popup-row">
                    <span>Sensor:</span>
                    <span>Sentinel-1 SAR (Copernicus)</span>
                  </div>
                </div>
              </div>
            )}

            {activePopup.type === 'source' && (
              <div className="re-popup-content">
                <div className="re-popup-header re-popup-source">
                  <Crosshair size={14} />
                  <strong>Reconstructed Source Zone</strong>
                </div>
                <div className="re-popup-body">
                  <div className="re-popup-row">
                    <span>Estimated Center:</span>
                    <code>
                      {reconstructedSource.lat.toFixed(4)}°N, {reconstructedSource.lon.toFixed(4)}°E
                    </code>
                  </div>
                  <div className="re-popup-row">
                    <span>Uncertainty Radius:</span>
                    <strong>{(reconstructedSource.radiusM / 1000).toFixed(1)} km</strong>
                  </div>
                  <div className="re-popup-row">
                    <span>Model:</span>
                    <span>leeway_euler_backward_v1</span>
                  </div>
                </div>
              </div>
            )}

            {activePopup.type === 'forward_step' && (
              <div className="re-popup-content" data-testid="forward-step-popup">
                <div className="re-popup-header" style={{ color: '#22d3ee', borderBottom: '1px solid #0891b2' }}>
                  <Compass size={14} />
                  <strong>Forward Drift Prediction (+{activePopup.step.step}h)</strong>
                </div>
                <div className="re-popup-body">
                  <div className="re-popup-row">
                    <span>Elapsed Prediction:</span>
                    <strong style={{ color: '#22d3ee' }}>+{activePopup.step.step} hours</strong>
                  </div>
                  {activePopup.step.timestamp && (
                    <div className="re-popup-row">
                      <span>Projected Time:</span>
                      <strong>{activePopup.step.timestamp.replace('T', ' ').slice(0, 19)} UTC</strong>
                    </div>
                  )}
                  <div className="re-popup-row">
                    <span>Predicted Coordinates:</span>
                    <code>
                      {activePopup.step.lat.toFixed(4)}°N, {activePopup.step.lon.toFixed(4)}°E
                    </code>
                  </div>
                  {activePopup.step.u_wind_ms != null && activePopup.step.v_wind_ms != null && (
                    <div className="re-popup-row">
                      <span>Wind Forcing (10m):</span>
                      <strong>
                        {Math.hypot(activePopup.step.u_wind_ms, activePopup.step.v_wind_ms).toFixed(1)} m/s
                      </strong>
                    </div>
                  )}
                  {activePopup.step.u_current_ms != null && activePopup.step.v_current_ms != null && (
                    <div className="re-popup-row">
                      <span>Ocean Current:</span>
                      <strong>
                        {Math.hypot(activePopup.step.u_current_ms, activePopup.step.v_current_ms).toFixed(2)} m/s
                      </strong>
                    </div>
                  )}
                  <div className="re-popup-row">
                    <span>Model:</span>
                    <span>leeway_euler_forward_v1 (α = 0.035)</span>
                  </div>
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8', fontStyle: 'italic', marginTop: '0.4rem' }}>
                    Deterministic model trajectory under supplied forcing. Not an observed future track.
                  </div>
                </div>
              </div>
            )}

            {activePopup.type === 'vessel' && (
              <div className="re-popup-content">
                <div className="re-popup-header re-popup-vessel">
                  <Ship size={14} />
                  <strong>{activePopup.vessel.vessel_name ?? 'Candidate Vessel'}</strong>
                  {activePopup.vessel.mmsi && <code>MMSI: {activePopup.vessel.mmsi}</code>}
                </div>
                <div className="re-popup-body">
                  <div className="re-popup-row">
                    <span>Evidence Consistency:</span>
                    <strong style={{ color: '#4ade80' }}>
                      {(activePopup.vessel.evidence_consistency_score * 100).toFixed(1)}%
                    </strong>
                  </div>
                  <div className="re-popup-row">
                    <span>Min Distance to Source:</span>
                    <strong>
                      {activePopup.vessel.min_source_distance_km != null
                        ? `${activePopup.vessel.min_source_distance_km.toFixed(1)} km`
                        : '—'}
                    </strong>
                  </div>
                  <div className="re-popup-row">
                    <span>Temporal Overlap:</span>
                    <strong>{activePopup.vessel.temporal_overlap_hours.toFixed(1)} h</strong>
                  </div>
                  <div className="re-popup-row">
                    <span>Trajectory Overlap:</span>
                    <strong>
                      {(activePopup.vessel.trajectory_overlap_fraction * 100).toFixed(0)}%
                    </strong>
                  </div>
                  <div className="re-popup-row">
                    <span>AIS Positions:</span>
                    <strong>{activePopup.vessel.ais_position_count}</strong>
                  </div>
                </div>
              </div>
            )}

            {activePopup.type === 'radar_target' && (
              <div className="re-popup-content" data-testid="radar-target-popup">
                <div className="re-popup-header" style={{ color: '#fbbf24', borderBottom: '1px solid #f59e0b' }}>
                  <Compass size={14} />
                  <strong>Bright Radar Target ({activePopup.target.target_id})</strong>
                </div>
                <div className="re-popup-body">
                  <div className="re-popup-row">
                    <span>Coordinates:</span>
                    <code>
                      {activePopup.target.lat.toFixed(4)}°N, {activePopup.target.lon.toFixed(4)}°E
                    </code>
                  </div>
                  <div className="re-popup-row">
                    <span>Peak Backscatter:</span>
                    <strong style={{ color: '#fbbf24' }}>{activePopup.target.peak_backscatter_db.toFixed(2)} dB</strong>
                  </div>
                  <div className="re-popup-row">
                    <span>Local Clutter Mean:</span>
                    <strong>{activePopup.target.local_clutter_mean_db.toFixed(2)} dB</strong>
                  </div>
                  <div className="re-popup-row">
                    <span>Target-to-Clutter Ratio (TCR):</span>
                    <strong style={{ color: '#4ade80' }}>{activePopup.target.target_to_clutter_ratio_db.toFixed(2)} dB</strong>
                  </div>
                  {activePopup.target.apparent_major_extent_m != null && (
                    <div className="re-popup-row">
                      <span>Apparent Radar Extent:</span>
                      <strong>
                        {activePopup.target.apparent_major_extent_m.toFixed(0)}m × {activePopup.target.apparent_minor_extent_m != null ? `${activePopup.target.apparent_minor_extent_m.toFixed(0)}m` : '—'}
                      </strong>
                    </div>
                  )}
                  <div className="re-popup-row">
                    <span>Pixel Count:</span>
                    <strong>{activePopup.target.pixel_count} px</strong>
                  </div>
                  {activePopup.target.detection_confidence != null && (
                    <div className="re-popup-row">
                      <span>Detection Confidence:</span>
                      <strong>{(activePopup.target.detection_confidence * 100).toFixed(0)}%</strong>
                    </div>
                  )}
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8', fontStyle: 'italic', marginTop: '0.4rem', borderTop: '1px solid rgba(148, 163, 184, 0.2)', paddingTop: '0.35rem' }}>
                    Bright radar targets are SAR scattering detections and are not automatically classified as vessels.
                  </div>
                </div>
              </div>
            )}

            {activePopup.type === 'sar_ais_obs' && (() => {
              const assoc = activePopup.association
              const prov = assoc.ais_position_provenance ?? assoc.ais_alignment_method ?? 'UNAVAILABLE'
              const timeOffset = assoc.temporal_delta_seconds ?? assoc.ais_time_offset_seconds
              return (
                <div className="re-popup-content" data-testid="sar-ais-obs-popup">
                  <div className="re-popup-header" style={{ color: '#38bdf8', borderBottom: '1px solid #0284c7' }}>
                    <Ship size={14} />
                    <strong>AIS Observation ({assoc.vessel_name ?? assoc.mmsi ?? 'Vessel'})</strong>
                  </div>
                  <div className="re-popup-body">
                    {assoc.mmsi && (
                      <div className="re-popup-row">
                        <span>MMSI:</span>
                        <code>{assoc.mmsi}</code>
                      </div>
                    )}
                    {assoc.vessel_type && (
                      <div className="re-popup-row">
                        <span>Vessel Type:</span>
                        <span>{assoc.vessel_type}</span>
                      </div>
                    )}
                    <div className="re-popup-row">
                      <span>AIS Coordinates:</span>
                      <code>
                        {assoc.ais_lat?.toFixed(4)}°N, {assoc.ais_lon?.toFixed(4)}°E
                      </code>
                    </div>
                    {assoc.ais_timestamp && (
                      <div className="re-popup-row">
                        <span>Timestamp:</span>
                        <strong>{assoc.ais_timestamp.replace('T', ' ').slice(0, 19)} UTC</strong>
                      </div>
                    )}
                    {timeOffset != null && (
                      <div className="re-popup-row">
                        <span>Time Offset:</span>
                        <strong>{Math.abs(timeOffset).toFixed(0)} s</strong>
                      </div>
                    )}
                    {assoc.ais_sog_knots != null && (
                      <div className="re-popup-row">
                        <span>Speed Over Ground (SOG):</span>
                        <strong>{assoc.ais_sog_knots.toFixed(1)} kn</strong>
                      </div>
                    )}
                    {assoc.ais_cog_degrees != null && (
                      <div className="re-popup-row">
                        <span>Course Over Ground (COG):</span>
                        <strong>{assoc.ais_cog_degrees.toFixed(0)}°</strong>
                      </div>
                    )}
                    <div className="re-popup-row">
                      <span>AIS Position Provenance:</span>
                      <strong style={{ color: prov === 'GENUINE_OBSERVATION' ? '#4ade80' : prov === 'TEMPORALLY_ALIGNED_FIX' ? '#38bdf8' : '#facc15' }}>
                        {prov}
                      </strong>
                    </div>
                    {assoc.classification && (
                      <div className="re-popup-row">
                        <span>Surveillance State:</span>
                        <strong style={{ color: '#38bdf8' }}>
                          {assoc.classification.replace(/_/g, ' ')}
                        </strong>
                      </div>
                    )}
                  </div>
                </div>
              )
            })()}

            {activePopup.type === 'sar_ais_assoc' && (
              <div className="re-popup-content" data-testid="sar-ais-assoc-popup">
                <div className="re-popup-header" style={{ color: '#c084fc', borderBottom: '1px solid #a855f7' }}>
                  <Compass size={14} />
                  <strong>SAR ↔ AIS Association</strong>
                </div>
                <div className="re-popup-body">
                  <div className="re-popup-row">
                    <span>Classification:</span>
                    <strong>{activePopup.association.classification.replace(/_/g, ' ')}</strong>
                  </div>
                  {activePopup.association.spatial_separation_m != null && (
                    <div className="re-popup-row">
                      <span>Spatial Separation:</span>
                      <strong>{activePopup.association.spatial_separation_m.toFixed(1)} m</strong>
                    </div>
                  )}
                  {activePopup.association.temporal_delta_seconds != null && (
                    <div className="re-popup-row">
                      <span>Temporal Difference:</span>
                      <strong>{Math.abs(activePopup.association.temporal_delta_seconds).toFixed(0)} s</strong>
                    </div>
                  )}
                  <div className="re-popup-row">
                    <span>Investigation Flag:</span>
                    <strong style={{ color: activePopup.association.investigation_flag ? '#f87171' : '#4ade80' }}>
                      {activePopup.association.investigation_flag ? 'Flagged for Review' : 'Nominal Gate Match'}
                    </strong>
                  </div>
                  {activePopup.association.findings_summary && (
                    <div style={{ fontSize: '0.74rem', color: '#cbd5e1', marginTop: '0.4rem' }}>
                      {activePopup.association.findings_summary}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Map Legend & Reconstructed Coordinates Footer */}
      <div className="re-map-footer">
        <div className="re-map-coordinates-bar">
          <div className="re-coord-item">
            <span className="re-coord-label">Source:</span>
            <span className="re-coord-value">
              {reconstructedSource.lat.toFixed(4)}°N, {reconstructedSource.lon.toFixed(4)}°E
            </span>
          </div>
          <div className="re-coord-item">
            <span className="re-coord-label">Uncertainty radius:</span>
            <span className="re-coord-value">
              {(reconstructedSource.radiusM / 1000).toFixed(1)} km
            </span>
          </div>
          <div className="re-coord-item">
            <span className="re-coord-label">Observation:</span>
            <span className="re-coord-value">
              {observation.lat.toFixed(4)}°N, {observation.lon.toFixed(4)}°E
            </span>
          </div>
        </div>

        {/* Legend Elements */}
        <div className="re-legend-bar">
          <div className="re-legend-entry">
            <span className="re-dot re-dot-obs" />
            <strong style={{ color: '#38bdf8' }}>Observed Spill / Slick</strong>
          </div>
          <div className="re-legend-entry">
            <span className="re-dot re-dot-source" />
            <strong style={{ color: '#ef4444' }}>Reconstructed Source Zone</strong>
          </div>
          <div className="re-legend-entry">
            <span className="re-line re-line-drift" />
            <strong style={{ color: '#f59e0b' }}>Backward Drift Trajectory</strong>
          </div>
          {forwardLineCoords.length > 1 && showForwardDrift && (
            <div className="re-legend-entry" data-testid="legend-forward-drift">
              <span
                className="re-line"
                style={{
                  background: '#06b6d4',
                  boxShadow: '0 0 6px rgba(6, 182, 212, 0.6)',
                  borderTop: '2px dashed #ffffff',
                }}
              />
              <strong style={{ color: '#22d3ee' }}>Forward Drift Prediction</strong>
            </div>
          )}
          {candidateTrackData.map((ct) => (
            <div
              key={ct.vessel.vessel_id}
              className={`re-legend-entry re-legend-clickable ${selectedVesselId === ct.vessel.vessel_id ? 're-legend-selected' : ''}`}
              onClick={() => {
                onSelectVessel(ct.vessel.vessel_id)
                setActivePopup({ type: 'vessel', vessel: ct.vessel })
              }}
            >
              <span className="re-line" style={{ background: ct.color }} />
              <strong style={{ color: ct.color }}>{ct.vessel.vessel_name ?? ct.vessel.mmsi}</strong>
              {!ct.hasPositions && <span className="re-tag-no-ais">(No AIS)</span>}
            </div>
          ))}
          {showWind && (
            <div className="re-legend-entry">
              <span className="re-arrow re-arrow-wind" />
              <span>ERA5 Wind Vector</span>
            </div>
          )}
          {showCurrents && (
            <div className="re-legend-entry">
              <span className="re-arrow re-arrow-curr" />
              <span>CMEMS Current Vector</span>
            </div>
          )}
          {sarSurveillance && (
            <>
              <div className="re-legend-entry" data-testid="legend-sar-target">
                <span style={{ color: '#f59e0b', fontSize: '0.85rem' }}>◆</span>
                <strong style={{ color: '#fbbf24' }}>Bright Radar Target</strong>
              </div>
              <div className="re-legend-entry" data-testid="legend-ais-obs">
                <span style={{ color: '#38bdf8', fontSize: '0.85rem' }}>○</span>
                <strong style={{ color: '#38bdf8' }}>AIS Observation</strong>
              </div>
              <div className="re-legend-entry" data-testid="legend-sar-ais-assoc">
                <span className="re-line" style={{ background: '#c084fc', borderTop: '2px dashed #ffffff' }} />
                <strong style={{ color: '#c084fc' }}>SAR ↔ AIS Association</strong>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
