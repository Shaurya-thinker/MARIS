# MARIS

Marine Intelligence & Spill Attribution System for SIH26143.

MARIS is a React/TypeScript/MapLibre prototype for investigating the historical Ulysse-CSL Virginia oil-spill incident near Cap Corse, Corsica, on 7 October 2018. The current application is a single-case demonstration platform, not an operational spill-detection or legal-attribution system.

## State Snapshot

```text
Phase 1: COMPLETE
Phase 2: COMPLETE
Phase 3 Prototype Polish: COMPLETE
Real AI Intelligence: NOT STARTED
```

The prototype displays a real Sentinel-1 historical image, reconstructed spill geometry, prepared environmental drift reconstruction, reconstructed AIS event evidence, transparent evidence factors, candidate prioritization, and an optional presentation playback flow.

## Technology

- React 19
- TypeScript with strict mode
- Vite
- MapLibre GL JS
- Lucide React
- Plain CSS with tokens in `src/styles/global.css`
- No backend, database, authentication, live APIs, or ML framework

## Actual Architecture

```text
index.html
	-> src/main.tsx
	-> App.tsx
	-> MainLayout
	-> InvestigationWorkspace
			 -> Header
			 -> IncidentPanel
			 -> MapView
			 -> AnalysisPanel
			 -> InvestigationPipeline
			 -> prototype playback layer
```

`App.tsx` composes the application. `MainLayout` provides the shell. `InvestigationWorkspace` owns layer visibility, selected candidate, the placeholder Analyze Scene state, and temporary playback state. `AnalysisPanel` currently contains spill, drift, origin, vessel, candidate, and attribution-factor responsibilities; there is no standalone `CandidateRanking` component.

## Permanent Application Components

### `src/components/layout/Header.tsx`

Displays MARIS branding, the active incident ID, system status, and an inert notification button.

### `src/components/layout/InvestigationWorkspace.tsx`

Coordinates permanent React state and passes it to the incident panel, MapLibre view, analysis panel, and pipeline. It also temporarily gates prepared map layers during prototype playback.

### `src/components/incident/IncidentPanel.tsx`

Displays incident, satellite, and spill metadata. It renders the local Sentinel-1 image and provides the Analyze Scene, spill, drift, and vessel controls.

### `src/components/map/MapView.tsx`

Creates one MapLibre map instance, loads prepared assets, adds sources and layers, handles candidate marker selection, updates visibility/highlighting, observes resize changes, and cleans up on unmount.

### `src/components/analysis/AnalysisPanel.tsx`

Displays spill analysis, prototype AI presentation, environmental drift summary, origin information, vessel information, candidate cards, attribution factors, and investigation-priority disclaimer.

### `src/components/pipeline/InvestigationPipeline.tsx`

Renders completed, current, and pending investigation stages. Stages are display-only and are not clickable.

## Data Inventory

| File | Purpose | Classification | Current use |
|---|---|---|---|
| `src/data/demoData.ts` | Incident display data and default pipeline | Prepared/UI data | Imported by `App` and workspace |
| `src/data/mapData.ts` | Asset URLs and origin geometry | Prepared configuration/geometry | Imported by `MapView` |
| `src/data/driftData.ts` | Drift summary values | Prepared display data | Imported by `AnalysisPanel` |
| `src/data/attributionData.ts` | Distance, time evidence, and ranks | Derived plus prepared evidence | Imported by `demoData` |
| `public/satellite/corsica_2018_s1.jpg` | Sentinel-1 historical image | Real historical source image | Rendered by `IncidentPanel` |
| `public/data/ulysse_csl_virginia_oil_slick_2018-10-08_georeferenced.geojson` | Slick boundary | Reconstructed geometry | Fetched by `MapView` |
| `public/data/corsica_drift_reconstruction_2018.json` | Drift trajectory | Prepared reconstruction | Fetched by `MapView` |
| `public/data/corsica_ais_reconstructed_2018.json` | Vessel event anchors | Reconstructed historical data | Fetched by `MapView` |
| `public/data/corsica_wind_era5_2018.json` | Hourly 10m wind | Real ERA5 reanalysis | Present, not loaded by frontend |
| `public/data/corsica_ocean_currents_cmems_2018.json` | Daily surface currents | CMEMS reanalysis | Present, not loaded by frontend |

Asset sizes observed during audit: 72 ERA5 records, 3 CMEMS records, 95 drift points, 2 AIS vessels with one event anchor each, and 1 spill GeoJSON feature.

## Satellite and Spill Behavior

The Sentinel-1 JPEG is displayed as an image. It is not processed by the application. There is no computer-vision model, segmentation model, generated mask, raster processing, or runtime confidence calculation.

The spill GeoJSON is fetched from `/data/ulysse_csl_virginia_oil_slick_2018-10-08_georeferenced.geojson` and registered directly as the `spill-polygon` source. `spill-fill` and `spill-line` render that supplied geometry. The separate `spill-centroid` point is the documented collision reference at approximately `43.24833 N, 9.47833 E`; it is not calculated from polygon geometry.

