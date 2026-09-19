import { useEffect, useRef, useState } from 'react'
import { Crosshair, Eye, EyeOff, Layers, Maximize2, Navigation, ScanLine, Ship, Waves, Wind, ZoomIn, ZoomOut } from 'lucide-react'
import * as maplibregl from 'maplibre-gl'
import type { Map as MapLibreMap } from 'maplibre-gl'
import type { Feature, FeatureCollection, LineString, Point, Polygon } from 'geojson'
import { mapData } from '../../data/mapData'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import type { DriftReconstructionData, MapLayerVisibility, ReconstructedAisData, VesselProperties } from '../../types/maris'
import type { BoundingBox } from '../../types/investigationApi'

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
const DEMO_BOUNDS: maplibregl.LngLatBoundsLike = [[9.08, 42.9], [9.75, 43.52]]

interface MapViewProps {
  layers: MapLayerVisibility
  selectedCandidate: string
  onSelectCandidate: (id: string) => void
  isDemoMode?: boolean
  isSimulationMode?: boolean
  simulationScenario?: SimulationScenario | null
  aoiBBox?: BoundingBox | null
  liveSpillGeometry?: Record<string, unknown> | null
  liveSpillCentroid?: { longitude: number; latitude: number } | null
  liveSourceZoneGeometry?: Record<string, unknown> | null
}

