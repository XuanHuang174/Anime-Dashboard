// Real GSAP + StrictMode and native dialog regression using only local fixtures.
process.env.BROWSE_ACCEPTANCE_PHASE = 'r3'
await import('./test-recommendation-browse.mjs')
