"""Stage G1 — Investigation Workflow Orchestration Service for MARIS.

Coordinates the end-to-end scientific pipeline (B1 -> B2 -> B3 -> C1 -> D1 -> D3 ->
E1 -> E2 -> E3 -> F1 -> F2 -> F3) without modifying scientific behavior or
duplicating domain algorithms.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.models.asset import Asset
from app.models.common import AreaOfInterest, AssetType, InvestigationStatus, Provenance, TimeWindow
from app.models.drift import DriftResult
from app.models.investigation import Investigation
from app.models.investigation_api import (
    ArtifactSummary,
    InvestigationListItem,
    InvestigationRunRequest,
    InvestigationRunResponse,
    InvestigationStatusResponse,
    StageError,
)
from app.models.satellite import SatelliteScene, SpillDetection
from app.services.behavioral_intelligence import (
    BehavioralIntelligenceError,
    analyze_candidate_behavior,
)
from app.services.candidate_ranking import (
    CandidateRankingError,
    rank_candidates,
)
from app.services.candidate_vessels import (
    CandidateVesselError,
    generate_candidate_vessels_for_spill,
)
from app.services.drift_modelling import (
    DriftModellingError,
    compute_drift_for_spill,
    extract_centroid,
)
from app.services.environmental_acquisition import (
    EnvironmentalAcquisitionError,
    acquire_environmental_data_for_scene,
)
from app.services.evidence_fusion import (
    EvidenceFusionError,
    fuse_evidence,
)
from app.services.explainability import (
    ExplainabilityError,
    generate_explainability_report,
)
from app.services.sentinel1_ingestion import (
    Sentinel1IngestionError,
    ingest_sentinel1_artifact,
)
from app.services.sentinel1_preprocessing import (
    Sentinel1PreprocessingError,
    preprocess_sentinel1_scene,
)
from app.services.source_estimation import (
    SourceEstimationError,
    compute_source_estimate_for_spill,
)
from app.services.spill_detection import (
    SpillDetectionError,
    detect_spills_from_sar_scene,
)
from app.services.trajectory_analysis import (
    TrajectoryAnalysisError,
    analyze_candidate_trajectories,
)


class WorkflowExecutionError(Exception):
    """Raised when investigation workflow execution fails at a scientific stage."""

    def __init__(self, stage: str, error_type: str, message: str) -> None:
        super().__init__(f"Stage {stage} failed ({error_type}): {message}")
        self.stage = stage
        self.error_type = error_type
        self.message = message


class InMemoryInvestigationStore:
    """Process-local in-memory store for investigation lifecycle tracking."""

    def __init__(self, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: f"inv-{uuid4().hex[:12]}")
        self._investigations: dict[str, Investigation] = {}
        self._status: dict[str, InvestigationStatusResponse] = {}
        self._order: list[str] = []

    def create(
        self,
        name: str,
        area_of_interest: AreaOfInterest,
        time_window: TimeWindow,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        investigation_id: str | None = None,
    ) -> Investigation:
        inv_id = investigation_id or self._id_factory()
        now = datetime.now(timezone.utc)
        inv = Investigation(
            id=inv_id,
            name=name,
            status=InvestigationStatus.CREATED,
            area_of_interest=area_of_interest,
            time_window=time_window,
            created_at=now,
            description=description,
            metadata=dict(metadata or {}),
            asset_ids=[],
            evidence_ids=[],
        )
        self._investigations[inv_id] = inv
        self._status[inv_id] = InvestigationStatusResponse(
            investigation_id=inv_id,
            status=InvestigationStatus.CREATED,
            current_stage=None,
            completed_stages=[],
            available_artifacts=[],
            errors=[],
        )
        self._order.append(inv_id)
        return inv

    def get(self, investigation_id: str) -> Investigation | None:
        return self._investigations.get(investigation_id)

    def list(self) -> list[InvestigationListItem]:
        results: list[InvestigationListItem] = []
        for inv_id in self._order:
            inv = self._investigations.get(inv_id)
            if inv is not None:
                results.append(
                    InvestigationListItem(
                        id=inv.id,
                        name=inv.name,
                        status=inv.status,
                        created_at=inv.created_at,
                        description=inv.description,
                        asset_count=len(inv.asset_ids),
                    )
                )
        return results

    def get_status(self, investigation_id: str) -> InvestigationStatusResponse | None:
        return self._status.get(investigation_id)

    def update_status(
        self,
        investigation_id: str,
        status: InvestigationStatus | None = None,
        current_stage: str | None = None,
        completed_stages: list[str] | None = None,
        errors: list[StageError] | None = None,
        available_artifacts: list[str] | None = None,
    ) -> InvestigationStatusResponse:
        current = self._status.get(investigation_id)
        if current is None:
            raise KeyError(f"Investigation '{investigation_id}' not found")

        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
            if investigation_id in self._investigations:
                inv = self._investigations[investigation_id]
                self._investigations[investigation_id] = inv.model_copy(update={"status": status})
        if current_stage is not None:
            updates["current_stage"] = current_stage
        if completed_stages is not None:
            updates["completed_stages"] = completed_stages
        if errors is not None:
            updates["errors"] = errors
        if available_artifacts is not None:
            updates["available_artifacts"] = available_artifacts

        new_status = current.model_copy(update=updates)
        self._status[investigation_id] = new_status
        return new_status

    def attach_assets(self, investigation_id: str, asset_ids: Sequence[str]) -> None:
        if investigation_id in self._investigations:
            inv = self._investigations[investigation_id]
            existing = list(inv.asset_ids)
            for aid in asset_ids:
                if aid and aid not in existing:
                    existing.append(aid)
            self._investigations[investigation_id] = inv.model_copy(update={"asset_ids": existing})

        if investigation_id in self._status:
            st = self._status[investigation_id]
            st_existing = list(st.available_artifacts)
            for aid in asset_ids:
                if aid and aid not in st_existing:
                    st_existing.append(aid)
            self._status[investigation_id] = st.model_copy(update={"available_artifacts": st_existing})

    def clear(self) -> None:
        self._investigations.clear()
        self._status.clear()
        self._order.clear()


default_investigation_store = InMemoryInvestigationStore()


def list_investigation_artifacts(
    investigation_id: str,
    registry: AssetRegistry | None = None,
) -> list[ArtifactSummary]:
    """Retrieve and summarize all artifacts registered for an investigation."""
    target_registry = registry or default_asset_registry
    assets = target_registry.list_for_investigation(investigation_id)
    summaries: list[ArtifactSummary] = []

    for asset in assets:
        prov = asset.provenance or Provenance()
        extra = prov.extra or {}
        upstream_ids: list[str] = []
        for key in (
            "parent_asset_id",
            "spill_detection_id",
            "source_estimate_id",
            "candidate_generation_id",
            "trajectory_analysis_id",
            "behavioral_intelligence_id",
            "evidence_fusion_id",
            "candidate_ranking_id",
        ):
            val = extra.get(key) or asset.metadata.get(key)
            if val and str(val) not in upstream_ids:
                upstream_ids.append(str(val))

        val_status = (
            asset.metadata.get("validation_classification")
            or asset.metadata.get("validation_status")
            or ("PASSED" if asset.metadata.get("validation_passed") else None)
        )

        summaries.append(
            ArtifactSummary(
                asset_id=asset.id,
                investigation_id=asset.investigation_id,
                asset_type=asset.type,
                provider=asset.provider,
                source=asset.source,
                location=asset.location,
                acquisition_time=asset.acquisition_time,
                processing_level=prov.processing_level,
                validation_status=val_status,
                provenance=prov,
                upstream_asset_ids=upstream_ids,
                metadata=dict(asset.metadata),
            )
        )
    return summaries


def run_investigation_workflow(
    investigation_id: str,
    payload: InvestigationRunRequest,
    store: InMemoryInvestigationStore | None = None,
    registry: AssetRegistry | None = None,
) -> InvestigationRunResponse:
    """Orchestrate scientific pipeline stages B1 through F3 sequentially.

    Dependency order:
        B1: Sentinel-1 Ingestion
        B2: SAR Preprocessing (calibrated sigma0 GeoTIFF)
        B3: Spill Detection & Geometry Extraction
        C1: Environmental Acquisition (ERA5 Wind & CMEMS Current)
        D1: Forward Drift Modelling
        D3: Backward Drift / Source Estimation
        E1: AIS Candidate Vessel Generation
        E2: AIS Trajectory Analysis
        E3: Behavioural Intelligence Analysis
        F1: Multi-Source Evidence Fusion
        F2: Candidate Scoring & Ranking
        F3: Explainability & Uncertainty Reporting
    """
    target_store = store or default_investigation_store
    target_registry = registry or default_asset_registry

    investigation = target_store.get(investigation_id)
    if investigation is None:
        raise KeyError(f"Investigation '{investigation_id}' not found")

    # Initialize workflow status
    completed_stages: list[str] = []
    artifacts: dict[str, str] = {}
    errors: list[StageError] = []

    target_store.update_status(
        investigation_id,
        status=InvestigationStatus.PROCESSING,
        current_stage="B1",
        completed_stages=completed_stages,
        errors=[],
    )

    scene: SatelliteScene | None = None
    b1_asset: Asset | None = None
    b2_asset: Asset | None = None
    spill_detection: SpillDetection | None = None
    spill_asset: Asset | None = None
    wind_asset: Asset | None = None
    current_asset: Asset | None = None
    drift_result: DriftResult | None = None

    def _fail(stage: str, error_type: str, message: str) -> InvestigationRunResponse:
        err = StageError(stage=stage, error=error_type, message=message)
        errors.append(err)
        target_store.update_status(
            investigation_id,
            status=InvestigationStatus.FAILED,
            current_stage=stage,
            completed_stages=completed_stages,
            errors=errors,
        )
        return InvestigationRunResponse(
            investigation_id=investigation_id,
            status=InvestigationStatus.FAILED,
            current_stage=stage,
            completed_stages=completed_stages,
            artifacts=artifacts,
            errors=errors,
        )

    # -----------------------------------------------------------------------
    # STAGE B1 — Sentinel-1 Ingestion
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="B1")
    if payload.sentinel1_artifact_path:
        try:
            scene, _val_result, b1_asset = ingest_sentinel1_artifact(
                investigation_id=investigation_id,
                artifact_path=payload.sentinel1_artifact_path,
                registry=target_registry,
            )
            artifacts["B1"] = b1_asset.id
            target_store.attach_assets(investigation_id, [b1_asset.id])
            completed_stages.append("B1")
        except (Sentinel1IngestionError, ValueError, FileNotFoundError) as exc:
            return _fail("B1", type(exc).__name__, str(exc))
        except Exception as exc:
            return _fail("B1", "B1_INTERNAL_ERROR", f"Sentinel-1 ingestion failed: {exc}")
    else:
        # Check if an existing SATELLITE_SCENE asset was registered for this investigation
        for asset in target_registry.list_for_investigation(investigation_id):
            if asset.type == AssetType.SATELLITE_SCENE:
                b1_asset = asset
                # Reconstruct scene from asset metadata if present
                scene_meta = asset.metadata.get("scene") or {}
                if scene_meta:
                    try:
                        scene = SatelliteScene(**scene_meta)
                    except Exception:
                        pass
                artifacts["B1"] = asset.id
                completed_stages.append("B1")
                break

        if not completed_stages:
            return _fail(
                "B1",
                "INSUFFICIENT_INPUT",
                "Sentinel-1 artifact path or existing scene asset is required to execute Stage B1.",
            )

    # -----------------------------------------------------------------------
    # STAGE B2 — SAR Preprocessing
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="B2", completed_stages=completed_stages)
    if payload.sar_asset_id:
        try:
            cand = target_registry.get(payload.sar_asset_id)
            if cand.investigation_id == investigation_id:
                b2_asset = cand
                artifacts["B2"] = b2_asset.id
                completed_stages.append("B2")
        except KeyError:
            return _fail("B2", "ASSET_NOT_FOUND", f"SAR asset '{payload.sar_asset_id}' not found in registry")

    if not b2_asset:
        if b1_asset is None or scene is None:
            return _fail("B2", "MISSING_UPSTREAM_DATA", "Stage B1 scene and asset are required for Stage B2 preprocessing.")
        try:
            b2_asset, _derived_meta = preprocess_sentinel1_scene(
                investigation_id=investigation_id,
                scene=scene,
                asset=b1_asset,
                registry=target_registry,
            )
            artifacts["B2"] = b2_asset.id
            target_store.attach_assets(investigation_id, [b2_asset.id])
            completed_stages.append("B2")
        except (Sentinel1PreprocessingError, ValueError) as exc:
            return _fail("B2", type(exc).__name__, str(exc))
        except Exception as exc:
            return _fail("B2", "B2_INTERNAL_ERROR", f"SAR preprocessing failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE B3 — Spill Detection & Geometry Extraction
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="B3", completed_stages=completed_stages)
    if payload.spill_id:
        for asset in target_registry.list_for_investigation(investigation_id):
            if (
                asset.id == payload.spill_id
                or asset.provenance.product_id == payload.spill_id
                or asset.metadata.get("detection_id") == payload.spill_id
            ):
                spill_asset = asset
                artifacts["B3"] = spill_asset.id
                completed_stages.append("B3")
                break

    if not spill_asset:
        if b2_asset is None or scene is None:
            return _fail("B3", "MISSING_UPSTREAM_DATA", "Preprocessed SAR asset and scene are required for Stage B3 detection.")
        try:
            spill_detection, spill_asset = detect_spills_from_sar_scene(
                investigation_id=investigation_id,
                scene=scene,
                sar_asset=b2_asset,
                polarization=payload.polarization,
                registry=target_registry,
            )
            artifacts["B3"] = spill_asset.id
            target_store.attach_assets(investigation_id, [spill_asset.id])
            completed_stages.append("B3")
        except (SpillDetectionError, ValueError) as exc:
            return _fail("B3", type(exc).__name__, str(exc))
        except Exception as exc:
            return _fail("B3", "B3_INTERNAL_ERROR", f"Spill detection failed: {exc}")

    # Reconstruct or confirm SpillDetection domain object if needed
    if spill_detection is None and spill_asset is not None:
        centroid_dict = spill_asset.metadata.get("centroid")
        detected = spill_asset.metadata.get("detected")
        if detected is None:
            detected = bool(centroid_dict is not None and spill_asset.metadata.get("spill_count", 1) > 0)
        spill_detection = SpillDetection(
            id=spill_asset.provenance.product_id or spill_asset.id,
            investigation_id=investigation_id,
            asset_id=spill_asset.id,
            scene_id=scene.id if scene else "unknown-scene",
            detected=detected,
            confidence=spill_asset.metadata.get("confidence", 1.0 if detected else 0.0),
            geometry=spill_asset.metadata.get("geometry", {}),
            area=spill_asset.metadata.get("total_area_m2") or spill_asset.metadata.get("area"),
            metadata=dict(spill_asset.metadata),
        )

    # -----------------------------------------------------------------------
    # STAGE C1 — Environmental Acquisition (Wind & Current)
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="C1", completed_stages=completed_stages)
    if payload.wind_asset_id:
        try:
            wind_asset = target_registry.get(payload.wind_asset_id)
            artifacts["C1_wind"] = wind_asset.id
        except KeyError:
            return _fail("C1", "ASSET_NOT_FOUND", f"Wind asset '{payload.wind_asset_id}' not found in registry")

    if payload.current_asset_id:
        try:
            current_asset = target_registry.get(payload.current_asset_id)
            artifacts["C1_current"] = current_asset.id
        except KeyError:
            return _fail("C1", "ASSET_NOT_FOUND", f"Current asset '{payload.current_asset_id}' not found in registry")

    if not wind_asset or not current_asset:
        if scene is not None:
            try:
                env_summary = acquire_environmental_data_for_scene(
                    scene=scene,
                    investigation_id=investigation_id,
                    registry=target_registry,
                )
                for prov_id, item in env_summary.items.items():
                    if prov_id == "era5" and item.asset and not wind_asset:
                        wind_asset = item.asset
                        artifacts["C1_wind"] = wind_asset.id
                        target_store.attach_assets(investigation_id, [wind_asset.id])
                    elif prov_id == "cmems" and item.asset and not current_asset:
                        current_asset = item.asset
                        artifacts["C1_current"] = current_asset.id
                        target_store.attach_assets(investigation_id, [current_asset.id])
            except (EnvironmentalAcquisitionError, ValueError) as exc:
                return _fail("C1", type(exc).__name__, str(exc))
            except Exception as exc:
                return _fail("C1", "C1_INTERNAL_ERROR", f"Environmental acquisition failed: {exc}")

    completed_stages.append("C1")

    # -----------------------------------------------------------------------
    # STAGE D1 — Forward Drift Modelling
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="D1", completed_stages=completed_stages)
    if not wind_asset or not current_asset:
        return _fail(
            "D1",
            "MISSING_UPSTREAM_DATA",
            "Validated ERA5 wind and CMEMS current assets are required for Stage D1 drift modelling.",
        )
    if not spill_detection:
        return _fail("D1", "MISSING_UPSTREAM_DATA", "Spill detection is required for Stage D1 drift modelling.")

    try:
        origin_lon, origin_lat = extract_centroid(spill_detection)
        obs_time = scene.acquisition_time if scene else (spill_asset.acquisition_time or datetime.now(timezone.utc))
        drift_hours = payload.drift_hours if payload.drift_hours is not None else 24.0
        step_hours = payload.step_hours if payload.step_hours is not None else 1.0
        leeway_fraction = payload.leeway_fraction if payload.leeway_fraction is not None else 0.035

        drift_result, drift_asset = compute_drift_for_spill(
            investigation_id=investigation_id,
            spill_detection_id=spill_detection.id,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            observation_time=obs_time,
            wind_asset=wind_asset,
            current_asset=current_asset,
            drift_hours=drift_hours,
            step_hours=step_hours,
            leeway_fraction=leeway_fraction,
            registry=target_registry,
        )
        artifacts["D1"] = drift_asset.id
        target_store.attach_assets(investigation_id, [drift_asset.id])
        completed_stages.append("D1")
    except (DriftModellingError, ValueError) as exc:
        return _fail("D1", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("D1", "D1_INTERNAL_ERROR", f"Forward drift modelling failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE D3 — Backward Drift / Source Estimation
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="D3", completed_stages=completed_stages)
    try:
        lookback_hours = payload.lookback_hours if payload.lookback_hours is not None else 12.0
        source_result, source_asset = compute_source_estimate_for_spill(
            investigation_id=investigation_id,
            spill_detection_id=spill_detection.id,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            observation_time=obs_time,
            wind_asset=wind_asset,
            current_asset=current_asset,
            lookback_hours=lookback_hours,
            step_hours=step_hours,
            leeway_fraction=leeway_fraction,
            spill_area_m2=spill_detection.area or spill_detection.metadata.get("total_area_m2"),
            registry=target_registry,
        )
        artifacts["D3"] = source_asset.id
        target_store.attach_assets(investigation_id, [source_asset.id])
        completed_stages.append("D3")
    except (SourceEstimationError, ValueError) as exc:
        return _fail("D3", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("D3", "D3_INTERNAL_ERROR", f"Source estimation failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE E1 — AIS Candidate Generation
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="E1", completed_stages=completed_stages)
    try:
        temporal_window = payload.temporal_window_hours if payload.temporal_window_hours is not None else 3.0
        spatial_buffer = payload.spatial_buffer_km if payload.spatial_buffer_km is not None else 5.0
        candidate_result, candidate_asset = generate_candidate_vessels_for_spill(
            investigation_id=investigation_id,
            spill_id=spill_detection.id,
            source_estimate=source_result,
            ais_asset_id=payload.ais_asset_id,
            temporal_window_hours=temporal_window,
            spatial_buffer_km=spatial_buffer,
            registry=target_registry,
        )
        if candidate_asset:
            artifacts["E1"] = candidate_asset.id
            target_store.attach_assets(investigation_id, [candidate_asset.id])
        completed_stages.append("E1")
    except (CandidateVesselError, ValueError) as exc:
        return _fail("E1", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("E1", "E1_INTERNAL_ERROR", f"Candidate vessel generation failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE E2 — AIS Trajectory Analysis
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="E2", completed_stages=completed_stages)
    try:
        trajectory_result, trajectory_asset = analyze_candidate_trajectories(
            investigation_id=investigation_id,
            spill_id=spill_detection.id,
            source_estimate=source_result,
            candidate_result=candidate_result,
            registry=target_registry,
        )
        artifacts["E2"] = trajectory_asset.id
        target_store.attach_assets(investigation_id, [trajectory_asset.id])
        completed_stages.append("E2")
    except (TrajectoryAnalysisError, ValueError) as exc:
        return _fail("E2", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("E2", "E2_INTERNAL_ERROR", f"Trajectory analysis failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE E3 — Behavioural Intelligence
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="E3", completed_stages=completed_stages)
    try:
        behavioral_result, behavioral_asset = analyze_candidate_behavior(
            investigation_id=investigation_id,
            spill_id=spill_detection.id,
            source_estimate=source_result,
            candidate_result=candidate_result,
            trajectory_analysis_id=trajectory_asset.id,
            registry=target_registry,
        )
        artifacts["E3"] = behavioral_asset.id
        target_store.attach_assets(investigation_id, [behavioral_asset.id])
        completed_stages.append("E3")
    except (BehavioralIntelligenceError, ValueError) as exc:
        return _fail("E3", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("E3", "E3_INTERNAL_ERROR", f"Behavioural intelligence analysis failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE F1 — Evidence Fusion
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="F1", completed_stages=completed_stages)
    try:
        fusion_result, fusion_asset = fuse_evidence(
            investigation_id=investigation_id,
            spill_id=spill_detection.id,
            source_estimate=source_result,
            candidate_result=candidate_result,
            trajectory_result=trajectory_result,
            behavioral_result=behavioral_result,
            drift_result=drift_result,
            registry=target_registry,
        )
        artifacts["F1"] = fusion_asset.id
        target_store.attach_assets(investigation_id, [fusion_asset.id])
        completed_stages.append("F1")
    except (EvidenceFusionError, ValueError) as exc:
        return _fail("F1", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("F1", "F1_INTERNAL_ERROR", f"Evidence fusion failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE F2 — Candidate Ranking
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="F2", completed_stages=completed_stages)
    try:
        ranking_result, ranking_asset = rank_candidates(
            evidence_fusion=fusion_result,
            investigation_id=investigation_id,
            spill_id=spill_detection.id,
            registry=target_registry,
        )
        artifacts["F2"] = ranking_asset.id
        target_store.attach_assets(investigation_id, [ranking_asset.id])
        completed_stages.append("F2")
    except (CandidateRankingError, ValueError) as exc:
        return _fail("F2", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("F2", "F2_INTERNAL_ERROR", f"Candidate ranking failed: {exc}")

    # -----------------------------------------------------------------------
    # STAGE F3 — Explainability & Uncertainty
    # -----------------------------------------------------------------------
    target_store.update_status(investigation_id, current_stage="F3", completed_stages=completed_stages)
    try:
        report, report_asset = generate_explainability_report(
            candidate_ranking=ranking_result,
            investigation_id=investigation_id,
            spill_id=spill_detection.id,
            registry=target_registry,
        )
        artifacts["F3"] = report_asset.id
        target_store.attach_assets(investigation_id, [report_asset.id])
        completed_stages.append("F3")
    except (ExplainabilityError, ValueError) as exc:
        return _fail("F3", type(exc).__name__, str(exc))
    except Exception as exc:
        return _fail("F3", "F3_INTERNAL_ERROR", f"Explainability report generation failed: {exc}")

    # -----------------------------------------------------------------------
    # Complete Workflow Execution
    # -----------------------------------------------------------------------
    final_stage = completed_stages[-1] if completed_stages else "F3"
    target_store.update_status(
        investigation_id,
        status=InvestigationStatus.COMPLETED,
        current_stage=final_stage,
        completed_stages=completed_stages,
        errors=[],
    )

    return InvestigationRunResponse(
        investigation_id=investigation_id,
        status=InvestigationStatus.COMPLETED,
        current_stage=final_stage,
        completed_stages=completed_stages,
        artifacts=artifacts,
        errors=[],
    )