export function MapView({
  layers,
  selectedCandidate,
  onSelectCandidate,
  isDemoMode = false,
  isSimulationMode = false,
  simulationScenario,
  aoiBBox,
  liveSpillGeometry,
  liveSpillCentroid,
  liveSourceZoneGeometry,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const [mapError, setMapError] = useState(false)
  const [cursorCoords, setCursorCoords] = useState<string>('--.----° N, --.----° E')

  // Calculate effective initial bounds
  const effectiveBounds: maplibregl.LngLatBoundsLike =
    isSimulationMode && simulationScenario
      ? buildSimulationBounds(simulationScenario)
      : !isDemoMode && aoiBBox
        ? [
            [aoiBBox.west, aoiBBox.south],
            [aoiBBox.east, aoiBBox.north],
          ]
        : DEMO_BOUNDS

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return undefined

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      bounds: effectiveBounds,
      fitBoundsOptions: { padding: 45 },
    })
    mapRef.current = map
    map.on('error', () => setMapError(true))

    map.on('mousemove', (e) => {
      const lat = e.lngLat.lat
      const lng = e.lngLat.lng
      const latDir = lat >= 0 ? 'N' : 'S'
      const lngDir = lng >= 0 ? 'E' : 'W'
      setCursorCoords(
        `${Math.abs(lat).toFixed(4)}° ${latDir}, ${Math.abs(lng).toFixed(4)}° ${lngDir}`
      )
    })

    map.on('style.load', async () => {
      try {
        if (isDemoMode) {
          const [spillResponse, aisResponse, driftResponse] = await Promise.all([
            fetch(mapData.spillGeoJsonUrl),
            fetch(mapData.aisJsonUrl),
            fetch(mapData.driftJsonUrl),
          ])
          if (!spillResponse.ok) throw new Error(`Spill data request failed: ${spillResponse.status}`)
          if (!aisResponse.ok) throw new Error(`AIS data request failed: ${aisResponse.status}`)
          if (!driftResponse.ok) throw new Error(`Drift data request failed: ${driftResponse.status}`)
          const spillData = (await spillResponse.json()) as FeatureCollection<Polygon, Record<string, unknown>>
          const aisData = (await aisResponse.json()) as ReconstructedAisData
          const driftData = (await driftResponse.json()) as DriftReconstructionData
          addMapSourcesAndLayers(map, spillData, aisData, driftData)
        } else if (isSimulationMode && simulationScenario) {
          addSimulationMapSourcesAndLayers(map, simulationScenario)
        } else {
          initLiveMapSourcesAndLayers(map, liveSpillGeometry, liveSpillCentroid, liveSourceZoneGeometry)
        }
        setMapReady(true)
        requestAnimationFrame(() => map.resize())
      } catch {
        setMapError(true)
      }
    })

    map.on('mouseenter', 'vessel-markers', () => {
      map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', 'vessel-markers', () => {
      map.getCanvas().style.cursor = ''
    })

    const resizeObserver = new ResizeObserver(() => map.resize())
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      map.remove()
      mapRef.current = null
    }
  }, [isDemoMode, isSimulationMode, simulationScenario])

  // Update live map sources dynamically when live backend data updates
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || isDemoMode || isSimulationMode) return

    updateLiveSources(map, liveSpillGeometry, liveSpillCentroid, liveSourceZoneGeometry)
  }, [liveSpillGeometry, liveSpillCentroid, liveSourceZoneGeometry, mapReady, isDemoMode, isSimulationMode])

  // Update bounds when AOI changes in live mode
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || isDemoMode || isSimulationMode || !aoiBBox) return

    map.fitBounds(
      [
        [aoiBBox.west, aoiBBox.south],
        [aoiBBox.east, aoiBBox.north],
      ],
      { padding: 45, duration: 800 }
    )
  }, [aoiBBox, mapReady, isDemoMode, isSimulationMode])

  // Layer visibility toggle
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    setLayerVisibility(map, layers)
  }, [layers, mapReady])

  // Candidate selection highlighting
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    try {
      if (map.getLayer('vessel-tracks')) {
        const selectedExpression = [
          'case',
          ['==', ['get', 'vesselId'], selectedCandidate],
          '#45c2b1',
          '#6f8d91',
        ] as maplibregl.ExpressionSpecification
        const selectedOpacity = [
          'case',
          ['==', ['get', 'vesselId'], selectedCandidate],
          1,
          0.35,
        ] as maplibregl.ExpressionSpecification
        map.setPaintProperty('vessel-tracks', 'line-color', selectedExpression)
        map.setPaintProperty('vessel-tracks', 'line-opacity', selectedOpacity)
      }
      if (map.getLayer('vessel-markers')) {
        const selectedExpression = [
          'case',
          ['==', ['get', 'vesselId'], selectedCandidate],
          '#45c2b1',
          '#6f8d91',
        ] as maplibregl.ExpressionSpecification
        map.setPaintProperty('vessel-markers', 'circle-color', selectedExpression)
        map.setPaintProperty('vessel-markers', 'circle-radius', [
          'case',
          ['==', ['get', 'vesselId'], selectedCandidate],
          8,
          4,
        ] as maplibregl.ExpressionSpecification)
        map.setPaintProperty('vessel-markers', 'circle-stroke-width', [
          'case',
          ['==', ['get', 'vesselId'], selectedCandidate],
          3,
          1,
        ] as maplibregl.ExpressionSpecification)
      }
    } catch {
      // Ignore if layers are not yet ready
    }
  }, [selectedCandidate, mapReady])

  function resetView() {
    mapRef.current?.fitBounds(effectiveBounds, { padding: 45, duration: 500 })
  }

  function handleZoomIn() {
    mapRef.current?.zoomIn({ duration: 300 })
  }

  function handleZoomOut() {
    mapRef.current?.zoomOut({ duration: 300 })
  }

  return (
    <section className="map-panel" aria-label="MARIS GIS map">
      <div className="map-surface">
        <div ref={containerRef} className="map-container" />
        
        {/* Floating Mission Control HUD Toolbar */}
        <div className="map-hud-toolbar">
          <button className="map-hud-btn" type="button" onClick={resetView} title="Reset camera bounds to AOI">
            <Crosshair size={14} color="var(--color-accent)" />
            <span>Reset AOI</span>
          </button>
          <button className="map-hud-btn" type="button" onClick={handleZoomIn} title="Zoom in">
            <ZoomIn size={14} />
          </button>
          <button className="map-hud-btn" type="button" onClick={handleZoomOut} title="Zoom out">
            <ZoomOut size={14} />
          </button>
        </div>

        {/* Live Coordinate Crosshair Tracker */}
        <div className="map-coord-tracker mono-num">
          <span>{cursorCoords}</span>
        </div>

        {mapError && (
          <div className="map-fallback">
            <strong>GIS view unavailable</strong>
            <span>The geographic base map could not be initialized. Analytical data remains available in the panels.</span>
          </div>
        )}

        <MapLegend isDemoMode={isDemoMode} isSimulationMode={isSimulationMode} />
      </div>
    </section>
  )
}

