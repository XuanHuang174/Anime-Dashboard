// Reuse the existing isolated native-dialog harness; no production account/data.
process.env.BROWSE_ACCEPTANCE_PHASE = 'r1'
await import('./test-recommendation-browse.mjs')
