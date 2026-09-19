"""MARIS Real-Data Experiment API routes.

All endpoints are mounted under /api/experiment/ prefix (registered in main.py).

These routes are completely isolated from the existing G1 investigation workflow
routes in app/api/routes.py.  They do NOT modify any existing endpoint behavior.
"""

from __future__ import annotations

import logging
from datetime import timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.experiment_schemas import (
    AisSearchRequest,
    AisSearchResponse,
    EnvironmentSelectRequest,
    EnvironmentSelectResponse,
    ExperimentConfigResponse,
    ExperimentListResponse,
    ExperimentRunRequest,
    ExperimentRunResponse,
    ExperimentRunSummary,
    SentinelDiscoverRequest,
    SentinelDiscoverResponse,
    SentinelProductItem,
    VesselFeaturesItem,
    VesselSummaryItem,
    SyntheticGenerateRequest,
    SyntheticGenerateResponse,
    SyntheticRunRequest,
    SyntheticRunResponse,
    ModelTrainRequest,
    ModelTrainResponse,
    ActiveModelResponse,
    EvaluatorDriftPreviewRequest,
    EvaluatorDriftPreviewResponse,
    EvaluatorFilterVesselsRequest,
    EvaluatorFilterVesselsResponse,
    EvaluatorInvestigationSummaryItem,
    EvaluatorReferenceObservationItem,
    EvaluatorRunRequest,
)
from app.core.config import settings
from app.services.real_experiment.ais_search import (
    AisSearchError,
    AisSearchService,
    ConfigurationUnavailable as AisConfigUnavailable,
)
from app.services.real_experiment.environment_selector import (
    ConfigurationUnavailable as EnvConfigUnavailable,
    EnvironmentAcquisitionError,
    EnvironmentSelectorService,
)
from app.services.real_experiment.experiment_runner import ExperimentError, ExperimentRunner
from app.services.real_experiment.experiment_store import get_experiment_store
from app.services.real_experiment.sentinel_discovery import (
    ConfigurationUnavailable as SentinelConfigUnavailable,
    DiscoveryError,
    SentinelDiscoveryService,
)

logger = logging.getLogger("maris.experiment")

router = APIRouter(tags=["Real-Data Experiment"])


# ---------------------------------------------------------------------------
# Configuration check
# ---------------------------------------------------------------------------

