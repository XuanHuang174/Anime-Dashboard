"""Create fresh synthetic fixtures in the explicitly isolated local A3 database."""
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.watch_history_e2e_settings'
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken
from anime.models import Anime, UserAnimeRecord

assert Path(settings.DATABASES['default']['NAME']).resolve() == root / '.tools' / 'watch-history-a3.sqlite3'
user = get_user_model().objects.create_user(f'watch-a3-{uuid4().hex[:10]}')
profile = user.profile
profile.is_public = True
profile.public_status = 'approved'
profile.save()
records = []
for title in ('A3 合成观看历程', 'A3 文件外作品'):
    anime, _ = Anime.objects.get_or_create(chinese_title=title, airing_period='2020-1', defaults={'episodes': '12', 'base_tags': ['日常']})
    row = UserAnimeRecord.objects.create(user=user, anime=anime, watch_status='watching', watched_eps=2, total_eps=12, personal_score='9.1', review='保留综合评分与长评')
    records.append(row.pk)
token = AccessToken.for_user(user)
fixture = {'username': user.username, 'user_id': user.pk, 'token': str(token), 'record_id': records[0], 'other_id': records[1]}
(root / '.tools' / 'watch-history-a3-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print('Synthetic A3 fixtures ready; credentials remain in ignored .tools only.')
