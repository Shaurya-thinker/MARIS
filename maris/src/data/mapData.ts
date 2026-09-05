import type { MapDemoData } from '../types/maris'

export const mapData: MapDemoData = {
  spillGeoJsonUrl: '/data/ulysse_csl_virginia_oil_slick_2018-10-08_georeferenced.geojson',
  aisJsonUrl: '/data/corsica_ais_reconstructed_2018.json',
  driftJsonUrl: '/data/corsica_drift_reconstruction_2018.json',
  originZone: {
    type: 'Feature', properties: { kind: 'origin-zone' },
    geometry: { type: 'Polygon', coordinates: [[[9.469, 43.244], [9.487, 43.244], [9.487, 43.253], [9.469, 43.253], [9.469, 43.244]]] },
  },
  originPoint: {
    type: 'Feature', properties: { kind: 'origin-point' },
    geometry: { type: 'Point', coordinates: [9.47833, 43.24833] },
  },
}