@router.get(
    "/config",
    response_model=ExperimentConfigResponse,
    summary="Report which external data source credentials are configured",
)
def get_experiment_config() -> ExperimentConfigResponse:
    """Return a configuration status summary.

    The wizard UI calls this on load to determine which steps are available
    and what warnings to display to the operator.
    """
    discovery_svc = SentinelDiscoveryService(cfg=settings)
    env_svc = EnvironmentSelectorService(cfg=settings)
    ais_svc = AisSearchService(cfg=settings)

    sentinel1_ok = discovery_svc.check_configured()
    era5_ok = env_svc.era5_configured()
    cmems_ok = env_svc.cmems_configured()
    ais_ok = ais_svc.is_configured()
    ais_adapter = getattr(settings, "ais_adapter_id", "unconfigured")

    warnings: list[str] = []
    if not sentinel1_ok:
        warnings.append("CDSE credentials absent: set CDSE_USERNAME + CDSE_PASSWORD (or CDSE_ACCESS_TOKEN).")
    if not era5_ok:
        warnings.append("CDS API key absent: set CDSAPI_KEY.")
    if not cmems_ok:
        warnings.append("Copernicus Marine credentials absent: set COPERNICUSMARINE_SERVICE_USERNAME + COPERNICUSMARINE_SERVICE_PASSWORD.")
    if not ais_ok:
        warnings.append(f"AIS adapter unconfigured (MARIS_AIS_ADAPTER={ais_adapter!r}). Set MARIS_AIS_ADAPTER.")

    return ExperimentConfigResponse(
        sentinel1_configured=sentinel1_ok,
        era5_configured=era5_ok,
        cmems_configured=cmems_ok,
        ais_configured=ais_ok,
        ais_adapter_id=ais_adapter,
        all_configured=sentinel1_ok and era5_ok and cmems_ok and ais_ok,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Step 1 — Sentinel-1 discovery
# ---------------------------------------------------------------------------

@router.post(
    "/sentinel/discover",
    response_model=SentinelDiscoverResponse,
    summary="Discover Sentinel-1 products in the CDSE catalogue",
)
def discover_sentinel_products(body: SentinelDiscoverRequest) -> SentinelDiscoverResponse:
    """Query the CDSE OData catalogue for Sentinel-1 products intersecting the
    given bounding box and time window.

    Does NOT download any data.  Returns catalogue metadata only.
    """
    svc = SentinelDiscoveryService(cfg=settings)

    if not svc.check_configured():
        # Respond with empty list + configured=False rather than hard 503
        # so the wizard can display the credential warning inline.
        return SentinelDiscoverResponse(products=[], count=0, configured=False)

    try:
        products = svc.discover(
            west=body.west,
            south=body.south,
            east=body.east,
            north=body.north,
            start=body.start,
            end=body.end,
            limit=body.limit,
        )
    except SentinelConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except DiscoveryError as exc:
        logger.warning("Sentinel-1 discovery failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    items = [
        SentinelProductItem(**p.as_dict())
        for p in products
    ]
    return SentinelDiscoverResponse(products=items, count=len(items), configured=True)


# ---------------------------------------------------------------------------
# Step 2 — Environment selection
# ---------------------------------------------------------------------------

@router.post(
    "/environment/select",
    response_model=EnvironmentSelectResponse,
    summary="Acquire ERA5 and CMEMS environmental data for a satellite observation",
)
def select_environment(body: EnvironmentSelectRequest) -> EnvironmentSelectResponse:
    """Acquire ERA5 10 m wind and CMEMS near-surface current data for the observation.

    The acquisition covers the spatial bounding box and the time window from
    (observation_time - backtrack_hours) to observation_time.

    Returns paths to the downloaded NetCDF files and scalar sample values for display.
    """
    svc = EnvironmentSelectorService(cfg=settings)

    try:
        env = svc.select_for_scene(
            observation_time=body.observation_time,
            west=body.west,
            south=body.south,
            east=body.east,
            north=body.north,
            backtrack_hours=body.backtrack_hours,
            investigation_id=body.investigation_id,
            era5_override_path=body.era5_override_path,
            cmems_override_path=body.cmems_override_path,
        )
    except EnvConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except EnvironmentAcquisitionError as exc:
        logger.warning("Environment acquisition failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return EnvironmentSelectResponse(
        era5_netcdf_path=env.era5_netcdf_path,
        era5_timestamp=env.era5_timestamp.isoformat(),
        era5_u_sample=env.era5_sample.u,
        era5_v_sample=env.era5_sample.v,
        cmems_netcdf_path=env.cmems_netcdf_path,
        cmems_timestamp=env.cmems_timestamp.isoformat(),
        cmems_u_sample=env.cmems_sample.u,
        cmems_v_sample=env.cmems_sample.v,
        auto_selected=env.auto_selected,
        era5_configured=svc.era5_configured(),
        cmems_configured=svc.cmems_configured(),
    )


# ---------------------------------------------------------------------------
# Step 4 — AIS vessel search
# ---------------------------------------------------------------------------

@router.post(
    "/ais/search",
    response_model=AisSearchResponse,
    summary="Search for AIS vessel tracks near the source candidate zone",
)
def search_ais(body: AisSearchRequest) -> AisSearchResponse:
    """Discover vessels whose AIS positions fall within the spatial and temporal window.

    The bounding box should be derived from the source candidate zone computed
    by the backward drift engine (Step 3 of the wizard is the drift configuration step).
    """
    svc = AisSearchService(cfg=settings)

    if not svc.is_configured():
        return AisSearchResponse(
            vessels=[],
            total_positions=0,
            search_bbox={"west": body.west, "south": body.south, "east": body.east, "north": body.north},
            search_window_start=body.start.isoformat(),
            search_window_end=body.end.isoformat(),
            adapter_id="unconfigured",
            configured=False,
        )

    try:
        result = svc.search_near_source_zone(
            west=body.west,
            south=body.south,
            east=body.east,
            north=body.north,
            start=body.start,
            end=body.end,
            investigation_id=body.investigation_id,
        )
    except AisConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except AisSearchError as exc:
        logger.warning("AIS search failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    items = [VesselSummaryItem(**v.as_dict()) for v in result.vessels]
    return AisSearchResponse(
        vessels=items,
        total_positions=result.total_positions,
        search_bbox=result.search_bbox,
        search_window_start=result.search_window_start,
        search_window_end=result.search_window_end,
        adapter_id=result.adapter_id,
        configured=True,
    )


# ---------------------------------------------------------------------------
# Step 5 — Run attribution experiment
# ---------------------------------------------------------------------------

@router.post(
    "/run",
    response_model=ExperimentRunResponse,
    summary="Execute a real-data backward drift + attribution experiment",
)
def run_experiment(body: ExperimentRunRequest) -> ExperimentRunResponse:
    """Execute the full real-data attribution experiment:

    1. Backward drift from the observed spill centroid using real ERA5 + CMEMS data.
    2. Feature calculation for each selected vessel.
    3. Evidence consistency ranking.
    4. Persist results to SQLite store.

    Returns the full ExperimentRunResponse with source reconstruction and ranked vessels.
    """
    runner = ExperimentRunner()
    store = get_experiment_store()

    selected = [v.model_dump() for v in body.selected_vessels]

    try:
        result = runner.run(
            satellite_product_id=body.satellite_product_id,
            observation_lon=body.observation_lon,
            observation_lat=body.observation_lat,
            observation_time=body.observation_time,
            era5_netcdf_path=body.era5_netcdf_path,
            cmems_netcdf_path=body.cmems_netcdf_path,
            backtrack_hours=body.backtrack_hours,
            step_hours=body.step_hours,
            spill_area_m2=body.spill_area_m2,
            selected_vessels=selected,
        )
    except ExperimentError as exc:
        logger.warning("Experiment run failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    try:
        store.save(result)
    except Exception as exc:
        logger.error("Failed to persist experiment run %s: %s", result.run_id, exc)
        # Do NOT raise — return the result even if persistence fails

    return _result_to_response(result)


# ---------------------------------------------------------------------------
# Run management
# ---------------------------------------------------------------------------

@router.get(
    "/runs",
    response_model=ExperimentListResponse,
    summary="List persisted experiment runs",
)
def list_runs(limit: int = 50) -> ExperimentListResponse:
    """Return a summary list of persisted experiment runs, sorted by created_at descending."""
    store = get_experiment_store()
    try:
        rows = store.list_runs(limit=limit)
    except Exception as exc:
        logger.error("Failed to list experiment runs: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read experiment store")

    summaries = [
        ExperimentRunSummary(
            run_id=r["run_id"],
            satellite_product_id=r["satellite_product_id"],
            observation_time=r["observation_time"],
            backtrack_hours=r["backtrack_hours"],
            model_version=r["model_version"],
            source_lon=r["source_lon"],
            source_lat=r["source_lat"],
            source_radius_m=r["source_radius_m"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return ExperimentListResponse(runs=summaries, count=len(summaries))


@router.get(
    "/runs/{run_id}",
    response_model=ExperimentRunResponse,
    summary="Retrieve a persisted experiment run",
)
def get_run(run_id: str) -> ExperimentRunResponse:
    """Return the full result for a previously executed experiment run."""
    store = get_experiment_store()
    try:
        result = store.get_run(run_id)
    except Exception as exc:
        logger.error("Failed to retrieve run %s: %s", run_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read experiment store")

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found")

    return _result_to_response(result)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _result_to_response(result: Any) -> ExperimentRunResponse:
    vessel_items = [
        VesselFeaturesItem(
            vessel_id=v.vessel_id,
            vessel_name=v.vessel_name,
            mmsi=v.mmsi,
            min_source_distance_km=v.min_source_distance_km,
            temporal_overlap_hours=v.temporal_overlap_hours,
            trajectory_overlap_fraction=v.trajectory_overlap_fraction,
            heading_consistency=v.heading_consistency,
            speed_consistency=v.speed_consistency,
            ais_position_count=v.ais_position_count,
            ais_coverage_fraction=v.ais_coverage_fraction,
            evidence_consistency_score=v.evidence_consistency_score,
            rank=v.rank,
            has_meaningful_support=v.has_meaningful_support,
        )
        for v in result.vessels
    ]
    return ExperimentRunResponse(
        run_id=result.run_id,
        satellite_product_id=result.satellite_product_id,
        observation_time=result.observation_time.isoformat(),
        backtrack_hours=result.backtrack_hours,
        step_hours=result.step_hours,
        model_version=result.model_version,
        source_lon=result.source_lon,
        source_lat=result.source_lat,
        source_radius_m=result.source_radius_m,
        source_zone_geojson=result.source_zone_geojson,
        backward_steps=result.backward_steps,
        vessels=vessel_items,
        era5_path=result.era5_path,
        cmems_path=result.cmems_path,
        created_at=result.created_at.isoformat(),
        scientific_disclaimer=result.scientific_disclaimer,
    )


# ---------------------------------------------------------------------------
# Synthetic Scenarios & ML Attribution Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/synthetic/generate",
    response_model=SyntheticGenerateResponse,
    summary="Generate a physically consistent synthetic investigation scenario",
)
def generate_synthetic(body: SyntheticGenerateRequest) -> SyntheticGenerateResponse:
    """Generate a seeded, physically consistent synthetic oil spill scenario."""
    from app.services.synthetic_experiment.synthetic_generator import generate_synthetic_scenario

    try:
        scenario = generate_synthetic_scenario(
            seed=body.seed,
            origin_lat=body.origin_lat,
            origin_lon=body.origin_lon,
            observation_time=body.observation_time,
            wind_speed_ms=body.wind_speed_ms,
            wind_direction_deg=body.wind_direction_deg,
            current_speed_ms=body.current_speed_ms,
            current_direction_deg=body.current_direction_deg,
            candidate_count=body.candidate_count,
            backtrack_hours=body.backtrack_hours,
            step_hours=body.step_hours,
            spill_area_m2=body.spill_area_m2,
        )
        return SyntheticGenerateResponse(**scenario.to_summary_dict())
    except Exception as exc:
        logger.error("Failed to generate synthetic scenario: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Synthetic generation error: {exc}",
        )


@router.post(
    "/synthetic/run",
    response_model=SyntheticRunResponse,
    summary="Execute backward drift and ML attribution on a synthetic scenario",
)
def run_synthetic(body: SyntheticRunRequest) -> SyntheticRunResponse:
    """Run complete synthetic drift physics and trained ML candidate attribution."""
    from app.services.synthetic_experiment.synthetic_experiment_runner import run_synthetic_experiment

    try:
        result = run_synthetic_experiment(
            seed=body.seed,
            origin_lat=body.origin_lat,
            origin_lon=body.origin_lon,
            observation_time=body.observation_time,
            wind_speed_ms=body.wind_speed_ms,
            wind_direction_deg=body.wind_direction_deg,
            current_speed_ms=body.current_speed_ms,
            current_direction_deg=body.current_direction_deg,
            candidate_count=body.candidate_count,
            backtrack_hours=body.backtrack_hours,
            step_hours=body.step_hours,
            spill_area_m2=body.spill_area_m2,
            model_id=body.model_id,
        )
        # Persist to SQLite
        store = get_experiment_store()
        store.save_synthetic_run(result)
        return SyntheticRunResponse(**result)
    except Exception as exc:
        logger.error("Failed to run synthetic experiment: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Synthetic experiment run error: {exc}",
        )


@router.get(
    "/synthetic/model",
    response_model=ActiveModelResponse,
    summary="Get active ML attribution model metrics and feature weights",
)
def get_active_model() -> ActiveModelResponse:
    """Retrieve metadata and test metrics for currently deployed attribution ML model."""
    from app.services.synthetic_experiment.model_registry import get_active_model_summary

    try:
        summary = get_active_model_summary()
        return ActiveModelResponse(**summary)
    except Exception as exc:
        logger.error("Failed to load active model: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model registry error: {exc}",
        )


@router.post(
    "/synthetic/train",
    response_model=ModelTrainResponse,
    summary="Train a new attribution ML model on a synthetic group-split dataset",
)
def train_model(body: ModelTrainRequest) -> ModelTrainResponse:
    """Train StandardScaler + LogisticRegression on a newly generated multi-scenario dataset."""
    from app.services.synthetic_experiment.attribution_model import train_attribution_model
    from app.services.synthetic_experiment.model_registry import save_model
    from app.services.synthetic_experiment.training_dataset import build_synthetic_dataset

    try:
        dataset = build_synthetic_dataset(
            num_scenarios=body.num_scenarios,
            base_seed=body.base_seed,
        )
        trained = train_attribution_model(
            dataset=dataset,
            model_type=body.model_type,
            c_regularization=body.c_regularization,
        )
        save_model(trained, is_default=True)

        return ModelTrainResponse(
            model_id=trained.model_id,
            model_type=trained.model_type,
            val_metrics=trained.val_metrics.to_dict(),
            test_metrics=trained.test_metrics.to_dict(),
            feature_coefficients=trained.feature_coefficients,
            training_sample_count=trained.training_sample_count,
            training_scenario_count=trained.training_scenario_count,
            trained_at=trained.trained_at.isoformat(),
        )
    except Exception as exc:
        logger.error("Failed to train model: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model training error: {exc}",
        )


@router.get(
    "/synthetic/runs",
    summary="List historical synthetic experiment runs",
)
def list_synthetic_runs(limit: int = 50) -> list[dict[str, Any]]:
    """List recent synthetic runs from SQLite."""
    store = get_experiment_store()
    return store.list_synthetic_runs(limit=limit)


# ---------------------------------------------------------------------------
# Evaluator Investigation Workflow Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/evaluator/reference-observations",
    response_model=list[EvaluatorReferenceObservationItem],
    summary="List verified reference Sentinel-1 SAR observations with ground truth context",
)
def get_evaluator_reference_observations() -> list[dict[str, Any]]:
    """Return verified Sentinel-1 reference observations stored in public/satellite."""
    from app.services.real_experiment.evaluator_workflow import list_reference_observations
    return list_reference_observations()


@router.post(
    "/evaluator/drift-preview",
    response_model=EvaluatorDriftPreviewResponse,
    summary="Calculate backward drift trajectory and reconstructed source zone from custom wind and current inputs",
)
def post_evaluator_drift_preview(body: EvaluatorDriftPreviewRequest) -> dict[str, Any]:
    """Execute real backward drift physics using evaluator wind and current vectors."""
    from app.services.real_experiment.evaluator_workflow import calculate_backward_drift_preview
    try:
        return calculate_backward_drift_preview(
            origin_lon=body.origin_lon,
            origin_lat=body.origin_lat,
            observation_time=body.observation_time,
            wind_speed_ms=body.wind_speed_ms,
            wind_direction_deg=body.wind_direction_deg,
            current_speed_ms=body.current_speed_ms,
            current_direction_deg=body.current_direction_deg,
            backtrack_hours=body.backtrack_hours,
            step_hours=body.step_hours,
            spill_area_m2=body.spill_area_m2,
        )
    except Exception as exc:
        logger.error("Failed to calculate backward drift preview: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Drift simulation error: {exc}",
        )


@router.post(
    "/evaluator/filter-vessels",
    response_model=EvaluatorFilterVesselsResponse,
    summary="Strictly filter candidate vessels by spatial corridor and temporal intersection",
)
def post_evaluator_filter_vessels(body: EvaluatorFilterVesselsRequest) -> dict[str, Any]:
    """Filter candidates requiring both spatial corridor (<= corridor_km) and temporal overlap."""
    from app.services.real_experiment.evaluator_workflow import (
        determine_provider_status,
        filter_candidates_for_investigation,
    )
    from app.services.real_experiment.ais_search import AisSearchService

    try:
        filtering_result = filter_candidates_for_investigation(
            vessels=body.vessels,
            backward_steps=body.backward_steps,
            source_lon=body.source_lon,
            source_lat=body.source_lat,
            source_radius_m=body.source_radius_m,
            observation_time=body.observation_time,
            backtrack_hours=body.backtrack_hours,
            corridor_km=body.corridor_km,
        )

        ais_search_svc = AisSearchService(cfg=settings)
        is_live = ais_search_svc.is_configured() or bool(body.vessels)
        p_status, p_desc = determine_provider_status(
            has_live_provider=is_live,
            total_found=len(body.vessels),
            eligible_count=filtering_result["eligible_count"],
        )

        return {
            **filtering_result,
            "provider_status": p_status,
            "provider_description": p_desc,
        }
    except Exception as exc:
        logger.error("Failed to filter candidate vessels: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Candidate filtering error: {exc}",
        )


@router.post(
    "/evaluator/run",
    summary="Run full end-to-end evaluator investigation and persist to SQLite",
)
def post_evaluator_run(body: EvaluatorRunRequest) -> dict[str, Any]:
    """Execute complete interactive investigation, ML attribution, and persistence."""
    from app.services.real_experiment.evaluator_workflow import run_evaluator_investigation

    try:
        return run_evaluator_investigation(
            selected_image_id=body.selected_image_id,
            observation_lon=body.observation_lon,
            observation_lat=body.observation_lat,
            observation_time=body.observation_time,
            wind_speed_ms=body.wind_speed_ms,
            wind_direction_deg=body.wind_direction_deg,
            current_speed_ms=body.current_speed_ms,
            current_direction_deg=body.current_direction_deg,
            corridor_km=body.corridor_km,
            backtrack_hours=body.backtrack_hours,
            step_hours=body.step_hours,
            spill_area_m2=body.spill_area_m2,
            custom_vessels=body.custom_vessels,
            model_id=body.model_id,
        )
    except Exception as exc:
        logger.error("Failed to run evaluator investigation: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Evaluator investigation execution error: {exc}",
        )


@router.get(
    "/evaluator/investigations",
    response_model=list[EvaluatorInvestigationSummaryItem],
    summary="List saved evaluator investigations for instant replay without re-running",
)
def list_evaluator_investigations(limit: int = 50) -> list[dict[str, Any]]:
    """List saved evaluator investigations from SQLite."""
    store = get_experiment_store()
    return store.list_evaluator_investigations(limit=limit)


@router.get(
    "/evaluator/investigations/{investigation_id}",
    summary="Get full saved evaluator investigation record by ID",
)
def get_evaluator_investigation(investigation_id: str) -> dict[str, Any]:
    """Fetch complete immutable investigation record for instant replay."""
    store = get_experiment_store()
    record = store.get_evaluator_investigation(investigation_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluator investigation '{investigation_id}' not found.",
        )
    return record

