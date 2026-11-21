from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from anime.models import TagCleanupJob
from anime.tag_cleanup import prune


class Command(BaseCommand):
    help = 'Redact expired tag-cleanup payloads. No journal or shared metadata is modified.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 500: raise CommandError('limit must be between 1 and 500')
        owners = list(TagCleanupJob.objects.exclude(status='expired').filter(expires_at__lte=timezone.now())
                      .values_list('user_id', flat=True).distinct()[:limit])
        for owner in owners:
            with transaction.atomic():
                user = get_user_model().objects.select_for_update().filter(pk=owner).first()
                if user: prune(user)
        self.stdout.write(f'Redacted expired cleanup payloads for {len(owners)} owner(s).')
