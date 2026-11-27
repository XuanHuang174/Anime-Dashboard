"""Fresh D2 fixtures only in the isolated local SQLite DB. Existing rows stay intact."""
import json
import runpy
from pathlib import Path

root = Path(__file__).resolve().parents[1]
scope = runpy.run_path(str(root / 'scripts' / 'seed-watch-review-c2-e2e.py'))
work, send, finish, user = (scope[key] for key in ('work', 'send', 'finish', 'user'))
from django.conf import settings
from anime.watch_review import read_review

assert Path(settings.DATABASES['default']['NAME']).resolve() == root / '.tools' / 'watch-history-a3.sqlite3'
year_only = work('D2 只记得本年'); finish(year_only, '2026', '2026')
memory = work('D2 年份回忆'); send(memory, 'memory', occurred_on='2026', note='知道年份，不能猜测月份。')
repeat = work('D2 跨月重看'); finish(repeat, '2026-03', '2026-03')
send(repeat, 'start-rewatch', started_on='2026-10')
send(repeat, 'transition', watch_status='completed', watched_eps=12, occurred_on='2026-10')
send(repeat, 'update-round', round_number=2, rating='7.0')
leap = work('D2 闰年二月'); finish(leap, '2024-02', '2024-02-29')
fixture = json.loads((root / '.tools' / 'watch-review-c2-fixture.json').read_text(encoding='utf-8'))
fixture.update(year='2026', year_only_record=year_only.pk, repeat_record=repeat.pk,
               annual_expected=read_review(user, {'year': '2026'}),
               month_expected=read_review(user, {'month': '2026-10'})['summary'])
(root / '.tools' / 'watch-review-d2-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print('D2 synthetic annual fixtures ready; credentials remain in ignored .tools only.')
