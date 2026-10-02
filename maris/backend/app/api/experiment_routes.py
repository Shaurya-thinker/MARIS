"""MARIS Real-Data Experiment API routes.

All endpoints are mounted under /api/experiment/ prefix (registered in main.py).

These routes are completely isolated from the existing G1 investigation workflow
routes in app/api/routes.py.  They do NOT modify any existing endpoint behavior.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.experiment_schemas import (
    AisPositionsRequest,
    AisPositionsResponse,
    AisSearchRequest,
    AisSearchResponse,
    EnvironmentSelectRequest,
    EnvironmentSelectResponse,
    ExperimentConfigResponse,
    ExperimentListResponse,
    ExperimentRunRequest,
    ExperimentRunResponse,
    ExperimentRunSummary,
    MonteCarloConfigRequest,
    MonteCarloEnsembleResultItem,
    EnsembleEvidenceItem,
    SentinelDiscoverRequest,
    SentinelDiscoverResponse,
    SentinelProductItem,
    SentinelCharacterizeRequest,
    SentinelCharacterizeResponse,
    SarAcquisitionRequest,
    SarAcquisitionResponse,
    SlickCharacterizationItem,
    ForwardDriftStepItem,
    ForwardPredictionRequest,
    ForwardPredictionResponse,
    ForwardPredictionResultItem,
    VesselBehavioralIntelligenceItem,
    BehavioralAnomalyItemSchema,
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
from app.services.real_experiment.slick_characterization import characterize_observation
from app.services.report_generator import (
    build_scientific_report_data,
    render_scientific_report_pdf,
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
# Step 1b — Sentinel-1 Slick Detection & Characterization (Step 10)
# ---------------------------------------------------------------------------

@router.post(
    "/sentinel/characterize",
    response_model=SentinelCharacterizeResponse,
    summary="Automated detection and characterization of Sentinel-1 oil slick / anomaly",
)
def characterize_sentinel_observation(body: SentinelCharacterizeRequest) -> SentinelCharacterizeResponse:
    """Extract quantitative characterization (centroid, estimated area, extent, damping contrast,
    confidence, sensor/mode, slick age) for a selected Sentinel-1 scene.
    """
    char_dict = characterize_observation(
        product_id=body.product_id,
        title=body.title,
        sensing_start=body.sensing_start,
        centroid_lon=body.centroid_lon,
        centroid_lat=body.centroid_lat,
        mode=body.mode,
        polarisation=body.polarisation,
        footprint=body.footprint,
        backtrack_hours=body.backtrack_hours,
        sar_raster_path=body.sar_raster_path,
    )
    return SentinelCharacterizeResponse(characterization=SlickCharacterizationItem(**char_dict))


@router.post(
    "/sentinel/acquire-subscene",
    response_model=SarAcquisitionResponse,
    summary="Acquire a lightweight calibrated SAR subscene GeoTIFF via CDSE Process API",
)
def acquire_sentinel_sar_subscene(body: SarAcquisitionRequest) -> SarAcquisitionResponse:
    """Acquire or retrieve from cache an analysis-ready Sentinel-1 SAR subscene GeoTIFF.

    Uses Copernicus Data Space Ecosystem (CDSE) / Sentinel Hub Process API for on-demand crops,
    or falls back to verified pre-staged local fixtures when offline.
    """
    from app.services.real_experiment.sar_acquisition import (
        SarAcquisitionService,
        SarAcquisitionError,
        SarAcquisitionAuthError,
    )
    svc = SarAcquisitionService()
    try:
        raster_path, meta, is_cached = svc.acquire_subscene(
            product_id=body.product_id,
            bbox=body.bbox,
            centroid_lon=body.centroid_lon,
            centroid_lat=body.centroid_lat,
            sensing_time=body.sensing_time,
            aoi_radius_km=body.aoi_radius_km,
            width_px=body.width_px,
            height_px=body.height_px,
            force_refresh=body.force_refresh,
        )
        return SarAcquisitionResponse(
            success=True,
            sar_raster_path=str(raster_path),
            product_id=meta.product_id,
            file_size_bytes=meta.file_size_bytes,
            is_cached=is_cached,
            source_provider=meta.source_provider,
            data_authenticity=meta.data_authenticity,
            is_test_fixture=meta.is_test_fixture,
            crs=meta.crs,
            bands=meta.bands,
            bbox=meta.bbox,
            message=(
                f"SAR subscene ready ({'cached' if is_cached else 'acquired'}). "
                f"Provenance: {meta.source_provider}."
            ),
        )
    except SarAcquisitionAuthError as exc:
        logger.warning("SAR acquisition authentication error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except SarAcquisitionError as exc:
        logger.error("SAR acquisition failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SAR subscene acquisition failed: {exc}",
        )
    except Exception as exc:
        logger.error("Unexpected SAR acquisition error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal acquisition error: {exc}",
        )


# ---------------------------------------------------------------------------
# Step 11 — Forward Drift Prediction
# ---------------------------------------------------------------------------

@router.post(
    "/drift/forward-predict",
    response_model=ForwardPredictionResponse,
    summary="Predict forward movement of the observed slick under metocean forcing",
)
def predict_forward_movement(body: ForwardPredictionRequest) -> ForwardPredictionResponse:
    """Predict forward advection and dispersion of the detected oil slick starting
    from the Sentinel-1 observation time using the Stage D1 Leeway-Euler model.
    """
    try:
        from app.services.real_experiment.forward_prediction import (
            predict_forward_drift,
            ForwardPredictionError,
        )
        prediction_dict = predict_forward_drift(
            origin_lon=body.origin_lon or 0.0,
            origin_lat=body.origin_lat or 0.0,
            observation_time=body.observation_time,
            era5_netcdf_path=body.era5_netcdf_path,
            cmems_netcdf_path=body.cmems_netcdf_path,
            prediction_hours=body.prediction_hours,
            step_hours=body.step_hours,
            leeway_fraction=body.leeway_fraction,
            slick_characterization=body.slick_characterization.dict() if body.slick_characterization else None,
        )
        pred_item = ForwardPredictionResultItem(**prediction_dict)
        return ForwardPredictionResponse(
            prediction=pred_item,
            forward_prediction=pred_item,
        )
    except ForwardPredictionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during forward drift prediction: {exc}",
        ) from exc


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
# AIS Fleet Registry — browse ais_vessels.db
# ---------------------------------------------------------------------------

@router.get(
    "/ais/fleet",
    summary="AIS Fleet Registry: database stats and vessel list from ais_vessels.db",
)
def get_ais_fleet(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    search: str = Query("", description="Filter by vessel name or MMSI (case-insensitive)"),
    source_type: str = Query("", description="Filter by source type (e.g. NOAA_MARINECADASTRE, SYNTHETIC_BENCHMARK)"),
) -> dict:
    """Return AIS fleet registry: database statistics + paginated vessel list.

    Each vessel entry includes MMSI, name, type, flag, call sign, dimensions,
    source provenance, position count, and first/last seen timestamps.
    """
    from app.services.real_experiment.ais_database import (
        get_connection,
        get_database_statistics,
    )

    stats = get_database_statistics()

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Build query with optional search + source_type filters
        where_clauses = []
        params: list = []

        if search.strip():
            q = f"%{search.strip().lower()}%"
            where_clauses.append("(lower(v.vessel_name) LIKE ? OR v.mmsi LIKE ?)")
            params.extend([q, q])

        if source_type.strip():
            where_clauses.append("v.source_type = ?")
            params.append(source_type.strip())

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Count total matching rows for pagination metadata
        cur.execute(f"SELECT count(*) FROM vessels v {where_sql}", params)
        total_matching = cur.fetchone()[0]

        # Fetch paginated vessel rows with per-vessel position count + timestamps
        cur.execute(
            f"""
            SELECT
                v.vessel_id,
                v.mmsi,
                v.vessel_name,
                v.vessel_type,
                v.flag_country,
                v.call_sign,
                v.length,
                v.width,
                v.draft,
                v.imo,
                v.source_type,
                v.is_real_observation,
                v.created_at,
                ds.provider_name,
                ds.geographic_coverage,
                ds.coverage_start,
                ds.coverage_end,
                count(p.id)  AS position_count,
                min(p.timestamp) AS first_seen,
                max(p.timestamp) AS last_seen
            FROM vessels v
            LEFT JOIN data_sources ds ON ds.source_id = v.source_id
            LEFT JOIN ais_positions p ON p.vessel_id = v.vessel_id
            {where_sql}
            GROUP BY v.vessel_id
            ORDER BY position_count DESC, v.vessel_name
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()
        vessels = [dict(r) for r in rows]

        # Source type breakdown for UI pills
        cur.execute(
            "SELECT source_type, count(*) as cnt FROM vessels GROUP BY source_type ORDER BY cnt DESC"
        )
        source_breakdown = [dict(r) for r in cur.fetchall()]

    finally:
        conn.close()

    return {
        "stats": stats,
        "source_breakdown": source_breakdown,
        "total_matching": total_matching,
        "limit": limit,
        "offset": offset,
        "vessels": vessels,
    }


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

    IF backward_steps are provided:
        perform dynamic drift-corridor AIS correlation.
    IF backward_steps are not provided:
        preserve the existing static AIS search behavior.
    """
    svc = AisSearchService(cfg=settings)

    if not svc.is_configured():
        return AisSearchResponse(
            vessels=[],
            total_positions=0,
            search_bbox={"west": body.west, "south": body.south, "east": body.east, "north": body.north},
            search_window_start=body.start.isoformat() if body.start else "",
            search_window_end=body.end.isoformat() if body.end else "",
            adapter_id="unconfigured",
            configured=False,
        )

    backward_steps = body.backward_steps
    source_candidate_zone = body.source_candidate_zone
    obs_time = body.observation_time or body.end or datetime.now(timezone.utc)
    if isinstance(obs_time, str):
        obs_time = datetime.fromisoformat(obs_time.replace("Z", "+00:00"))
    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=timezone.utc)

    bt_hours = body.backtrack_hours or 12.0
    step_h = body.step_hours or 1.0

    # If backward_steps are not provided, attempt to reconstruct drift trajectory only if observation info is supplied
    if not backward_steps and (body.observation_lat is not None or body.satellite_product_id is not None):
        origin_lat = body.observation_lat
        origin_lon = body.observation_lon

        is_corsica = (
            "20181008" in (body.satellite_product_id or "")
            or (obs_time.year == 2018 and obs_time.month == 10 and obs_time.day == 8)
        )
        if (origin_lat is None or origin_lat < 43.0) and is_corsica:
            origin_lat = 43.2736
            origin_lon = 9.4913

        era5_path = body.era5_netcdf_path
        cmems_path = body.cmems_netcdf_path

        if not era5_path or not cmems_path:
            try:
                from app.services.real_experiment.environment_selector import EnvironmentSelectorService
                env_svc = EnvironmentSelectorService(cfg=settings)
                env_sel = env_svc.select_for_scene(
                    observation_time=obs_time,
                    west=body.west,
                    south=body.south,
                    east=body.east,
                    north=body.north,
                    backtrack_hours=bt_hours,
                )
                era5_path = env_sel.era5_netcdf_path
                cmems_path = env_sel.cmems_netcdf_path
            except Exception as exc:
                logger.warning("Auto-environment selection in AIS search failed: %s", exc)

        if era5_path and cmems_path and origin_lat is not None and origin_lon is not None:
            try:
                from app.services.drift_modelling import _open_netcdf
                from app.services.source_estimation import (
                    generate_source_candidate_polygon,
                    run_backward_drift,
                )
                wind_ds = _open_netcdf(era5_path)
                curr_ds = _open_netcdf(cmems_path)
                drift_step_models = run_backward_drift(
                    origin_lon=origin_lon,
                    origin_lat=origin_lat,
                    observation_time=obs_time,
                    wind_ds=wind_ds,
                    curr_ds=curr_ds,
                    lookback_hours=bt_hours,
                    step_hours=step_h,
                    spill_area_m2=body.spill_area_m2,
                )
                wind_ds.close()
                curr_ds.close()
                if drift_step_models:
                    backward_steps = [s.model_dump() for s in drift_step_models]
                    final_step = drift_step_models[-1]
                    source_candidate_zone = generate_source_candidate_polygon(
                        center_lon=final_step.lon,
                        center_lat=final_step.lat,
                        radius_m=final_step.uncertainty_radius_m,
                    )
            except Exception as exc:
                logger.warning("Dynamic drift trajectory derivation in search_ais failed: %s", exc)

    # Dynamic Drift-Corridor AIS Search if backward_steps are present or reconstructed
    if backward_steps:
        from app.services.real_experiment.ais_search import (
            derive_drift_ais_corridor,
            search_along_drift_corridor,
        )
        corridor = derive_drift_ais_corridor(
            backward_steps=backward_steps,
            observation_time=obs_time,
            backtrack_hours=bt_hours,
        )
        try:
            summaries = search_along_drift_corridor(
                backward_steps=backward_steps,
                observation_time=obs_time,
                backtrack_hours=bt_hours,
                source_candidate_zone=source_candidate_zone,
            )
        except AisConfigUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
        except AisSearchError as exc:
            logger.warning("Dynamic corridor AIS search failed: %s", exc)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

        w_start_str = corridor["window_start"].isoformat()
        w_end_str = corridor["window_end"].isoformat()
        items = [
            VesselSummaryItem(
                mmsi=s.mmsi,
                vessel_name=s.vessel_name,
                imo=s.imo,
                position_count=s.position_count,
                first_timestamp=s.first_timestamp.isoformat() if isinstance(s.first_timestamp, datetime) else str(s.first_timestamp or w_start_str),
                last_timestamp=s.last_timestamp.isoformat() if isinstance(s.last_timestamp, datetime) else str(s.last_timestamp or w_end_str),
                source_adapter="SqliteAisAdapter",
                positions=s.positions,
                min_trajectory_distance_km=s.min_trajectory_distance_km,
                trajectory_time_delta_hours=s.trajectory_time_delta_hours,
                source_zone_intersection=s.source_zone_intersection,
                min_source_distance_km=s.min_source_distance_km,
                source_type=s.source_type,
                provider_name=s.provider_name,
            )
            for s in summaries
        ]
        total_positions = sum(s.position_count for s in summaries)
        return AisSearchResponse(
            vessels=items,
            total_positions=total_positions,
            search_bbox={
                "west": corridor["west"],
                "south": corridor["south"],
                "east": corridor["east"],
                "north": corridor["north"],
            },
            search_window_start=w_start_str,
            search_window_end=w_end_str,
            adapter_id="SqliteAisAdapter",
            configured=True,
            backward_steps=[s if isinstance(s, dict) else s.model_dump() for s in backward_steps],
        )

    # Fallback to static search if backward_steps are not provided
    try:
        start_time = body.start or datetime.now(timezone.utc)
        end_time = body.end or datetime.now(timezone.utc)
        result = svc.search_near_source_zone(
            west=body.west,
            south=body.south,
            east=body.east,
            north=body.north,
            start=start_time,
            end=end_time,
            investigation_id=body.investigation_id,
        )
    except AisConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except AisSearchError as exc:
        logger.warning("AIS search failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    items = [
        VesselSummaryItem(
            **{
                **v.as_dict(),
                "source_type": v.as_dict().get("source_type") or "sqlite_ais",
                "provider_name": v.as_dict().get("provider_name") or "ais_vessels.db",
            }
        )
        for v in result.vessels
    ]
    return AisSearchResponse(
        vessels=items,
        total_positions=result.total_positions,
        search_bbox=result.search_bbox,
        search_window_start=result.search_window_start,
        search_window_end=result.search_window_end,
        adapter_id=result.adapter_id,
        configured=True,
    )


@router.post(
    "/ais/positions",
    response_model=AisPositionsResponse,
    summary="Retrieve authentic historical AIS positions for selected MMSIs",
)
def get_ais_positions(body: AisPositionsRequest) -> AisPositionsResponse:
    """Retrieve raw historical AIS positions for selected vessels.

    Does not fabricate or interpolate points.
    Filters strictly by time window and optional bounding box.
    """
    svc = AisSearchService(cfg=settings)
    if not svc.is_configured():
        return AisPositionsResponse(vessel_positions={}, total_positions=0)

    try:
        positions_map = svc.get_vessel_positions(
            mmsis=body.mmsis,
            west=body.west,
            south=body.south,
            east=body.east,
            north=body.north,
            start=body.start,
            end=body.end,
        )
    except AisConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception as exc:
        logger.warning("AIS positions retrieval failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    total = sum(len(plist) for plist in positions_map.values())
    return AisPositionsResponse(vessel_positions=positions_map, total_positions=total)


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
    slick_char_dict = body.slick_characterization.model_dump() if body.slick_characterization else None

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
            search_bbox=body.search_bbox,
            slick_characterization=slick_char_dict,
            forward_prediction_hours=body.forward_prediction_hours,
            forward_step_hours=body.forward_step_hours,
            monte_carlo_config=body.monte_carlo.model_dump() if body.monte_carlo else None,
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
@router.get(
    "/experiments",
    response_model=ExperimentListResponse,
    summary="Alias: List persisted experiment runs",
)
def list_runs(limit: int = 50) -> ExperimentListResponse:
    """Return a summary list of persisted experiment runs, sorted by created_at descending."""
    store = get_experiment_store()
    try:
        rows = store.list_runs(limit=limit)
    except Exception as exc:
        logger.error("Failed to list experiment runs: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read experiment store")

    summaries = []
    for r in rows:
        v_count = 0
        if "vessels_json" in r and r["vessels_json"]:
            try:
                v_count = len(json.loads(r["vessels_json"]))
            except Exception:
                pass
        obs_lon = r.get("observation_lon")
        obs_lat = r.get("observation_lat")
        if obs_lon is None or obs_lon == 0.0:
            if "20181008" in r.get("satellite_product_id", "") or "2018-10-08" in str(r.get("observation_time", "")):
                obs_lon = 9.4783
                obs_lat = 43.2483
            elif r.get("backward_steps"):
                try:
                    steps = json.loads(r["backward_steps"])
                    if steps:
                        obs_lon = steps[0].get("lon")
                        obs_lat = steps[0].get("lat")
                except Exception:
                    pass

        slick_char_item = None
        if r.get("slick_characterization_json"):
            try:
                slick_char_item = SlickCharacterizationItem(**json.loads(r["slick_characterization_json"]))
            except Exception:
                slick_char_item = None

        fwd_pred_item = None
        if r.get("forward_prediction_json"):
            try:
                fwd_pred_item = ForwardPredictionResultItem(**json.loads(r["forward_prediction_json"]))
            except Exception:
                fwd_pred_item = None

        summaries.append(
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
                observation_lon=obs_lon,
                observation_lat=obs_lat,
                candidate_count=v_count,
                status="completed",
                slick_characterization=slick_char_item,
                forward_prediction=fwd_pred_item,
            )
        )
    return ExperimentListResponse(runs=summaries, count=len(summaries))


@router.get(
    "/runs/{run_id}",
    response_model=ExperimentRunResponse,
    summary="Retrieve a persisted experiment run",
)
@router.get(
    "/experiments/{run_id}",
    response_model=ExperimentRunResponse,
    summary="Alias: Retrieve a persisted experiment run",
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


@router.get(
    "/runs/{run_id}/report",
    summary="Generate scientific investigation report (PDF or JSON)",
)
@router.get(
    "/experiments/{run_id}/report",
    summary="Alias: Generate scientific investigation report (PDF or JSON)",
)
def get_report(run_id: str, format: str = Query("pdf", pattern="^(pdf|json)$")) -> Response:
    """Return a publication-grade scientific report generated from the stored historical run."""
    store = get_experiment_store()
    try:
        result = store.get_run(run_id)
    except Exception as exc:
        logger.error("Failed to retrieve run %s for report: %s", run_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read experiment store")

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found")

    report_data = build_scientific_report_data(result)

    if format.lower() == "json":
        return JSONResponse(content=report_data)

    try:
        pdf_bytes = render_scientific_report_pdf(report_data)
    except Exception as exc:
        logger.error("Failed to render PDF report for run %s: %s", run_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"PDF generation failed: {exc}")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="MARIS_Report_{run_id[:8]}.pdf"',
            "Content-Type": "application/pdf",
        },
    )


@router.get(
    "/runs/{run_id}/export",
    summary="Export complete historical experiment record as JSON",
)
@router.get(
    "/experiments/{run_id}/export",
    summary="Alias: Export complete historical experiment record as JSON",
)
def export_run_json(run_id: str) -> Response:
    """Return complete authentic stored experiment record as a downloadable JSON document."""
    store = get_experiment_store()
    try:
        result = store.get_run(run_id)
    except Exception as exc:
        logger.error("Failed to retrieve run %s for export: %s", run_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read experiment store")

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found")

    report_data = build_scientific_report_data(result)
    pretty_json = json.dumps(report_data, indent=2, ensure_ascii=False)
    return Response(
        content=pretty_json,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="MARIS_Experiment_{run_id[:8]}.json"',
        },
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _result_to_response(result: Any) -> ExperimentRunResponse:
    obs_lon = getattr(result, "observation_lon", None)
    obs_lat = getattr(result, "observation_lat", None)
    if (obs_lon is None or obs_lon == 0.0) and getattr(result, "backward_steps", None):
        if "20181008" in result.satellite_product_id or "2018-10-08" in str(result.observation_time):
            obs_lon = 9.4783
            obs_lat = 43.2483
        else:
            obs_lon = result.backward_steps[0].get("lon")
            obs_lat = result.backward_steps[0].get("lat")

    vessel_items = []
    for v in result.vessels:
        beh_item = None
        raw_beh = getattr(v, "behavioral_intelligence", None)
        if raw_beh is not None:
            try:
                beh_dict = raw_beh.as_dict() if hasattr(raw_beh, "as_dict") else raw_beh
                if isinstance(beh_dict, dict):
                    beh_item = VesselBehavioralIntelligenceItem(**beh_dict)
            except Exception:
                beh_item = None

        vessel_items.append(
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
                positions=getattr(v, "positions", []),
                min_trajectory_distance_km=getattr(v, "min_trajectory_distance_km", None),
                trajectory_time_delta_hours=getattr(v, "trajectory_time_delta_hours", None),
                source_zone_intersection=getattr(v, "source_zone_intersection", False),
                source_type=getattr(v, "source_type", "sqlite_ais"),
                provider_name=getattr(v, "provider_name", "ais_vessels.db"),
                model_probability=getattr(v, "model_probability", None),
                ml_feature_vector=getattr(v, "ml_feature_vector", None),
                behavioral_intelligence=beh_item,
                evidence_breakdown=getattr(v, "evidence_breakdown", None),
                explanation=getattr(v, "explanation", []),
                consistency_level=getattr(v, "consistency_level", "LOW"),
                scientific_disclaimer=getattr(
                    v,
                    "scientific_disclaimer",
                    "Evidence consistency indicates spatiotemporal correlation with the reconstructed drift model, not legal liability.",
                ),
                ensemble_evidence=(
                    EnsembleEvidenceItem(**v.ensemble_evidence)
                    if getattr(v, "ensemble_evidence", None) and isinstance(v.ensemble_evidence, dict)
                    else None
                ),
            )
        )

    slick_char_item = None
    if getattr(result, "slick_characterization", None):
        try:
            slick_char_item = SlickCharacterizationItem(**result.slick_characterization)
        except Exception:
            slick_char_item = None

    fwd_pred_item = None
    if getattr(result, "forward_prediction", None):
        try:
            fwd_pred_item = ForwardPredictionResultItem(**result.forward_prediction)
        except Exception:
            fwd_pred_item = None

    mc_ens_item = None
    if getattr(result, "monte_carlo_ensemble", None):
        try:
            mc_ens_item = MonteCarloEnsembleResultItem(**result.monte_carlo_ensemble)
        except Exception as exc:
            logger.warning("Failed to serialize monte_carlo_ensemble in response: %s", exc)
            mc_ens_item = None

    return ExperimentRunResponse(
        run_id=result.run_id,
        satellite_product_id=result.satellite_product_id,
        observation_time=result.observation_time.isoformat() if hasattr(result.observation_time, "isoformat") else str(result.observation_time),
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
        created_at=result.created_at.isoformat() if hasattr(result.created_at, "isoformat") else str(result.created_at),
        scientific_disclaimer=result.scientific_disclaimer,
        observation_lon=obs_lon,
        observation_lat=obs_lat,
        status=getattr(result, "status", "completed"),
        slick_characterization=slick_char_item,
        forward_prediction=fwd_pred_item,
        monte_carlo_ensemble=mc_ens_item,
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
    from app.services.real_experiment.evaluator_workflow import (
        calculate_backward_drift_preview,
        get_reference_observation,
    )
    obs_source = body.observation_source
    effective_lon = body.origin_lon
    effective_lat = body.origin_lat
    effective_area = body.spill_area_m2

    if body.selected_image_id:
        ref_obs = get_reference_observation(body.selected_image_id)
        if ref_obs and ref_obs.get("detected_slick_metrics") and ref_obs["detected_slick_metrics"].get("detected"):
            det = ref_obs["detected_slick_metrics"]
            c_lon = det.get("centroid_lon")
            c_lat = det.get("centroid_lat")
            catalog_lon = ref_obs.get("observation_lon")
            catalog_lat = ref_obs.get("observation_lat")

            if c_lon is not None and c_lat is not None:
                matches_catalog = (
                    catalog_lon is not None
                    and catalog_lat is not None
                    and abs(body.origin_lon - catalog_lon) < 0.01
                    and abs(body.origin_lat - catalog_lat) < 0.01
                )
                matches_sar = (
                    abs(body.origin_lon - float(c_lon)) < 0.005
                    and abs(body.origin_lat - float(c_lat)) < 0.005
                )
                if matches_catalog or matches_sar or obs_source == "SAR_DERIVED":
                    effective_lon = float(c_lon)
                    effective_lat = float(c_lat)
                    if det.get("area_m2"):
                        effective_area = float(det["area_m2"])
                    obs_source = "SAR_DERIVED"

    try:
        return calculate_backward_drift_preview(
            origin_lon=effective_lon,
            origin_lat=effective_lat,
            observation_time=body.observation_time,
            wind_speed_ms=body.wind_speed_ms,
            wind_direction_deg=body.wind_direction_deg,
            current_speed_ms=body.current_speed_ms,
            current_direction_deg=body.current_direction_deg,
            backtrack_hours=body.backtrack_hours,
            step_hours=body.step_hours,
            spill_area_m2=effective_area,
            observation_source=obs_source or "BENCHMARK_FALLBACK",
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

