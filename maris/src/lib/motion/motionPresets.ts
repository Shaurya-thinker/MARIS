/**
 * MARIS Motion Presets and Principles
 *
 * Design principles:
 * - Apple: subtle transitions, excellent easing, no unnecessary movement, spatial continuity.
 * - SpaceX / Mission-Control: precise, operational, responsive, calm, information-first.
 * - Restrained durations (0.15s - 0.28s) to feel snappy, not sluggish.
 * - Fully respects prefers-reduced-motion.
 */

export const TIMINGS = {
  instant: 0.05,
  fast: 0.15,
  base: 0.22,
  page: 0.25,
  modal: 0.24,
  stagger: 0.04,
} as const

export const EASINGS = {
  out: 'power2.out',
  smooth: 'sine.out',
  expo: 'expo.out',
  inOut: 'power2.inOut',
} as const

export const MOTION_PRESETS = {
  // Route / Page transitions: subtle fade + small vertical settle
  pageEnter: {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0, duration: TIMINGS.page, ease: EASINGS.out },
  },

  // Card staggered entrance
  cardEnter: {
    initial: { opacity: 0, y: 12 },
    animate: { opacity: 1, y: 0, duration: TIMINGS.base, ease: EASINGS.out },
    stagger: TIMINGS.stagger,
  },

  // Modal / Drawer popup
  modalEnter: {
    initial: { opacity: 0, scale: 0.98, y: 6 },
    animate: { opacity: 1, scale: 1, y: 0, duration: TIMINGS.modal, ease: EASINGS.out },
  },

  // Evaluator step switch
  stepPanelEnter: {
    initial: { opacity: 0, y: 6 },
    animate: { opacity: 1, y: 0, duration: TIMINGS.base, ease: EASINGS.out },
  },

  // Micro-interaction values
  buttonPressScale: 0.98,
  cardHoverY: -2,
} as const
