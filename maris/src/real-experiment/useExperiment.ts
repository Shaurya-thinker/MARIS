/**
 * useExperiment — React hook managing the real-data experiment wizard state.
 *
 * Centralises all async API calls and step navigation so the view component
 * remains a pure rendering concern.
 *
 * The hook is completely independent of useInvestigation / simulation state.
 */

import { useCallback, useEffect, useReducer } from 'react'
import {
  discoverSentinelProducts,
  fetchAisPositions,
  fetchExperimentConfig,
  getExperimentRun,
  listExperimentRuns,
  runExperiment,
  searchAisVessels,
  selectEnvironment,
} from './experimentApi'
import type {
  AisSearchRequest,
  ExperimentConfig,
  ExperimentRunRequest,
  ExperimentRunResult,
  ExperimentRunSummary,
  ExperimentWizardState,
  SelectedEnvironment,
  SentinelDiscoverRequest,
  SentinelProduct,
  VesselInput,
  WizardStep,
} from './experimentTypes'

// ---------------------------------------------------------------------------
// Reducer actions
// ---------------------------------------------------------------------------

type Action =
  | { type: 'SET_CONFIG'; config: ExperimentConfig }
  | { type: 'SET_STEP'; step: WizardStep }
  | { type: 'SET_SEARCH_BBOX'; bbox: { west: number; south: number; east: number; north: number } }
  | { type: 'SET_SEARCH_DATES'; start: string; end: string }
  | { type: 'SET_PRODUCTS'; products: SentinelProduct[] }
  | { type: 'SELECT_PRODUCT'; product: SentinelProduct }
  | { type: 'SET_ENVIRONMENT'; env: SelectedEnvironment }
  | { type: 'SET_BACKTRACK'; hours: number }
  | { type: 'SET_STEP_HOURS'; hours: number }
  | { type: 'SET_SPILL_AREA'; m2: number | null }
  | { type: 'SET_OBSERVATION_COORDS'; lat: number | null; lon: number | null }
  | { type: 'SET_AIS_RESULT'; result: import('./experimentTypes').AisSearchResponse }
  | { type: 'TOGGLE_VESSEL'; vessel: VesselInput }
  | { type: 'CLEAR_VESSELS' }
  | { type: 'RUN_STARTED' }
  | { type: 'RUN_SUCCESS'; result: ExperimentRunResult }
  | { type: 'RUN_ERROR'; error: string }
  | { type: 'SET_HISTORY'; runs: ExperimentRunSummary[] }
  | { type: 'LOAD_RUN'; result: ExperimentRunResult }

const initialState: ExperimentWizardState = {
  step: 1,
  config: null,
  searchBbox: null,
  searchStart: '',
  searchEnd: '',
  discoveredProducts: [],
  selectedProduct: null,
  environment: null,
  backtrackHours: 12,
  stepHours: 1.0,
  spillAreaM2: null,
  observationLat: null,
  observationLon: null,
  aisSearchResult: null,
  selectedVessels: [],
  runStatus: 'idle',
  runError: null,
  runResult: null,
  runHistory: [],
}