The slick is manually digitized/reconstructed from the published SAR visualization and is not an official ground-truth mask. “Spill detection” currently means displaying a prepared historical spill result.

## Environmental Drift Behavior

The wind and current files document the environmental inputs, but the browser does not load or integrate them. `MapView` fetches the prepared drift JSON and maps its existing 95 trajectory points directly to a GeoJSON LineString.

The prepared reconstruction uses nearest-grid ERA5 10m wind and CMEMS surface currents, 3% windage, and 15-minute integration steps. It starts at the documented collision coordinate and ends at approximately `43.2846433 N, 9.3798074 E`, with approximately `8.953 km` displacement.

No interpolation, smoothing, or runtime drift calculation occurs in MARIS. The result is labeled “Prototype Environmental Reconstruction” and is not an operational forecast.

## AIS Behavior

The application fetches `/data/corsica_ais_reconstructed_2018.json`. The dataset explicitly states that raw machine-readable AIS is unavailable.

ULYSSE (`MMSI 672248000`, `IMO 9142459`) has one collision-site event anchor, approximately 19 kn, and documented approach course 161 degrees. CSL VIRGINIA (`MMSI 212416000`, `IMO 9289568`) has one collision-site event anchor, SOG 0, anchored status, and a 1000m swing circle.

MARIS does not retrieve historical AIS and does not fabricate intermediate coordinates. The map displays two reconstructed anchors at the collision site, a short visualization vector for ULYSSE's documented approach direction, and a derived swing-circle outline for CSL VIRGINIA. The `vessel-tracks` source is an empty FeatureCollection; no continuous vessel track is displayed.

## MapLibre Layers

All layers are created in `src/components/map/MapView.tsx`:

| Layer | Geometry | Purpose | Toggle |
|---|---|---|---|
| `origin-zone-fill`, `origin-zone-line` | Polygon | Prepared origin context | Drift state |
| `origin-point` | Point | Collision/origin reference | Drift state |
| `spill-fill`, `spill-line` | Supplied Polygon | Reconstructed slick | Spill |
| `spill-centroid` | Point | Collision reference | Spill |
| `drift-backward` | Prepared LineString | Environmental reconstruction | Drift |
| `drift-forward` | Empty filtered branch | Structural future path | Drift |
| `vessel-tracks` | Empty FeatureCollection | Track layer contract | Vessels |
| `vessel-markers` | Two Points | Reconstructed vessel anchors | Vessels |
| vessel label layers | Symbols | ULYSSE/CSL VIRGINIA labels | Vessels |
| `approach-vector` | Derived LineString | ULYSSE 161 degree direction | Vessels |
| `anchor-swing-circle` | Derived Polygon outline | CSL VIRGINIA 1000m swing area | Vessels |

Candidate selection updates marker color, radius, stroke, and vessel-track paint properties. Map marker clicks select ULYSSE or CSL VIRGINIA and update the analysis panel. Spill, drift, and vessel controls update existing MapLibre layers without recreating sources. Reset View changes only the camera using fixed Corsica bounds.

## Attribution Model

`src/data/attributionData.ts` defines these conceptual weights:

- Spatial Proximity: 35%
- Temporal Match: 25%
- Trajectory Consistency: 25%
- Behavioral Anomaly: 15%

Spatial proximity uses a Haversine calculation, but both candidate positions are prepared collision-site coordinates, producing `0.00 km`. Temporal match compares the prepared `06:02:00 UTC` vessel timestamp with the prepared `06:02:46 UTC` collision timestamp, producing approximately 0.8 minutes.

Trajectory consistency is not calculated as a trajectory metric. ULYSSE's 161 degree course is documented evidence; CSL VIRGINIA reports “Insufficient trajectory data.” Behavioral evidence is documented behavior, not anomaly detection.

The candidate order is prepared/manual: ULYSSE is rank 1 and CSL VIRGINIA is rank 2. The application shows “Partial-data attribution” and “Highest-Ranked Candidate.” It cannot establish guilt, responsibility, or definitive causation.

## Prototype-Only Layer

The following files are temporary presentation code:

- `src/prototype/prototypeFlow.ts` defines six playback stages and temporary pipeline states.
- `src/prototype/PrototypeAiAnalysis.tsx` displays “Prototype Inference,” a prepared spill-mask label, and hardcoded 94% “Prototype confidence.” It runs no model and calculates no confidence.
- `src/prototype/InvestigationPlayback.tsx` provides start, pause, resume, restart, and exit controls.

Playback stages are Sentinel-1 SAR, AI Spill Detection, Environmental Drift, Origin Estimation, AIS Correlation, and Candidate Ranking. At each stage it changes presentation state and progressively gates existing spill, drift, and vessel layers. It does not run AI, drift, origin, AIS, or ranking computation.

