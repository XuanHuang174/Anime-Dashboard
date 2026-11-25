"""Fresh private B2 fixtures, exclusively in the local synthetic SQLite DB."""
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
from anime.models import Anime, UserAnimeRecord, WatchPlanQueue
from anime.watch_plans import command

assert Path(settings.DATABASES['default']['NAME']).resolve() == root / '.tools' / 'watch-history-a3.sqlite3'
user = get_user_model().objects.create_user(f'plans-b2-{uuid4().hex[:10]}')
profile = user.profile; profile.is_public = True; profile.public_status = 'approved'; profile.save()
records = []
for index, (title, status) in enumerate([('B2 首次观看', 'plan'), ('B2 恢复搁置', 'on_hold'), ('B2 准备重看', 'completed'), ('B2 进行中', 'watching'), ('B2 正常安排', 'plan')]):
    anime, _ = Anime.objects.get_or_create(chinese_title=title, airing_period='2020-1', defaults={'episodes': '12', 'base_tags': ['日常']})
    record = UserAnimeRecord.objects.create(user=user, anime=anime, watch_status=status, watched_eps=2 if status == 'watching' else 0, total_eps=12, personal_score='9.1', review='保留作品长评')
    records.append(record.pk)
    if index:
        queue = WatchPlanQueue.objects.filter(user=user).first()
        command(user, record.pk, {'action': 'enroll', 'version': queue.revision if queue else 0, 'operation_id': str(uuid4()), 'priority': 1 if index == 2 else 2, 'note': f'合成安排 {title}', 'planned_for': '2030-02' if index == 2 else ''})
more = []
for index in range(22):
    anime, _ = Anime.objects.get_or_create(chinese_title=f'B2 分页作品 {index:02}', airing_period='2019-1')
    more.append(UserAnimeRecord.objects.create(user=user, anime=anime).pk)
fixture = {'username': user.username, 'user_id': user.pk, 'token': str(AccessToken.for_user(user)), 'records': records, 'more_ids': more}
(root / '.tools' / 'watch-plans-b2-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print('Synthetic B2 fixtures ready; credentials stay in ignored .tools only.')
