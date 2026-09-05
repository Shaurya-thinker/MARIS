import type { DriftReconstructionData } from '../types/maris'

export const driftSummary: Pick<DriftReconstructionData, 'status' | 'forcing' | 'result'> = {
  status: 'prototype_environmental_reconstruction',
  forcing: {
    wind: 'ERA5 10 m Wind + CMEMS Surface Currents',
    current: 'ERA5 10 m Wind + CMEMS Surface Currents',
    windageFraction: 0.03,
    integrationStepMinutes: 15,
    method: 'Prepared environmental reconstruction',
  },
  result: {
    estimatedPositionAtFirstSAR: { lat: 43.28464331763805, lon: 9.37980739084363 },
    displacementKm: 8.953188607520522,
    bearingFromOrigin_deg: 296.84020606297224,
  },
}
