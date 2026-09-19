"""Synthetic training dataset generator with group-safe scenario splitting.

Generates multi-scenario datasets by executing real backward drift integration
and extracting candidate features. Enforces strict scenario-level group splitting
(70% Train / 15% Validation / 15% Test) so no scenario appears in multiple splits.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.services.source_estimation import run_backward_drift
from app.services.synthetic_experiment.feature_builder import (
    FEATURE_NAMES,
    extract_vessel_features,
)
from app.services.synthetic_experiment.synthetic_generator import generate_synthetic_scenario


@dataclass
class DatasetPartition:
    """A partition of training or evaluation data."""

    features: np.ndarray          # Shape (N, 10)
    labels: np.ndarray            # Shape (N,) binary 0 or 1
    scenario_ids: list[str]       # Scenario ID for each sample
    vessel_ids: list[str]         # Vessel identifier for each sample
    sample_metadata: list[dict[str, Any]]


@dataclass
class SyntheticDataset:
    """Full group-split synthetic dataset."""

    train: DatasetPartition
    val: DatasetPartition
    test: DatasetPartition
    feature_names: list[str]
    total_scenarios: int
    total_samples: int
    scenario_manifest: list[dict[str, Any]]


def build_synthetic_dataset(
    num_scenarios: int = 50,
    base_seed: int = 42,
    candidates_per_scenario: int = 4,
    backtrack_hours: float = 6.0,
    step_hours: float = 0.5,
    scenario_seeds: list[int] | None = None,
) -> SyntheticDataset:
    """Generate a group-split dataset of synthetic scenarios with extracted features."""
    rng = random.Random(base_seed)

    scenario_ids = []
    all_samples = []
    manifest = []

    seeds = scenario_seeds if scenario_seeds is not None else [
        base_seed + (i * 1000) + rng.randint(1, 999) for i in range(num_scenarios)
    ]
    num_scenarios = len(seeds)

    for scenario_seed in seeds:
        scenario = generate_synthetic_scenario(
            seed=scenario_seed,
            candidate_count=candidates_per_scenario,
            backtrack_hours=backtrack_hours,
            step_hours=step_hours,
        )
        scenario_ids.append(scenario.scenario_id)

        # Execute backward drift using actual physics integration
        backward_steps = run_backward_drift(
            origin_lon=scenario.origin_lon,
            origin_lat=scenario.origin_lat,
            observation_time=scenario.observation_time,
            wind_ds=scenario.wind_ds,
            curr_ds=scenario.curr_ds,
            lookback_hours=scenario.backtrack_hours,
            step_hours=scenario.step_hours,
            spill_area_m2=scenario.spill_area_m2,
        )

        final_step = backward_steps[-1]
        source_lon = final_step.lon
        source_lat = final_step.lat
        source_radius_m = final_step.uncertainty_radius_m

        manifest_entry = scenario.to_summary_dict()
        manifest_entry["reconstructed_source_lon"] = round(source_lon, 5)
        manifest_entry["reconstructed_source_lat"] = round(source_lat, 5)
        manifest_entry["source_uncertainty_radius_m"] = round(source_radius_m, 1)
        manifest.append(manifest_entry)

        # Extract features for all candidate vessels
        for v in scenario.vessels:
            feat_dict = extract_vessel_features(
                vessel_data=v,
                source_lon=source_lon,
                source_lat=source_lat,
                source_radius_m=source_radius_m,
                observation_time=scenario.observation_time,
                backtrack_hours=scenario.backtrack_hours,
                backward_steps=backward_steps,
            )

            is_gt = bool(v.get("is_ground_truth", False))
            v_id = str(v.get("id") or v.get("mmsi") or v.get("vessel_name"))

            sample = {
                "scenario_id": scenario.scenario_id,
                "vessel_id": v_id,
                "label": 1 if is_gt else 0,
                "features": [feat_dict[name] for name in FEATURE_NAMES],
                "metadata": {
                    "vessel_name": v.get("vessel_name"),
                    "mmsi": v.get("mmsi"),
                    "vessel_type": v.get("vessel_type"),
                    "is_ground_truth": is_gt,
                    "features": feat_dict,
                },
            }
            all_samples.append(sample)

    # Group-safe partition by scenario_id: 70% Train / 15% Val / 15% Test
    rng.shuffle(scenario_ids)

    n_scenarios = len(scenario_ids)
    n_train = max(1, int(round(n_scenarios * 0.70)))
    n_val = max(1, int(round(n_scenarios * 0.15)))
    if n_train + n_val >= n_scenarios:
        n_val = max(1, (n_scenarios - n_train) // 2)

    train_ids = set(scenario_ids[:n_train])
    val_ids = set(scenario_ids[n_train : n_train + n_val])
    test_ids = set(scenario_ids[n_train + n_val :])
    if not test_ids:
        test_ids = set(scenario_ids[n_train + n_val - 1 :])

    def _create_partition(target_ids: set[str]) -> DatasetPartition:
        matching = [s for s in all_samples if s["scenario_id"] in target_ids]
        if not matching:
            feats = np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)
            lbls = np.zeros((0,), dtype=np.int64)
            return DatasetPartition(feats, lbls, [], [], [])

        feats = np.array([s["features"] for s in matching], dtype=np.float32)
        lbls = np.array([s["label"] for s in matching], dtype=np.int64)
        scen_ids = [s["scenario_id"] for s in matching]
        v_ids = [s["vessel_id"] for s in matching]
        metas = [s["metadata"] for s in matching]

        return DatasetPartition(
            features=feats,
            labels=lbls,
            scenario_ids=scen_ids,
            vessel_ids=v_ids,
            sample_metadata=metas,
        )

    return SyntheticDataset(
        train=_create_partition(train_ids),
        val=_create_partition(val_ids),
        test=_create_partition(test_ids),
        feature_names=FEATURE_NAMES,
        total_scenarios=n_scenarios,
        total_samples=len(all_samples),
        scenario_manifest=manifest,
    )
