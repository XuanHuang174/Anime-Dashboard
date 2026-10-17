"""Print a coordinated Render environment patch; never changes configuration.

Every explicit off/on release gets a fresh epoch, so previously signed browse
cursors cannot resurrect on re-enable. Apply all values to all workers together.
"""
import argparse
import json
import secrets


def environment_patch(enabled):
    return {'RECOMMENDATION_ALGORITHM_VERSION': 'content.v1',
            'RECOMMENDATION_BROWSE_ENABLED': 'True' if enabled else 'False',
            'RECOMMENDATION_BROWSE_EPOCH': secrets.token_hex(16)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--enable', action='store_true')
    action.add_argument('--disable', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps({'environment_patch': environment_patch(args.enable),
                      'applied': False, 'requires': 'Operator applies all values in ONE deployment to ALL workers; no database changes.'}, indent=2))


if __name__ == '__main__':
    main()
