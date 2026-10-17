// Same production-page/native-dialog harness, synthetic V2 contracts only.
process.env.BROWSE_ACCEPTANCE_PHASE = 'v2d2'
await import('./test-recommendation-browse.mjs')
