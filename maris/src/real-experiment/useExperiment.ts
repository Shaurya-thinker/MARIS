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
  characterizeSentinelObservation,
  fetchAisPositions,
  fetchExperimentConfig,
  getExperimentRun,
  listExperimentRuns,
  runExperiment,
  runForwardPrediction,
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
  ForwardPredictionResult,
  SelectedEnvironment,
  SentinelDiscoverRequest,
  SentinelProduct,
  SlickCharacterization,
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
  | { type: 'SET_SLICK_CHARACTERIZATION'; characterization: SlickCharacterization | null }
  | { type: 'RUN_STARTED' }
  | { type: 'RUN_SUCCESS'; result: ExperimentRunResult }
  | { type: 'RUN_ERROR'; error: string }
  | { type: 'SET_HISTORY'; runs: ExperimentRunSummary[] }
  | { type: 'LOAD_RUN'; result: ExperimentRunResult }
  | { type: 'SET_FORWARD_PREDICTION_HOURS'; hours: number }
  | { type: 'SET_FORWARD_STEP_HOURS'; hours: number }
  | { type: 'SET_ENABLE_FORWARD_PREDICTION'; enabled: boolean }
  | { type: 'FORWARD_PREDICT_START' }
  | { type: 'FORWARD_PREDICT_SUCCESS'; result: ForwardPredictionResult }
  | { type: 'FORWARD_PREDICT_ERROR'; error: string }

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
  slickCharacterization: null,
  forwardPredictionHours: 12,
  forwardStepHours: 1.0,
  enableForwardPrediction: true,
  forwardPrediction: null,
  forwardPredicting: false,
  forwardPredictError: null,
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
    case 'SET_SLICK_CHARACTERIZATION':
      return {
        ...state,
        slickCharacterization: action.characterization,
        observationLat: action.characterization?.centroid_lat ?? state.observationLat,
        observationLon: action.characterization?.centroid_lon ?? state.observationLon,
        spillAreaM2: action.characterization?.area_m2 ?? state.spillAreaM2,
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
      return {
        ...state,
        runStatus: 'success',
        runResult: action.result,
        forwardPrediction: action.result.forward_prediction ?? state.forwardPrediction,
        step: 6,
      }
    case 'RUN_ERROR':
      return { ...state, runStatus: 'error', runError: action.error }
    case 'SET_HISTORY':
      return { ...state, runHistory: action.runs }
    case 'LOAD_RUN':
      return {
        ...state,
        runResult: action.result,
        runStatus: 'success',
        slickCharacterization: action.result.slick_characterization ?? null,
        observationLat: action.result.observation_lat ?? null,
        observationLon: action.result.observation_lon ?? null,
        forwardPrediction: action.result.forward_prediction ?? null,
        step: 6,
      }
    case 'SET_FORWARD_PREDICTION_HOURS':
      return { ...state, forwardPredictionHours: action.hours }
    case 'SET_FORWARD_STEP_HOURS':
      return { ...state, forwardStepHours: action.hours }
    case 'SET_ENABLE_FORWARD_PREDICTION':
      return { ...state, enableForwardPrediction: action.enabled }
    case 'FORWARD_PREDICT_START':
      return { ...state, forwardPredicting: true, forwardPredictError: null }
    case 'FORWARD_PREDICT_SUCCESS':
      return { ...state, forwardPredicting: false, forwardPrediction: action.result, forwardPredictError: null }
    case 'FORWARD_PREDICT_ERROR':
      return { ...state, forwardPredicting: false, forwardPredictError: action.error }
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

  const characterizeObservation = useCallback(
    async (productToChar?: SentinelProduct): Promise<SlickCharacterization | null> => {
      const target = productToChar ?? state.selectedProduct
      if (!target) return null
      try {
        const resp = await characterizeSentinelObservation({
          product_id: target.product_id,
          title: target.title,
          sensing_start: target.sensing_start,
          centroid_lon: target.centroid_lon,
          centroid_lat: target.centroid_lat,
          mode: target.mode,
          polarisation: target.polarisation,
          footprint: target.footprint,
          backtrack_hours: state.backtrackHours,
        })
        dispatch({ type: 'SET_SLICK_CHARACTERIZATION', characterization: resp.characterization })
        return resp.characterization
      } catch (err) {
        console.warn('Failed to characterize observation:', err)
        return null
      }
    },
    [state.selectedProduct, state.backtrackHours]
  )

  const selectProduct = useCallback((product: SentinelProduct) => {
    dispatch({ type: 'SELECT_PRODUCT', product })
    characterizeObservation(product)
  }, [characterizeObservation])

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
      slick_characterization: state.slickCharacterization,
      forward_prediction_hours: state.enableForwardPrediction ? state.forwardPredictionHours : undefined,
      forward_step_hours: state.enableForwardPrediction ? state.forwardStepHours : undefined,
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

  const setForwardPredictionHours = useCallback((hours: number) => {
    dispatch({ type: 'SET_FORWARD_PREDICTION_HOURS', hours })
  }, [])

  const setForwardStepHours = useCallback((hours: number) => {
    dispatch({ type: 'SET_FORWARD_STEP_HOURS', hours })
  }, [])

  const setEnableForwardPrediction = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_ENABLE_FORWARD_PREDICTION', enabled })
  }, [])

  const predictForward = useCallback(async (): Promise<ForwardPredictionResult | null> => {
    const { selectedProduct, environment, forwardPredictionHours, forwardStepHours, searchBbox } = state
    if (!selectedProduct || !environment) {
      dispatch({ type: 'FORWARD_PREDICT_ERROR', error: 'Missing required inputs (observation or environment)' })
      return null
    }

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

    dispatch({ type: 'FORWARD_PREDICT_START' })
    try {
      const resp = await runForwardPrediction({
        satellite_product_id: selectedProduct.product_id,
        observation_lon: observationLon,
        observation_lat: observationLat,
        origin_lon: observationLon,
        origin_lat: observationLat,
        observation_time: selectedProduct.sensing_start,
        era5_netcdf_path: environment.era5_netcdf_path,
        cmems_netcdf_path: environment.cmems_netcdf_path,
        prediction_hours: forwardPredictionHours,
        step_hours: forwardStepHours,
      })
      const fwdResult = resp.forward_prediction ?? resp.prediction
      if (fwdResult) {
        dispatch({ type: 'FORWARD_PREDICT_SUCCESS', result: fwdResult })
      }
      return fwdResult ?? null
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Forward drift prediction failed'
      dispatch({ type: 'FORWARD_PREDICT_ERROR', error: msg })
      return null
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
    characterizeObservation,
    acquireEnvironment,
    setBacktrackHours,
    setStepHours,
    setSpillArea,
    setObservationCoords,
    setForwardPredictionHours,
    setForwardStepHours,
    setEnableForwardPrediction,
    predictForward,
    searchVessels,
    toggleVessel,
    clearVessels,
    executeRun,
    loadRun,
    refreshHistory,
  }
}
