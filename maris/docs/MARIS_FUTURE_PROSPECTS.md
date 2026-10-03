# MARIS — Future Scientific & Technical Prospects

## 1. Project Roadmap & Current Checkpoint

The Maritime Attribution and Risk Intelligence System (MARIS) has completed Phase 6. The authoritative project roadmap stands as follows:

| Phase | Description | Status |
| :--- | :--- | :--- |
| **Phase 1** | Real SAR pixel ingestion (Copernicus Sentinel-1 GRD calibrated backscatter) | ✅ **COMPLETE** |
| **Phase 2** | SAR → existing drift engine (Centroid geocoding & coastal integration) | ✅ **COMPLETE** |
| **Phase 3** | Dynamic AIS correlation (`ais_vessels.db` corridor & spatiotemporal search) | ✅ **COMPLETE** |
| **Phase 4** | Attribution + explainability (Multi-criteria evidence fusion & scoring) | ✅ **COMPLETE** |
| **Phase 5** | Uncertainty / Monte Carlo ensemble simulation | ✅ **COMPLETE** |
| **Phase 6** | SAR ↔ AIS dual-sensor vessel matching & surveillance intelligence | ✅ **COMPLETE** |
| *6.1* | *SAR Bright Target Detection (Sentinel-1 CFAR scattering detector)* | ✅ *COMPLETE* |
| *6.2* | *SAR ↔ AIS Matching (3,000 m spatiotemporal gating & 6 classifications)* | ✅ *COMPLETE* |
| *6.3* | *Surveillance Backend Integration (Experiment runner & canonical taxonomy)* | ✅ *COMPLETE* |
| *6.4* | *Surveillance UI & Scientific Report (Attribution map & Section 8.5)* | ✅ *COMPLETE* |
| **Phase 7** | Advanced marine transport physics & oil weathering dynamics | 💤 **DEFERRED** |
| **Phase 8** | Production architecture & enterprise scaling | ⬜ **FUTURE** |

### Checkpoint Invariant
The completed **Phases 1–6 operational baseline is intentionally and strictly preserved**. No experimental physics, feature flags, or uncalibrated weathering formulations have been introduced into the active production pipeline.

---

## 2. Deferred Phase 7 — Advanced Marine Physics & Oil Weathering

Phase 7 was comprehensively audited to evaluate candidate scientific advancements for potential future integration. It is **explicitly deferred** and represents a **future scientific advancement track**, not implemented functionality.

### Candidate Marine Transport Physics (Future Track)
- **Surface Stokes Drift**:
  - *Concept*: Integration of wave-orbital mass transport vectors ($\vec{u}_{\text{Stokes}}$) from Copernicus Marine wave reanalysis (`GLOBAL_MULTIYEAR_WAV_001_032`) or ECMWF ERA5 wave fields (`ustokes`, `vstokes`).
  - *Status*: Candidate future enhancement. Not implemented in active drift engine.
- **Direct Windage / Leeway Recalibration**:
  - *Concept*: Recalibrating the direct wind leeway coefficient from the empirical baseline ($\alpha = 0.035$) down to a decoupled direct windage parameter ($\alpha_{\text{direct}}$) if explicit wave Stokes drift is introduced.
  - *Status*: Potential future capability requiring dedicated empirical calibration.
- **Conditional Tidal Forcing**:
  - *Concept*: Coupling barotropic astronomical tidal currents ($\vec{u}_{\text{tide}}$) from harmonic constituent atlases (e.g., TPXO9-atlas, FES2014) in macrotidal coastal straits (e.g., English Channel, Singapore Strait).
  - *Status*: Deferred. Inapplicable to deep microtidal open-ocean regimes such as the Mediterranean Cap Corse validation site ($|\vec{u}_{\text{tide}}| < 0.03\text{ m/s}$).

### Candidate Oil Weathering Dynamics (Future Track)
- **Evaporation Modeling**:
  - *Concept*: Analytical evaporative exposure formulation (e.g., Fingas / Mackay models) to project volatile hydrocarbon mass loss as a function of wind speed, sea temperature, and distillation fractions.
  - *Status*: Candidate future enhancement.
- **Emulsification (Mousse Formation)**:
  - *Concept*: Water-in-oil emulsification kinetics governed by wave turbulence and asphaltene content, driving volume expansion and non-linear dynamic viscosity growth ($\mu(t)$).
  - *Status*: Potential future capability.
- **Natural Dispersion**:
  - *Concept*: Breaking-wave energy entrainment (Delvigne & Sweeney droplet formulation) modeling subsurface droplet flux under rough sea states.
  - *Status*: Candidate future enhancement.
- **Oil-State & Persistence Modeling**:
  - *Concept*: Decoupled 1D mass balance engine tracking slick volume, remaining surface fraction, and physical persistence over time.
  - *Status*: Deferred future track.

### Observability Constraints (Future Track)
- **Wave-State-Aware Slick Persistence**:
  - *Concept*: Correlating significant wave height ($H_s$) and breaking fraction with capillary wave damping thresholds to establish physical lookback limits on satellite SAR slick detectability.
  - *Status*: Potential future enhancement.