function buildSimulationBounds(scenario: SimulationScenario): maplibregl.LngLatBoundsLike {
  const polygonCoords = scenario.spillGeometry.coordinates[0].concat(scenario.sourceZone.coordinates[0])
  const trackCoords = scenario.candidateVessels.flatMap((vessel) => vessel.track.map((point) => [point.longitude, point.latitude] as [number, number]))
  const allCoords = [...polygonCoords, ...trackCoords]

  const minLon = Math.min(...allCoords.map(([lon]) => lon))
  const maxLon = Math.max(...allCoords.map(([lon]) => lon))
  const minLat = Math.min(...allCoords.map(([, lat]) => lat))
  const maxLat = Math.max(...allCoords.map(([, lat]) => lat))

  return [
    [minLon - 0.12, minLat - 0.12],
    [maxLon + 0.12, maxLat + 0.12],
  ]
}

function addSimulationMapSourcesAndLayers(map: MapLibreMap, scenario: SimulationScenario) {
  const spillFeature = { type: 'Feature', properties: {}, geometry: scenario.spillGeometry }
  const sourceFeature = { type: 'Feature', properties: {}, geometry: scenario.sourceZone }
  const vesselTracks = scenario.candidateVessels.map((vessel) => ({
    type: 'Feature',
    properties: { vesselId: vessel.id, vesselName: vessel.name, candidate: vessel.isCandidate },
    geometry: {
      type: 'LineString',
      coordinates: vessel.track.map((point) => [point.longitude, point.latitude]),
    },
  }))
  const vesselMarkers = scenario.candidateVessels.map((vessel) => ({
    type: 'Feature',
    properties: { vesselId: vessel.id, vesselName: vessel.name, candidate: vessel.isCandidate },
    geometry: {
      type: 'Point',
      coordinates: [vessel.track.at(-1)?.longitude ?? 0, vessel.track.at(-1)?.latitude ?? 0],
    },
  }))

  map.addSource('spill-polygon', { type: 'geojson', data: { type: 'FeatureCollection', features: [spillFeature] } })
  map.addSource('spill-centroid', {
    type: 'geojson',
    data: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: { kind: 'simulation-slick-centroid' },
          geometry: {
            type: 'Point',
            coordinates: [
              scenario.spillGeometry.coordinates[0][0][0],
              scenario.spillGeometry.coordinates[0][0][1],
            ],
          },
        },
      ],
    },
  })
  map.addSource('origin-zone', { type: 'geojson', data: { type: 'FeatureCollection', features: [sourceFeature] } })
  map.addSource('drift-paths', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
  map.addSource('vessel-tracks', { type: 'geojson', data: { type: 'FeatureCollection', features: vesselTracks } })
  map.addSource('vessel-markers', { type: 'geojson', data: { type: 'FeatureCollection', features: vesselMarkers } })
  map.addSource('origin-point', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
  map.addSource('approach-vector', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
  map.addSource('anchor-swing-circle', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })

  map.addLayer({
    id: 'origin-zone-fill',
    type: 'fill',
    source: 'origin-zone',
    paint: { 'fill-color': '#62aee8', 'fill-opacity': 0.15 },
  })
  map.addLayer({
    id: 'origin-zone-line',
    type: 'line',
    source: 'origin-zone',
    paint: { 'line-color': '#62aee8', 'line-width': 2, 'line-dasharray': [3, 2] },
  })
  map.addLayer({
    id: 'spill-fill',
    type: 'fill',
    source: 'spill-polygon',
    paint: { 'fill-color': '#e0b15b', 'fill-opacity': 0.45 },
  })
  map.addLayer({
    id: 'spill-line',
    type: 'line',
    source: 'spill-polygon',
    paint: { 'line-color': '#e0b15b', 'line-width': 2.5 },
  })
  map.addLayer({
    id: 'spill-centroid',
    type: 'circle',
    source: 'spill-centroid',
    paint: {
      'circle-color': '#e49a55',
      'circle-radius': 6,
      'circle-stroke-color': '#fff4d6',
      'circle-stroke-width': 2,
    },
  })
  map.addLayer({
    id: 'vessel-tracks',
    type: 'line',
    source: 'vessel-tracks',
    paint: { 'line-color': '#6f8d91', 'line-width': 2, 'line-opacity': 0.7 },
  })
  map.addLayer({
    id: 'vessel-markers',
    type: 'circle',
    source: 'vessel-markers',
    paint: {
      'circle-color': '#6f8d91',
      'circle-radius': 4,
      'circle-stroke-color': '#08171b',
      'circle-stroke-width': 1,
    },
  })
}

function initLiveMapSourcesAndLayers(
  map: MapLibreMap,
  spillGeom?: Record<string, unknown> | null,
  spillCentroid?: { longitude: number; latitude: number } | null,
  sourceZoneGeom?: Record<string, unknown> | null
) {
  const spillFC = formatAsFeatureCollection(spillGeom)
  const sourceZoneFC = formatAsFeatureCollection(sourceZoneGeom)
  const centroidFC: FeatureCollection<Point, Record<string, unknown>> = spillCentroid
    ? {
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            properties: { kind: 'observed-slick-centroid' },
            geometry: { type: 'Point', coordinates: [spillCentroid.longitude, spillCentroid.latitude] },
          },
        ],
      }
    : { type: 'FeatureCollection', features: [] }

  const emptyFC: FeatureCollection = { type: 'FeatureCollection', features: [] }

  map.addSource('spill-polygon', { type: 'geojson', data: spillFC })
  map.addSource('spill-centroid', { type: 'geojson', data: centroidFC })
  map.addSource('origin-zone', { type: 'geojson', data: sourceZoneFC })
  map.addSource('origin-point', { type: 'geojson', data: emptyFC })
  map.addSource('drift-paths', { type: 'geojson', data: emptyFC })
  map.addSource('vessel-tracks', { type: 'geojson', data: emptyFC })
  map.addSource('vessel-markers', { type: 'geojson', data: emptyFC })
  map.addSource('approach-vector', { type: 'geojson', data: emptyFC })
  map.addSource('anchor-swing-circle', { type: 'geojson', data: emptyFC })

  map.addLayer({
    id: 'origin-zone-fill',
    type: 'fill',
    source: 'origin-zone',
    paint: { 'fill-color': '#62aee8', 'fill-opacity': 0.15 },
  })
  map.addLayer({
    id: 'origin-zone-line',
    type: 'line',
    source: 'origin-zone',
    paint: { 'line-color': '#62aee8', 'line-width': 2, 'line-dasharray': [3, 2] },
  })
  map.addLayer({
    id: 'spill-fill',
    type: 'fill',
    source: 'spill-polygon',
    paint: { 'fill-color': '#e0b15b', 'fill-opacity': 0.45 },
  })
  map.addLayer({
    id: 'spill-line',
    type: 'line',
    source: 'spill-polygon',
    paint: { 'line-color': '#e0b15b', 'line-width': 2.5 },
  })
  map.addLayer({
    id: 'spill-centroid',
    type: 'circle',
    source: 'spill-centroid',
    paint: {
      'circle-color': '#e49a55',
      'circle-radius': 6,
      'circle-stroke-color': '#fff4d6',
      'circle-stroke-width': 2,
    },
  })
  map.addLayer({
    id: 'drift-backward',
    type: 'line',
    source: 'drift-paths',
    paint: { 'line-color': '#62aee8', 'line-width': 2.5, 'line-dasharray': [1, 1.5] },
  })
  map.addLayer({
    id: 'drift-forward',
    type: 'line',
    source: 'drift-paths',
    paint: { 'line-color': '#45c2b1', 'line-width': 2.5, 'line-dasharray': [4, 2] },
  })
  map.addLayer({
    id: 'vessel-tracks',
    type: 'line',
    source: 'vessel-tracks',
    paint: { 'line-color': '#6f8d91', 'line-width': 2, 'line-opacity': 0.65 },
  })
  map.addLayer({
    id: 'vessel-markers',
    type: 'circle',
    source: 'vessel-markers',
    paint: {
      'circle-color': '#6f8d91',
      'circle-radius': 5,
      'circle-stroke-color': '#08171b',
      'circle-stroke-width': 1.5,
    },
  })
}

