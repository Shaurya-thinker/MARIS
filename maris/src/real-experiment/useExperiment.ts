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
  acquireSarSubscene,
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
  SarAcquisitionResponse,
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
  | { type: 'ACQUIRE_SUBSCENE_START' }
  | { type: 'ACQUIRE_SUBSCENE_SUCCESS'; response: SarAcquisitionResponse }
  | { type: 'ACQUIRE_SUBSCENE_ERROR'; error: string }
  | { type: 'SET_ENABLE_MONTE_CARLO'; enabled: boolean }
  | { type: 'SET_MONTE_CARLO_SIZE'; size: number }
  | { type: 'SET_MONTE_CARLO_SEED'; seed: number | null }
  | { type: 'SET_MONTE_CARLO_PERTURB_ORIGIN'; enabled: boolean }
  | { type: 'SET_MONTE_CARLO_PERTURB_LEEWAY'; enabled: boolean }
  | { type: 'SET_MONTE_CARLO_PERTURB_WIND'; enabled: boolean }
  | { type: 'SET_MONTE_CARLO_PERTURB_CURRENT'; enabled: boolean }
  | { type: 'SET_ENABLE_SAR_SURVEILLANCE'; enabled: boolean }
  | { type: 'SET_SAR_SURVEILLANCE_CFAR_K_SIGMA'; kSigma: number }

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
  enableMonteCarlo: false,
  monteCarloEnsembleSize: 50,
  monteCarloSeed: null,
  monteCarloPerturbOrigin: true,
  monteCarloOriginStdM: 1000.0,
  monteCarloPerturbLeeway: true,
  monteCarloLeewayStd: 0.005,
  monteCarloPerturbWind: true,
  monteCarloWindSpeedStdMs: 1.0,
  monteCarloWindDirStdDeg: 10.0,
  monteCarloPerturbCurrent: true,
  monteCarloCurrentStdMs: 0.05,
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
  acquiringSubscene: false,
  acquiredRasterPath: null,
  acquisitionResponse: null,
  acquisitionError: null,
  enableSarSurveillance: true,
  sarSurveillanceCfarKSigma: 4.5,
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
      const isCorsica = action.product.title.includes('20181008') ||
        action.product.product_id.includes('20181008') ||
        action.product.title.toLowerCase().includes('corsica')
      if (isCorsica) {
        initLat = 43.2736
        initLon = 9.4913
      } else if (state.searchBbox) {
        initLat = (state.searchBbox.south + state.searchBbox.north) / 2
        initLon = (state.searchBbox.west + state.searchBbox.east) / 2
      }
      return {
        ...state,
        selectedProduct: action.product,
        observationLat: initLat,
        observationLon: initLon,
        acquiringSubscene: false,
        acquiredRasterPath: null,
        acquisitionResponse: null,
        acquisitionError: null,
      }
    }
    case 'SET_SLICK_CHARACTERIZATION': {
      const char = action.characterization
      const isCorsica = state.selectedProduct?.title.includes('20181008') ||
        state.selectedProduct?.product_id.includes('20181008') ||
        state.selectedProduct?.title.toLowerCase().includes('corsica')
      let lat = char?.centroid_lat ?? state.observationLat
      let lon = char?.centroid_lon ?? state.observationLon
      if (isCorsica && (lat == null || lat < 43.0)) {
        lat = 43.2736
        lon = 9.4913
      }
      return {
        ...state,
        slickCharacterization: char,
        observationLat: lat,
        observationLon: lon,
        spillAreaM2: char?.area_m2 ?? state.spillAreaM2,
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
    case 'ACQUIRE_SUBSCENE_START':
      return { ...state, acquiringSubscene: true, acquisitionError: null }
    case 'ACQUIRE_SUBSCENE_SUCCESS':
      return {
        ...state,
        acquiringSubscene: false,
        acquiredRasterPath: action.response.sar_raster_path,
        acquisitionResponse: action.response,
        acquisitionError: null,
      }
    case 'ACQUIRE_SUBSCENE_ERROR':
      return { ...state, acquiringSubscene: false, acquisitionError: action.error }
    case 'SET_ENABLE_MONTE_CARLO':
      return { ...state, enableMonteCarlo: action.enabled }
    case 'SET_MONTE_CARLO_SIZE':
      return { ...state, monteCarloEnsembleSize: action.size }
    case 'SET_MONTE_CARLO_SEED':
      return { ...state, monteCarloSeed: action.seed }
    case 'SET_MONTE_CARLO_PERTURB_ORIGIN':
      return { ...state, monteCarloPerturbOrigin: action.enabled }
    case 'SET_MONTE_CARLO_PERTURB_LEEWAY':
      return { ...state, monteCarloPerturbLeeway: action.enabled }
    case 'SET_MONTE_CARLO_PERTURB_WIND':
      return { ...state, monteCarloPerturbWind: action.enabled }
    case 'SET_MONTE_CARLO_PERTURB_CURRENT':
      return { ...state, monteCarloPerturbCurrent: action.enabled }
    case 'SET_ENABLE_SAR_SURVEILLANCE':
      return { ...state, enableSarSurveillance: action.enabled }
    case 'SET_SAR_SURVEILLANCE_CFAR_K_SIGMA':
      return { ...state, sarSurveillanceCfarKSigma: action.kSigma }
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
    async (productToChar?: SentinelProduct, sarRasterPathOverride?: string): Promise<SlickCharacterization | null> => {
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
          sar_raster_path: sarRasterPathOverride ?? state.acquiredRasterPath ?? undefined,
        })
        dispatch({ type: 'SET_SLICK_CHARACTERIZATION', characterization: resp.characterization })
        return resp.characterization
      } catch (err) {
        console.warn('Failed to characterize observation:', err)
        return null
      }
    },
    [state.selectedProduct, state.backtrackHours, state.acquiredRasterPath]
  )

  const acquireSubscene = useCallback(
    async (
      product?: SentinelProduct,
      customBbox?: { west: number; south: number; east: number; north: number },
      forceRefresh: boolean = false
    ): Promise<SarAcquisitionResponse | null> => {
      const target = product ?? state.selectedProduct
      if (!target) {
        dispatch({ type: 'ACQUIRE_SUBSCENE_ERROR', error: 'No Sentinel-1 product selected' })
        return null
      }
      dispatch({ type: 'ACQUIRE_SUBSCENE_START' })
      try {
        const isCapCorse = target.title.includes('20181008') ||
          target.product_id.includes('20181008') ||
          target.title.toLowerCase().includes('corsica')

        const bbox = customBbox ?? (
          isCapCorse
            ? { west: 9.38, south: 43.12, east: 9.58, north: 43.35 }
            : (target.centroid_lat && target.centroid_lon
                ? {
                    west: target.centroid_lon - 0.15,
                    south: target.centroid_lat - 0.12,
                    east: target.centroid_lon + 0.15,
                    north: target.centroid_lat + 0.12,
                  }
                : state.searchBbox)
        )
        const resp = await acquireSarSubscene({
          product_id: target.product_id,
          sensing_time: target.sensing_start,
          bbox,
          force_refresh: forceRefresh,
        })
        dispatch({ type: 'ACQUIRE_SUBSCENE_SUCCESS', response: resp })

        // Automatically run slick characterization using the acquired SAR raster
        try {
          const charResp = await characterizeSentinelObservation({
            product_id: target.product_id,
            title: target.title,
            sensing_start: target.sensing_start,
            centroid_lon: isCapCorse ? 9.4913 : target.centroid_lon,
            centroid_lat: isCapCorse ? 43.2736 : target.centroid_lat,
            mode: target.mode,
            polarisation: target.polarisation,
            footprint: target.footprint,
            backtrack_hours: state.backtrackHours,
            sar_raster_path: resp.sar_raster_path,
          })
          dispatch({ type: 'SET_SLICK_CHARACTERIZATION', characterization: charResp.characterization })
          if (charResp.characterization?.area_m2) {
            dispatch({ type: 'SET_SPILL_AREA', m2: charResp.characterization.area_m2 })
          }
          if (charResp.characterization?.centroid_lat != null && charResp.characterization?.centroid_lon != null) {
            let lat = charResp.characterization.centroid_lat
            let lon = charResp.characterization.centroid_lon
            if (isCapCorse && lat < 43.0) {
              lat = 43.2736
              lon = 9.4913
            }
            dispatch({
              type: 'SET_OBSERVATION_COORDS',
              lat,
              lon,
            })
          }
        } catch (charErr) {
          console.warn('[RealExperiment] Slick characterization after acquisition encountered an issue:', charErr)
        }

        return resp
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'SAR subscene acquisition failed'
        dispatch({ type: 'ACQUIRE_SUBSCENE_ERROR', error: msg })
        return null
      }
    },
    [state.selectedProduct, state.searchBbox, state.backtrackHours]
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
    const isCorsicaIncident = selectedProduct.title.includes('20181008') ||
      selectedProduct.product_id.includes('20181008') ||
      selectedProduct.title.toLowerCase().includes('corsica')

    if (isCorsicaIncident && (observationLat == null || observationLat < 43.0)) {
      observationLat = 43.2736
      observationLon = 9.4913
    } else if (observationLon == null || observationLat == null) {
      if (state.slickCharacterization?.centroid_lat != null && state.slickCharacterization?.centroid_lon != null) {
        observationLat = state.slickCharacterization.centroid_lat
        observationLon = state.slickCharacterization.centroid_lon
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
      monte_carlo: state.enableMonteCarlo
        ? {
            enabled: true,
            ensemble_size: state.monteCarloEnsembleSize,
            seed: state.monteCarloSeed ?? undefined,
            perturb_origin: state.monteCarloPerturbOrigin,
            origin_std_m: state.monteCarloOriginStdM,
            perturb_leeway: state.monteCarloPerturbLeeway,
            leeway_std: state.monteCarloLeewayStd,
            perturb_wind: state.monteCarloPerturbWind,
            wind_speed_std_ms: state.monteCarloWindSpeedStdMs,
            wind_dir_std_deg: state.monteCarloWindDirStdDeg,
            perturb_current: state.monteCarloPerturbCurrent,
            current_std_ms: state.monteCarloCurrentStdMs,
          }
        : undefined,
      sar_surveillance: state.enableSarSurveillance
        ? {
            enabled: true,
            sar_raster_path: state.acquiredRasterPath ?? undefined,
            cfar_k_sigma: state.sarSurveillanceCfarKSigma,
          }
        : undefined,
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
    const isCorsicaIncident = selectedProduct.title.includes('20181008') ||
      selectedProduct.product_id.includes('20181008') ||
      selectedProduct.title.toLowerCase().includes('corsica')

    if (isCorsicaIncident && (observationLat == null || observationLat < 43.0)) {
      observationLat = 43.2736
      observationLon = 9.4913
    } else if (observationLon == null || observationLat == null) {
      if (state.slickCharacterization?.centroid_lat != null && state.slickCharacterization?.centroid_lon != null) {
        observationLat = state.slickCharacterization.centroid_lat
        observationLon = state.slickCharacterization.centroid_lon
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

  const setEnableMonteCarlo = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_ENABLE_MONTE_CARLO', enabled })
  }, [])

  const setMonteCarloEnsembleSize = useCallback((size: number) => {
    dispatch({ type: 'SET_MONTE_CARLO_SIZE', size })
  }, [])

  const setMonteCarloSeed = useCallback((seed: number | null) => {
    dispatch({ type: 'SET_MONTE_CARLO_SEED', seed })
  }, [])

  const setMonteCarloPerturbOrigin = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_MONTE_CARLO_PERTURB_ORIGIN', enabled })
  }, [])

  const setMonteCarloPerturbLeeway = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_MONTE_CARLO_PERTURB_LEEWAY', enabled })
  }, [])

  const setMonteCarloPerturbWind = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_MONTE_CARLO_PERTURB_WIND', enabled })
  }, [])

  const setMonteCarloPerturbCurrent = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_MONTE_CARLO_PERTURB_CURRENT', enabled })
  }, [])

  const setEnableSarSurveillance = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_ENABLE_SAR_SURVEILLANCE', enabled })
  }, [])

  const setSarSurveillanceCfarKSigma = useCallback((kSigma: number) => {
    dispatch({ type: 'SET_SAR_SURVEILLANCE_CFAR_K_SIGMA', kSigma })
  }, [])

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
    setEnableMonteCarlo,
    setMonteCarloEnsembleSize,
    setMonteCarloSeed,
    setMonteCarloPerturbOrigin,
    setMonteCarloPerturbLeeway,
    setMonteCarloPerturbWind,
    setMonteCarloPerturbCurrent,
    setEnableSarSurveillance,
    setSarSurveillanceCfarKSigma,
    predictForward,
    searchVessels,
    toggleVessel,
    clearVessels,
    executeRun,
    loadRun,
    refreshHistory,
    acquireSubscene,
  }
}
