"""Synthetic E1 restoration UI fixtures. Fixed isolated local SQLite only."""
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.journal_polish_e2e_settings'
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken
from anime.models import Anime, UserAnimeRecord
from anime.curations import command, version

assert Path(settings.DATABASES['default']['NAME']).resolve() == root / '.tools' / 'watch-history-a3.sqlite3'
user = get_user_model().objects.create_user(f'curation-e1-{uuid4().hex[:10]}')
profile = user.profile; profile.is_public = True; profile.public_status = 'approved'; profile.save()


def send(action, **fields):
    return command(user, {'action': action, 'version': version(user), 'operation_id': str(uuid4()), **fields})


records = [UserAnimeRecord.objects.create(user=user,
    anime=Anime.objects.get_or_create(chinese_title=title, airing_period='2026-01', defaults={'episodes': '12'})[0],
    total_eps=12, watch_status='on_hold', watched_eps=3) for title in ('E1 合成雨天作品', 'E1 合成入门作品', 'E1 合成失效作品')]
one = send('create', title='E1 雨天慢慢看', description='私人主题简介')['list_id']
send('add', list_id=one, record_ids=[records[0].pk, records[2].pk])
two = send('create', title='E1 入门路线')['list_id']; send('add', list_id=two, record_ids=[records[1].pk])
from anime.models import CurationEntry
entry = CurationEntry.objects.get(record=records[0])
send('update-entry', list_id=one, entry_id=str(entry.portable_id), reason='私人入选理由🌸')
records[2].delete()
fixture = {'username': user.username, 'user_id': user.pk, 'token': str(AccessToken.for_user(user)),
           'list_id': one, 'entry_id': str(entry.portable_id)}
(root / '.tools' / 'curations-e1-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print('E1 synthetic fixtures ready; credentials remain in ignored .tools only.')