function updateLiveSources(
  map: MapLibreMap,
  spillGeom?: Record<string, unknown> | null,
  spillCentroid?: { longitude: number; latitude: number } | null,
  sourceZoneGeom?: Record<string, unknown> | null
) {
  const spillSource = map.getSource('spill-polygon') as maplibregl.GeoJSONSource | undefined
  if (spillSource) {
    spillSource.setData(formatAsFeatureCollection(spillGeom))
  }

  const centroidSource = map.getSource('spill-centroid') as maplibregl.GeoJSONSource | undefined
  if (centroidSource) {
    const fc: FeatureCollection<Point, Record<string, unknown>> = spillCentroid
      ? {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              properties: { kind: 'observed-slick-centroid' },
              geometry: { type: 'Point', coordinates: [spillCentroid.longitude, spillCentroid.latitude] },
            },
          ],
        }
      : { type: 'FeatureCollection', features: [] }
    centroidSource.setData(fc)
  }

  const originSource = map.getSource('origin-zone') as maplibregl.GeoJSONSource | undefined
  if (originSource) {
    originSource.setData(formatAsFeatureCollection(sourceZoneGeom))
  }
}

function formatAsFeatureCollection(geom?: Record<string, unknown> | null): FeatureCollection {
  if (!geom || Object.keys(geom).length === 0) {
    return { type: 'FeatureCollection', features: [] }
  }
  if (geom.type === 'FeatureCollection') {
    return geom as unknown as FeatureCollection
  }
  if (geom.type === 'Feature') {
    return { type: 'FeatureCollection', features: [geom as unknown as Feature] }
  }
  if (geom.type === 'Polygon' || geom.type === 'MultiPolygon') {
    return {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: {},
          geometry: geom as unknown as Polygon,
        },
      ],
    }
  }
  return { type: 'FeatureCollection', features: [] }
}