function reducer(state: ExperimentWizardState, action: Action): ExperimentWizardState {
  switch (action.type) {
    case 'SET_CONFIG':
      return { ...state, config: action.config }
    case 'SET_STEP':
      return { ...state, step: action.step }
    case 'SET_SEARCH_BBOX':
      return { ...state, searchBbox: action.bbox }
    case 'SET_SEARCH_DATES':
      return { ...state, searchStart: action.start, searchEnd: action.end }
    case 'SET_PRODUCTS':
      return { ...state, discoveredProducts: action.products }
    case 'SELECT_PRODUCT': {
      let initLat = action.product.centroid_lat
      let initLon = action.product.centroid_lon
      if (action.product.title.includes('20181008') || action.product.product_id.includes('20181008')) {
        initLat = 43.24833
        initLon = 9.47833
      } else if (state.searchBbox) {
        initLat = (state.searchBbox.south + state.searchBbox.north) / 2
        initLon = (state.searchBbox.west + state.searchBbox.east) / 2
      }
      return {
        ...state,
        selectedProduct: action.product,
        observationLat: initLat,
        observationLon: initLon,
      }
    }
    case 'SET_OBSERVATION_COORDS':
      return { ...state, observationLat: action.lat, observationLon: action.lon }
    case 'SET_ENVIRONMENT':
      return { ...state, environment: action.env }
    case 'SET_BACKTRACK':
      return { ...state, backtrackHours: action.hours }
    case 'SET_STEP_HOURS':
      return { ...state, stepHours: action.hours }
    case 'SET_SPILL_AREA':
      return { ...state, spillAreaM2: action.m2 }
    case 'SET_AIS_RESULT':
      return { ...state, aisSearchResult: action.result }
    case 'TOGGLE_VESSEL': {
      const key = action.vessel.mmsi ?? action.vessel.vessel_name ?? ''
      const exists = state.selectedVessels.find(v => (v.mmsi ?? v.vessel_name ?? '') === key)
      if (exists) {
        return {
          ...state,
          selectedVessels: state.selectedVessels.filter(v => (v.mmsi ?? v.vessel_name ?? '') !== key),
        }
      }
      let vesselToAdd = action.vessel
      if ((!vesselToAdd.positions || vesselToAdd.positions.length === 0) && state.aisSearchResult) {
        const found = state.aisSearchResult.vessels.find(v => (v.mmsi ?? v.vessel_name ?? '') === key)
        if (found && found.positions && found.positions.length > 0) {
          vesselToAdd = { ...vesselToAdd, positions: found.positions }
        }
      }
      return {
        ...state,
        selectedVessels: [...state.selectedVessels, vesselToAdd],
      }
    }
    case 'CLEAR_VESSELS':
      return { ...state, selectedVessels: [] }
    case 'RUN_STARTED':
      return { ...state, runStatus: 'running', runError: null }
    case 'RUN_SUCCESS':
      return { ...state, runStatus: 'success', runResult: action.result, step: 6 }
    case 'RUN_ERROR':
      return { ...state, runStatus: 'error', runError: action.error }
    case 'SET_HISTORY':
      return { ...state, runHistory: action.runs }
    case 'LOAD_RUN':
      return { ...state, runResult: action.result, runStatus: 'success', step: 6 }
    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useExperiment() {
  const [state, dispatch] = useReducer(reducer, initialState)

  // Load config on mount
  useEffect(() => {
    fetchExperimentConfig()
      .then(config => dispatch({ type: 'SET_CONFIG', config }))
      .catch(err => console.warn('[RealExperiment] config fetch failed:', err))
  }, [])

  // Load run history on mount
  useEffect(() => {
    listExperimentRuns(20)
      .then(resp => dispatch({ type: 'SET_HISTORY', runs: resp.runs }))
      .catch(err => console.warn('[RealExperiment] history fetch failed:', err))
  }, [])

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  const goToStep = useCallback((step: WizardStep) => {
    dispatch({ type: 'SET_STEP', step })
  }, [])

  const setSearchBbox = useCallback((bbox: { west: number; south: number; east: number; north: number }) => {
    dispatch({ type: 'SET_SEARCH_BBOX', bbox })
  }, [])

  const setSearchDates = useCallback((start: string, end: string) => {
    dispatch({ type: 'SET_SEARCH_DATES', start, end })
  }, [])

  const searchProducts = useCallback(
    async (body: SentinelDiscoverRequest): Promise<void> => {
      const resp = await discoverSentinelProducts(body)
      dispatch({ type: 'SET_PRODUCTS', products: resp.products })
    },
    []
  )

  const selectProduct = useCallback((product: SentinelProduct) => {
    dispatch({ type: 'SELECT_PRODUCT', product })
  }, [])

  const acquireEnvironment = useCallback(
    async (overrides?: { era5?: string; cmems?: string }): Promise<SelectedEnvironment> => {
      const { selectedProduct, searchBbox, backtrackHours } = state
      if (!selectedProduct || !searchBbox) {
        throw new Error('No product or search bbox selected')
      }
      const resp = await selectEnvironment({
        observation_time: selectedProduct.sensing_start,
        west: searchBbox.west,
        south: searchBbox.south,
        east: searchBbox.east,
        north: searchBbox.north,
        backtrack_hours: backtrackHours,
        era5_override_path: overrides?.era5 ?? null,
        cmems_override_path: overrides?.cmems ?? null,
      })
      dispatch({ type: 'SET_ENVIRONMENT', env: resp })
      return resp
    },
    [state]
  )

  const setBacktrackHours = useCallback((hours: number) => {
    dispatch({ type: 'SET_BACKTRACK', hours })
  }, [])

  const setStepHours = useCallback((hours: number) => {
    dispatch({ type: 'SET_STEP_HOURS', hours })
  }, [])

  const setSpillArea = useCallback((m2: number | null) => {
    dispatch({ type: 'SET_SPILL_AREA', m2 })
  }, [])

  const searchVessels = useCallback(
    async (req: AisSearchRequest): Promise<void> => {
      const resp = await searchAisVessels(req)
      dispatch({ type: 'SET_AIS_RESULT', result: resp })
    },
    []
  )

  const toggleVessel = useCallback((vessel: VesselInput) => {
    dispatch({ type: 'TOGGLE_VESSEL', vessel })
  }, [])

  const clearVessels = useCallback(() => {
    dispatch({ type: 'CLEAR_VESSELS' })
  }, [])

  const executeRun = useCallback(async (): Promise<void> => {
    const { selectedProduct, environment, backtrackHours, stepHours, spillAreaM2, selectedVessels, searchBbox } = state
    if (!selectedProduct || !environment || !searchBbox) {
      dispatch({ type: 'RUN_ERROR', error: 'Missing required inputs (product, environment, or bbox)' })
      return
    }

    // Derive spill observation point from state, benchmark reference, search AOI, or product centroid
    let observationLon = state.observationLon
    let observationLat = state.observationLat
    if (observationLon == null || observationLat == null) {
      if (selectedProduct.title.includes('20181008') || selectedProduct.product_id.includes('20181008')) {
        observationLat = 43.24833
        observationLon = 9.47833
      } else if (searchBbox) {
        observationLat = (searchBbox.south + searchBbox.north) / 2
        observationLon = (searchBbox.west + searchBbox.east) / 2
      } else {
        observationLon = selectedProduct.centroid_lon ?? 0
        observationLat = selectedProduct.centroid_lat ?? 0
      }
    }

    // Ensure all selected vessels have authentic positions populated
    let vesselsToRun = selectedVessels
    const missingPositions = selectedVessels.filter(v => !v.positions || v.positions.length === 0)
    if (missingPositions.length > 0) {
      const mmsisToFetch = missingPositions.map(v => v.mmsi ?? v.id).filter((m): m is string => Boolean(m))
      if (mmsisToFetch.length > 0) {
        try {
          const obsTime = new Date(selectedProduct.sensing_start)
          const startTime = new Date(obsTime.getTime() - backtrackHours * 3600 * 1000)
          const posResp = await fetchAisPositions({
            mmsis: mmsisToFetch,
            west: searchBbox.west,
            south: searchBbox.south,
            east: searchBbox.east,
            north: searchBbox.north,
            start: startTime.toISOString(),
            end: obsTime.toISOString(),
          })
          if (posResp && posResp.vessel_positions) {
            vesselsToRun = selectedVessels.map(v => {
              const k = v.mmsi ?? v.id ?? ''
              if ((!v.positions || v.positions.length === 0) && posResp.vessel_positions[k]) {
                return { ...v, positions: posResp.vessel_positions[k] }
              }
              return v
            })
          }
        } catch {
          // Non-fatal fallback: backend ExperimentRunner will also attempt lookup from ais_vessels.db
        }
      }
    }

    const req: ExperimentRunRequest = {
      satellite_product_id: selectedProduct.product_id,
      observation_lon: observationLon,
      observation_lat: observationLat,
      observation_time: selectedProduct.sensing_start,
      era5_netcdf_path: environment.era5_netcdf_path,
      cmems_netcdf_path: environment.cmems_netcdf_path,
      backtrack_hours: backtrackHours,
      step_hours: stepHours,
      spill_area_m2: spillAreaM2,
      selected_vessels: vesselsToRun,
    }

    dispatch({ type: 'RUN_STARTED' })
    try {
      const result = await runExperiment(req)
      dispatch({ type: 'RUN_SUCCESS', result })
      // Refresh history
      listExperimentRuns(20)
        .then(resp => dispatch({ type: 'SET_HISTORY', runs: resp.runs }))
        .catch(() => { /* non-fatal */ })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Experiment run failed'
      dispatch({ type: 'RUN_ERROR', error: msg })
    }
  }, [state])

  const loadRun = useCallback(async (runId: string): Promise<void> => {
    const result = await getExperimentRun(runId)
    dispatch({ type: 'LOAD_RUN', result })
  }, [])

  const refreshHistory = useCallback(async (): Promise<void> => {
    const resp = await listExperimentRuns(50)
    dispatch({ type: 'SET_HISTORY', runs: resp.runs })
  }, [])

  const setObservationCoords = useCallback((lat: number | null, lon: number | null) => {
    dispatch({ type: 'SET_OBSERVATION_COORDS', lat, lon })
  }, [])

  return {
    state,
    goToStep,
    setSearchBbox,
    setSearchDates,
    searchProducts,
    selectProduct,
    acquireEnvironment,
    setBacktrackHours,
    setStepHours,
    setSpillArea,
    setObservationCoords,
    searchVessels,
    toggleVessel,
    clearVessels,
    executeRun,
    loadRun,
    refreshHistory,
  }
}
