"""Print only: coordinated canary/full/rollback environment for extension 11."""
import argparse
import json
import secrets


def environment_patch(mode, users=()):
    if mode not in ('canary', 'all', 'off'):
        raise ValueError('Unknown rollout mode')
    names = sorted(set(users))
    if any(not isinstance(name, str) or not name.strip() or name != name.strip() or ',' in name for name in names):
        raise ValueError('Canary usernames must be exact nonempty names without commas')
    if bool(names) != (mode == 'canary'):
        raise ValueError('Only canary mode requires usernames')
    # Never touch personal recommendations, V2, content review or NSFW controls.
    return {'RECOMMENDATION_EXPLORATION_ENABLED': 'False' if mode == 'off' else 'True',
            'RECOMMENDATION_EXPLORATION_CANARY_USERS': ','.join(names),
            'RECOMMENDATION_EXPLORATION_EPOCH': secrets.token_hex(16)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('canary', 'all', 'off'), required=True)
    parser.add_argument('--user', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        patch = environment_patch(args.mode, args.user)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({'environment_patch': patch, 'applied': False,
        'requires': 'Apply all three values together to all workers in one deployment; no data changes.'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