Prototype-only CSS includes `.playback-launch`, `.playback-bar`, `.playback-actions`, `.prototype-ai`, `.confidence-line`, `.confidence-bar`, and `prototype-reveal`.

## User Interaction Summary

| Action | Behavior |
|---|---|
| Start playback | Activates timed staged reveal |
| Pause/resume | Stops or restarts playback interval |
| Restart | Returns to stage zero and starts again |
| Exit playback | Restores normal layer view and pipeline |
| Analyze Scene | Changes button to “Scene queued”; no analysis runs |
| Toggle layers | Updates MapLibre layer visibility |
| Select candidate | Highlights card and selected marker |
| Click vessel marker | Selects the corresponding documented vessel |
| Reset View | Fits fixed Corsica bounds only |
| Zoom/compass | Uses MapLibre navigation control |
| Click pipeline stage | No action |
| Header bell | No action |

## Classification

| Element | Classification |
|---|---|
| Sentinel-1 image | Real historical source data |
| Spill polygon | Reconstructed/manual digitization |
| ERA5 wind | Real reanalysis source, currently unused by frontend |
| CMEMS currents | Real reanalysis source, currently unused by frontend |
| Drift trajectory | Prepared environmental reconstruction |
| Origin point | Documented collision coordinate |
| Origin zone | Prepared hardcoded visual geometry |
| ULYSSE/CSL VIRGINIA identity | Documented historical vessel data |
| AIS positions | Reconstructed event anchors |
| AIS trajectories | Not available; no continuous tracks |
| Haversine and timestamp differences | Real frontend calculations over prepared inputs |
| Candidate ranking | Prepared/manual ordering with transparent evidence |
| AI segmentation | Prototype-only UI representation |
| AI confidence | Prototype-only hardcoded display value |
| Playback | Prototype-only UI orchestration |
| MapLibre rendering | Real runtime map rendering |

## Single-Case Dependencies

The current implementation is intentionally tied to one case through Corsica coordinates, fixed map bounds, incident dates, asset filenames, ULYSSE and CSL VIRGINIA identities, MMSI/IMO values, 161 degree approach, 1000m swing circle, prepared origin geometry, fixed pipeline stages, and candidate set.

These dependencies are correct for the historical demonstration but must become dynamic for a multi-incident system. Future work should generalize incident schemas, asset registries, map bounds, vessel sets, candidate factors, evidence, confidence, pipeline state, and provenance.

## Phase 2 Rollback Plan

To remove the temporary presentation layer and return to the Phase 2 architecture:

1. Delete `src/prototype/prototypeFlow.ts`.
2. Delete `src/prototype/PrototypeAiAnalysis.tsx`.
3. Delete `src/prototype/InvestigationPlayback.tsx`.
4. Remove prototype imports from `InvestigationWorkspace.tsx` and `AnalysisPanel.tsx`.
5. Remove playback state, timer effect, gated layer state, and prototype pipeline selection from `InvestigationWorkspace.tsx`.
6. Pass `layers`, `pipelineStages`, and the permanent analysis props directly again.
7. Remove `prototypeActive` and the `<PrototypeAiAnalysis />` usage.
8. Remove prototype-only CSS selectors and `prototype-reveal`.
9. Run strict TypeScript, Oxlint, build, and browser validation.

Permanent spill, drift, AIS, attribution, and MapLibre data files should remain.

## What MARIS Can Claim Today

MARIS can display and contextualize one historical Sentinel-1 oil-spill case using real source imagery, reconstructed spill geometry, prepared environmental drift, reconstructed AIS event evidence, limited calculated evidence, and transparent investigation prioritization.

MARIS cannot claim to detect oil spills from imagery, run AI segmentation, retrieve raw AIS, calculate operational drift, calculate a complete attribution score, provide validated probability, establish responsibility, or support multiple cases dynamically.

## Future Real AI Intelligence

The future system must replace prepared spill geometry with Sentinel-1 preprocessing, segmentation, calibrated confidence, and geometry extraction; replace prepared drift with operational environmental forcing, uncertainty propagation, and dynamic origin estimation; replace AIS anchors with licensed/raw historical trajectories; and replace prepared candidate ordering with validated, explainable, calibrated attribution.

Missing high-priority capabilities include SAR model training/evaluation/inference, dynamic spill characterization, continuous AIS handling, robust spatio-temporal correlation, dynamic origin estimation, calibrated confidence, provenance, and multi-case generalization. Advanced capabilities may include multi-sensor fusion, near-real-time feeds, human review, and forensic audit trails.

## Final Assessment

The prototype is technically coherent for a controlled single-case hackathon demonstration. Its permanent investigation architecture and temporary presentation layer are identifiable, and its reconstructed/prepared limitations are documented. It is ready for a demo/video when the presentation clearly distinguishes real source data, reconstructed evidence, prepared outputs, calculated evidence, and UI simulation.

Real AI intelligence has not started.
