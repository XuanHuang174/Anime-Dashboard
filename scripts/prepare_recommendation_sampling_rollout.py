"""Print the P3 operator patch only; no credentials, deployment or DB access."""
import argparse
import json


def environment_patch(enabled):
    return {'RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED': 'True' if enabled else 'False',
            'RECOMMENDATION_ALGORITHM_VERSION': 'content.v1',
            'RECOMMENDATION_V2_BROWSE_ENABLED': 'False'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--enable', action='store_true')
    action.add_argument('--disable', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps({'environment_patch': environment_patch(args.enable), 'applied': False,
        'requires': 'Deploy compatible P1/P2 backend and frontend first; apply together to ALL Render workers. '
                    'Keep stored settings and migrations; never clear journals or feedback.'}, indent=2))


if __name__ == '__main__':
    main()