function addMapSourcesAndLayers(
  map: MapLibreMap,
  spillData: FeatureCollection<Polygon, Record<string, unknown>>,
  aisData: ReconstructedAisData,
  driftData: DriftReconstructionData
) {
  const spillCentroid: FeatureCollection<Point, Record<string, unknown>> = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: { kind: 'observed-slick-reference' },
        geometry: { type: 'Point', coordinates: [9.478333, 43.248333] },
      },
    ],
  }
  map.addSource('spill-polygon', { type: 'geojson', data: spillData })
  map.addSource('spill-centroid', { type: 'geojson', data: spillCentroid })
  map.addSource('origin-zone', { type: 'geojson', data: mapData.originZone })
  map.addSource('origin-point', { type: 'geojson', data: mapData.originPoint })
  const driftFeature: Feature<LineString, { direction: 'backward' }> = {
    type: 'Feature',
    properties: { direction: 'backward' },
    geometry: { type: 'LineString', coordinates: driftData.trajectory.map((point) => [point.lon, point.lat]) },
  }
  map.addSource('drift-paths', { type: 'geojson', data: driftFeature })
  const vesselFeatures = createAisFeatures(aisData)
  map.addSource('vessel-tracks', { type: 'geojson', data: { type: 'FeatureCollection', features: vesselFeatures.tracks } })
  map.addSource('vessel-markers', { type: 'geojson', data: { type: 'FeatureCollection', features: vesselFeatures.markers } })
  map.addSource('approach-vector', { type: 'geojson', data: vesselFeatures.approachVector })
  map.addSource('anchor-swing-circle', { type: 'geojson', data: vesselFeatures.anchorCircle })

  map.addLayer({
    id: 'origin-zone-fill',
    type: 'fill',
    source: 'origin-zone',
    paint: { 'fill-color': '#62aee8', 'fill-opacity': 0.12 },
  })
  map.addLayer({
    id: 'origin-zone-line',
    type: 'line',
    source: 'origin-zone',
    paint: { 'line-color': '#62aee8', 'line-width': 1.5, 'line-dasharray': [3, 2] },
  })
  map.addLayer({
    id: 'origin-point',
    type: 'circle',
    source: 'origin-point',
    paint: {
      'circle-color': '#62aee8',
      'circle-radius': 5,
      'circle-stroke-color': '#dce7e9',
      'circle-stroke-width': 1,
    },
  })
  map.addLayer({
    id: 'spill-fill',
    type: 'fill',
    source: 'spill-polygon',
    paint: { 'fill-color': '#e0b15b', 'fill-opacity': 0.4 },
  })
  map.addLayer({
    id: 'spill-line',
    type: 'line',
    source: 'spill-polygon',
    paint: { 'line-color': '#e0b15b', 'line-width': 2 },
  })
  map.addLayer({
    id: 'spill-centroid',
    type: 'circle',
    source: 'spill-centroid',
    paint: {
      'circle-color': '#e49a55',
      'circle-radius': 5,
      'circle-stroke-color': '#fff4d6',
      'circle-stroke-width': 2,
    },
  })
  map.addLayer({
    id: 'drift-backward',
    type: 'line',
    source: 'drift-paths',
    paint: { 'line-color': '#62aee8', 'line-width': 2.5, 'line-dasharray': [1, 1.5] },
  })
  map.addLayer({
    id: 'drift-forward',
    type: 'line',
    source: 'drift-paths',
    paint: { 'line-color': '#45c2b1', 'line-width': 2.5, 'line-dasharray': [4, 2] },
  })
  map.addLayer({
    id: 'vessel-tracks',
    type: 'line',
    source: 'vessel-tracks',
    paint: { 'line-color': '#6f8d91', 'line-width': 2, 'line-opacity': 0.55 },
  })
  map.addLayer({
    id: 'vessel-markers',
    type: 'circle',
    source: 'vessel-markers',
    paint: {
      'circle-color': '#6f8d91',
      'circle-radius': 4,
      'circle-stroke-color': '#08171b',
      'circle-stroke-width': 1,
    },
  })
}

