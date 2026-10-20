"""Explicit owner-authorized aggregate readiness, never broad private export."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError

from recommendations.engine import user_context
from recommendations.quality_readonly import readonly_snapshot


class Command(BaseCommand):
    help = '独立显式授权的全站只读样本可用性汇总；不排名、不导出账号或手账明细、不替换冻结集。'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--max-accounts', type=int, default=200)
        parser.add_argument('--output-dir', type=Path)

    def handle(self, *args, **options):
        if not options['confirm_sitewide_readonly'] or not 1 <= options['max_accounts'] <= 1000:
            raise CommandError('Require explicit sitewide read-only authorization and a bounded account limit.')
        if connection.vendor != 'postgresql' or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1':
            raise CommandError('PostgreSQL and unchanged online default V1 required.')
        root = (Path(settings.BASE_DIR) / '.tools').resolve()
        directory = options['output_dir'] or root / 'population-readiness' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if not directory.resolve().is_relative_to(root) or directory.exists():
            raise CommandError('Use a new private .tools directory; overwrite refused.')
        started = perf_counter()
        counts = Counter(dict.fromkeys(('active_accounts', 'accounts_with_journal', 'personalized_accounts',
            'preference_only_accounts', 'cold_start_accounts', 'natural_holdout_eligible_accounts'), 0))
        try:
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only(
                        'pk', 'is_active')[:options['max_accounts'] + 1])
                    if len(users) > options['max_accounts']:
                        raise ValueError('Account bound exceeded; no silent sampling or partial report.')
                    for user in users:
                        if perf_counter() - started > 120:
                            raise ValueError('Readiness time budget exceeded; no partial success.')
                        context = user_context(user, evaluation_baseline=True)
                        counts['active_accounts'] += 1
                        counts['accounts_with_journal'] += int(bool(context['owned']) or context['profile']['cold_start_state']['journal_record_count'] > 0)
                        counts['personalized_accounts'] += int(context['mode'] == 'personalized')
                        counts['preference_only_accounts'] += int(context['mode'] == 'preference_based')
                        counts['cold_start_accounts'] += int(context['mode'] == 'cold_start')
                        counts['natural_holdout_eligible_accounts'] += int(context['mode'] == 'personalized'
                            and len(context['seeds']) >= max(4, settings.RECOMMENDATION_PROFILE_MIN_SAMPLES + 1))
            report = {'contract_version': 'recommendations.population-readiness.v1',
                'counts': dict(counts), 'read_only': True, 'online_default': 'content.v1',
                'no_review_reads': True, 'no_external_http': True, 'private_details_exported': False,
                'ranking_performed': False, 'original_frozen_cohort_changed': False,
                'cohort_selected': False, 'approval_issued': False,
                'scope': 'active accounts only; aggregate feasibility, not recommendation accuracy',
                'seconds': round(perf_counter() - started, 3)}
            directory.mkdir(parents=True)
            with (directory / 'readiness.json').open('x', encoding='utf-8') as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
            self.stdout.write(json.dumps(report, ensure_ascii=False))
            self.stdout.write(f'Private report: {directory}')
        except (ValueError, OSError, DatabaseError) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Readiness stopped safely ({type(exc).__name__}).') from None
