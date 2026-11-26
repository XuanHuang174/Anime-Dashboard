"""Synthetic local C2 review fixtures only; new account, no existing rows removed."""
import json
import runpy
from pathlib import Path
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
runpy.run_path(str(root / 'scripts' / 'seed-journal-polish-e2e.py'))
from django.conf import settings
from django.contrib.auth import get_user_model
from anime.models import Anime, UserAnimeRecord
from anime.watch_history import execute
from anime.watch_review import read_review

assert Path(settings.DATABASES['default']['NAME']).resolve() == root / '.tools' / 'watch-history-a3.sqlite3'
fixture = json.loads((root / '.tools' / 'watch-plans-b2-fixture.json').read_text(encoding='utf-8'))
user = get_user_model().objects.get(pk=fixture['user_id'])

def work(title):
    anime, _ = Anime.objects.get_or_create(chinese_title=title, airing_period='2020-1')
    return UserAnimeRecord.objects.create(user=user, anime=anime, personal_score='9.0', total_eps=12)

def send(record, action, **data):
    record.refresh_from_db()
    return execute(record.pk, user, {'action': action, 'version': record.watch_revision, 'operation_id': str(uuid4()), **data})

def finish(record, start='2026-10-02', end='2026-10-10'):
    send(record, 'transition', watch_status='watching', occurred_on=start)
    send(record, 'transition', watch_status='completed', watched_eps=12, occurred_on=end)

main = work('C2 十月相遇'); finish(main)
send(main, 'start-rewatch', started_on='2026-10-15', note='再看一次，保留这段轮次感想')
send(main, 'transition', watch_status='completed', watched_eps=12, occurred_on='2026-10-20')
send(main, 'note', occurred_on='2026-10-21', note='十月的独立随记\n' + '这是一段完整保存的合成感想。' * 25)
coarse = work('C2 只记得月份'); finish(coarse, '2026', '2026-10')
memory = work('C2 补录往事')
send(memory, 'memory', occurred_on='2026-10-12', note='记得十月的这一天，但文字不自动增加完成作品。')
for index in range(15):
    finish(work(f'C2 本期作品 {index:02}'), '2026-09-30', f'2026-10-{index + 1:02}')
fixture.update(month='2026-10', review_record=main.pk, coarse_record=coarse.pk,
               review_expected=read_review(user, {'month':'2026-10'})['summary'])
(root / '.tools' / 'watch-review-c2-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print('C2 synthetic review fixtures ready; account credentials remain in ignored .tools only.')