function setLayerVisibility(map: MapLibreMap, layers: MapLayerVisibility) {
  const visibility = (visible: boolean): 'visible' | 'none' => (visible ? 'visible' : 'none')
  ;['spill-fill', 'spill-line', 'spill-centroid'].forEach((id) => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility(layers.spill))
  })
  ;['drift-backward', 'drift-forward'].forEach((id) => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility(layers.drift))
  })
  ;[
    'vessel-tracks',
    'vessel-markers',
    'vessel-labels-ulysse',
    'vessel-labels-virginia',
    'approach-vector',
    'anchor-swing-circle',
  ].forEach((id) => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility(layers.vessels))
  })
}

function createAisFeatures(aisData: ReconstructedAisData) {
  const ulysse = aisData.vessels.find((vessel) => vessel.name === 'ULYSSE')
  const virginia = aisData.vessels.find((vessel) => vessel.name === 'CSL VIRGINIA')
  if (!ulysse || !virginia) throw new Error('Required reconstructed AIS vessels are missing')
  const collisionPoint = [aisData.incident.location.lon, aisData.incident.location.lat] as [number, number]
  const markers: Feature<Point, VesselProperties>[] = [
    { type: 'Feature', properties: { vesselId: ulysse.name, candidate: true }, geometry: { type: 'Point', coordinates: collisionPoint } },
    { type: 'Feature', properties: { vesselId: virginia.name, candidate: true }, geometry: { type: 'Point', coordinates: collisionPoint } },
  ]
  const approachLengthKm = 0.1
  const approachRadians = ((ulysse.approach?.courseDeg ?? 161) * Math.PI) / 180
  const approachStart: [number, number] = [
    collisionPoint[0] - (Math.sin(approachRadians) * approachLengthKm) / (111 * Math.cos((collisionPoint[1] * Math.PI) / 180)),
    collisionPoint[1] - (Math.cos(approachRadians) * approachLengthKm) / 111,
  ]
  const approachVector: Feature<LineString, VesselProperties> = {
    type: 'Feature',
    properties: { vesselId: ulysse.name, candidate: true },
    geometry: { type: 'LineString', coordinates: [approachStart, collisionPoint] },
  }
  const anchorCircle = createCircleFeature(collisionPoint, (virginia.anchor?.swingCircleDiameterM ?? 1000) / 2)
  return { tracks: [] as Feature<LineString, VesselProperties>[], markers, approachVector, anchorCircle }
}

