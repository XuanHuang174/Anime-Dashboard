"""Read-only presentation adapter, never an authorization or release provider.

Keep the existing server checks authoritative until a separate content-safety
integration is ready. A browser checkbox must never create an account grant.
"""

CONTRACT_VERSION = 'recommendations.scope-control.v1'


def scope_control_contract(*, available, serving_enabled, revision):
    return {
        'contract_version': CONTRACT_VERSION,
        'selection_mode': 'session_draft',
        'default_scope': 'safe',
        'available': available is True and serving_enabled is True,
        'reason': ('available' if available is True and serving_enabled is True
                   else 'service_closed' if serving_enabled is not True else 'access_unavailable'),
        'revision': revision,
    }
