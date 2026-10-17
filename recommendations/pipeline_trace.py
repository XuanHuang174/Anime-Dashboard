"""Offline observers only: never select, score, filter or reorder candidates.

Only stage counts and one target's membership/position leave the observer. Raw
identities stay in memory. No database access, private persistence or HTTP hook.
"""
TRACE_VERSION = 'recommendations.pipeline-trace.v1'
ENGINE_STAGES = ('eligible', 'pool', 'hydrated', 'validated', 'returned')


class PipelineObserver:
    def __init__(self, target=None):
        self.target = target
        self.stages = {}

    def stage(self, name, identities):
        if name not in ENGINE_STAGES or name in self.stages:
            raise ValueError('Invalid or repeated pipeline stage')
        count, position = 0, None
        for count, identity in enumerate(identities, 1):
            if identity == self.target:
                position = count
        self.stages[name] = {'count': count, 'target_position': position}

    def export(self, *, cold_start=False):
        return {'contract_version': TRACE_VERSION, 'cold_start': cold_start,
                'stages': {name: {'observed': name in self.stages or cold_start,
                                 **self.stages.get(name, {'count': 0 if cold_start else None, 'target_position': None})}
                           for name in ENGINE_STAGES}}