function createCircleFeature(center: [number, number], radiusMeters: number): Feature<Polygon, { vesselId: string; diameterM: number }> {
  const coordinates = Array.from({ length: 65 }, (_, index) => {
    const angle = (index / 64) * Math.PI * 2
    const latitudeOffset = (radiusMeters * Math.cos(angle)) / 111_320
    const longitudeOffset = (radiusMeters * Math.sin(angle)) / (111_320 * Math.cos((center[1] * Math.PI) / 180))
    return [center[0] + longitudeOffset, center[1] + latitudeOffset] as [number, number]
  })
  return {
    type: 'Feature',
    properties: { vesselId: 'CSL VIRGINIA', diameterM: radiusMeters * 2 },
    geometry: { type: 'Polygon', coordinates: [coordinates] },
  }
}

function MapLegend({ isDemoMode, isSimulationMode }: { isDemoMode: boolean; isSimulationMode: boolean }) {
  if (isDemoMode) {
    return (
      <div className="map-legend">
        <div className="legend-title">Historical Evidence</div>
        <span>
          <i className="legend-swatch legend-swatch--spill" /> Observed slick - reconstructed
        </span>
        <span>
          <i className="legend-swatch legend-swatch--origin" /> Estimated origin zone
        </span>
        <span>
          <i className="legend-swatch legend-swatch--backward" /> Drift trajectory
        </span>
        <span>
          <i className="legend-swatch legend-swatch--candidate" /> Reconstructed vessel anchor
        </span>
        <span>
          <i className="legend-swatch legend-swatch--approach" /> ULYSSE approach (161°)
        </span>
      </div>
    )
  }

  if (isSimulationMode) {
    return (
      <div className="map-legend">
        <div className="legend-title">Simulation Evidence</div>
        <span>
          <i className="legend-swatch legend-swatch--spill" /> Synthetic slick polygon
        </span>
        <span>
          <i className="legend-swatch legend-swatch--origin" /> Derived source zone
        </span>
        <span>
          <i className="legend-swatch legend-swatch--candidate" /> Correlated vessel tracks
        </span>
      </div>
    )
  }

  return (
    <div className="map-legend">
      <div className="legend-title">Live GIS Artifacts</div>
      <span>
        <i className="legend-swatch legend-swatch--spill" /> B3 Spill Geometry
      </span>
      <span>
        <i className="legend-swatch legend-swatch--origin" /> D3 Source Candidate Zone
      </span>
      <span>
        <i className="legend-swatch legend-swatch--backward" /> D1/D3 Drift Trajectory
      </span>
      <span>
        <i className="legend-swatch legend-swatch--candidate" /> E1 Candidate Positions
      </span>
    </div>
  )
}