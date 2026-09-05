import type { AttributionFactor, CandidateAttribution } from '../types/maris'

const collisionOrigin = { lat: 43.24833, lon: 9.47833 }
const collisionTimestamp = Date.parse('2018-10-07T06:02:46Z')

function haversineKm(first: { lat: number; lon: number }, second: { lat: number; lon: number }) {
  const earthRadiusKm = 6371
  const latitudeDelta = (second.lat - first.lat) * Math.PI / 180
  const longitudeDelta = (second.lon - first.lon) * Math.PI / 180
  const latitudeOne = first.lat * Math.PI / 180
  const latitudeTwo = second.lat * Math.PI / 180
  const value = Math.sin(latitudeDelta / 2) ** 2 + Math.cos(latitudeOne) * Math.cos(latitudeTwo) * Math.sin(longitudeDelta / 2) ** 2
  return earthRadiusKm * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value))
}

function spatialFactor(position: { lat: number; lon: number }): AttributionFactor {
  return { label: 'Spatial Proximity', weight: 35, status: 'calculated', detail: `${haversineKm(collisionOrigin, position).toFixed(2)} km from collision reference` }
}

function temporalFactor(timestamp: string): AttributionFactor {
  const differenceMinutes = Math.abs(Date.parse(timestamp) - collisionTimestamp) / 60000
  return { label: 'Temporal Match', weight: 25, status: 'calculated', detail: differenceMinutes === 0 ? 'Temporal match at 06:02:46 UTC' : `${differenceMinutes.toFixed(1)} minutes from collision reference` }
}

const insufficientTrajectory: AttributionFactor = { label: 'Trajectory Consistency', weight: 25, status: 'insufficient', detail: 'Insufficient trajectory data' }

export const candidateAttributions: CandidateAttribution[] = [
  {
    id: 'ULYSSE', mmsi: '672248000', imo: '9142459', role: 'Striking vessel - underway', priority: 'Highest-ranked candidate', color: 'accent', rank: 1, overallLabel: 'Highest-Ranked Candidate',
    factors: [spatialFactor({ lat: 43.24833, lon: 9.47833 }), temporalFactor('2018-10-07T06:02:00Z'), { label: 'Trajectory Consistency', weight: 25, status: 'documented', detail: 'Documented approach course: 161 deg; no intermediate points' }, { label: 'Behavioral Anomaly', weight: 15, status: 'documented', detail: 'Documented approach speed: 19 kn' }],
  },
  {
    id: 'CSL VIRGINIA', mmsi: '212416000', imo: '9289568', role: 'Struck vessel - at anchor', priority: 'Candidate for review', color: 'warning', rank: 2, overallLabel: 'Candidate',
    factors: [spatialFactor({ lat: 43.24833, lon: 9.47833 }), temporalFactor('2018-10-07T06:02:00Z'), insufficientTrajectory, { label: 'Behavioral Anomaly', weight: 15, status: 'documented', detail: 'Anchored state; SOG 0 kn; 1000 m swing circle' }],
  },
]