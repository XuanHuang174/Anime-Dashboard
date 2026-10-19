// Journal visual regression + existing state/session checks with local fixtures.
process.env.BROWSE_ACCEPTANCE_PHASE = 'r2'
await import('./test-recommendation-browse.mjs')