### Explicitly Out of Scope
The following areas were evaluated during the Phase 7 audit and determined to be **strictly out of scope** due to disproportionate complexity or lack of satellite-observational relevance:
- **Chemical Dissolution**: Dissolution accounts for $<1\%$ of total slick mass; it is an ecotoxicological metric with negligible impact on horizontal trajectory or satellite attribution.
- **Full 3D Subsurface Particle Tracking**: MARIS is an oil-slick satellite surveillance and maritime attribution platform tracking surface slicks, not a 3D subsurface blowout plume simulator.
- **Unvalidated Oil-Specific Deterministic Assumptions**: Slicks detected anonymously from orbit have unknown cargo compositions prior to vessel attribution. Forcing deterministic distillation curves without standardized uncertainty bands introduces pseudo-precision and violates scientific integrity.

---

## 3. Why Phase 7 is Deferred

Phase 7 is deferred not because it lacks scientific merit, but because **modifications to the physical transport engine propagate through the entire attribution pipeline**:

$$\text{Drift Advection} \longrightarrow \text{Source Estimation} \longrightarrow \text{AIS Candidate Discovery} \longrightarrow \text{Evidence Fusion} \longrightarrow \text{Attribution Ranking}$$

### The Critical Leeway–Stokes Coupling Question
The canonical operational leeway coefficient ($\alpha = 0.035$, or $3.5\%$ of $10\text{ m}$ wind speed) used in Stage D1/D3 is an authoritative empirical standard derived from field buoy and slick experiments (ITOPF, NOAA GNOME, Breivik et al. 2011). Crucially:
$$\alpha = 0.035 \text{ already empirically incorporates wave-induced Stokes drift and surface windage combined.}$$

If explicit Stokes drift ($\vec{u}_{\text{Stokes}}$) is added into the advection equation without recalibration:
$$\vec{v}_{\text{drift}} = \vec{v}_{\text{current}} + \vec{u}_{\text{Stokes}} + 0.035 \cdot \vec{v}_{\text{wind}} \quad \Longrightarrow \quad \mathbf{DOUBLE\text{-}COUNTING\ ERROR}$$

Wave transport would be counted twice, artificially exaggerating downwind displacement and corrupting the reconstructed source zone. Any replacement direct windage parameter must be empirically calibrated and scientifically validated against authoritative field datasets before being introduced.

**Conclusion**: The existing, validated Phase 1–6 physics ($\vec{v}_{\text{drift}} = \vec{v}_{\text{current}} + 0.035 \cdot \vec{v}_{\text{wind}}$) remains the protected operational baseline.

---

## 4. Preserved Physics Baseline

The protected operational baseline of MARIS remains strictly as follows:
- **Forward Drift (Stage D1 & Step 11)**: Deterministic Leeway-Euler forward integration with $\alpha = 0.035$, step size $\Delta t = 1.0\text{ h}$, and coastal boundary termination via Natural Earth 10m maritime masking.
- **Backward Drift & Source Reconstruction (Stage D3)**: Time-reversed Leeway-Euler stepping under ERA5 hourly 10 m wind and CMEMS daily mean surface current, with analytical uncertainty expansion $R(t) = R_0 + c \cdot t$.
- **Monte Carlo Uncertainty Ensemble (Phase 5)**: Non-destructive parameter perturbations (origin, leeway factor, wind speed/direction, ocean current) evaluating trajectory sensitivity.
- **Zero Fabrication**: Discrepancies between physical radar returns and AIS transponder broadcasts are reported neutrally as observational anomalies without speculative accusations.

---

## 5. Phase 8 — Future Production Architecture

Phase 8 is designated as a **future technical and infrastructure track** to support large-scale operational deployments.

### Potential Future Architecture Areas
- **Database Engine Migration**: Transitioning persistence from SQLite (`real_experiments.db`, `ais_vessels.db`) to enterprise **PostgreSQL**.
- **Spatial Engine Integration**: Utilizing **PostGIS** for database-native R-Tree spatial indexing, corridor intersection queries, and geodesic distance calculations.
- **Time-Series Optimization**: Evaluating **TimescaleDB** for high-throughput temporal AIS telemetry partitioning.
- **Asynchronous Task Queues**: Introducing distributed job queues (e.g., Celery, Redis, or Temporal) to decouple long-running satellite processing and Monte Carlo workflows.
- **Large-Scale AIS Partitioning**: Implementing spatial grid partitioning and quad-key indexing across global AIS archives exceeding tens of millions of records.
- **Geospatial Vector Tiles**: Dynamic Mapbox Vector Tile (MVT) generation for smooth frontend rendering of large vessel traffic densities.
- **Production Containerization & Orchestration**: Kubernetes deployment manifests, caching layers (Redis), and horizontal worker auto-scaling.

### Phase 8 Constraints
These are future architectural directions. **None of these components are implemented now**. SQLite, pure Python in-memory interpolation, and current FastAPI/Vite architectures remain the operational baseline.
