"""Fresh annual export samples only in the existing isolated local SQLite DB."""
import json
import runpy
from pathlib import Path

root = Path(__file__).resolve().parents[1]
scope = runpy.run_path(str(root / 'scripts' / 'seed-watch-review-d2-e2e.py'))
work, send, finish, user = (scope[key] for key in ('work', 'send', 'finish', 'user'))
from django.conf import settings
from anime.models import UserAnimeRecord
from anime.watch_review import read_review

assert Path(settings.DATABASES['default']['NAME']).resolve() == root / '.tools' / 'watch-history-a3.sqlite3'
fixture = json.loads((root / '.tools' / 'watch-review-d2-fixture.json').read_text(encoding='utf-8'))
for index in range(101):
    record = work(f'D3 年度分页 {index:03}')
    finish(record, f'2026-{index % 12 + 1:02}', f'2026-{index % 12 + 1:02}')
    if index < 26:
        record.private_poster_url = f'https://media.xh-anime.com/synthetic-d3-{index}.jpg'
        record.save(update_fields=['private_poster_url'])
send(UserAnimeRecord.objects.get(pk=fixture['year_only_record']), 'update-round', round_number=1,
     note='D3 私人轮次备注：默认不应导出。')
send(UserAnimeRecord.objects.get(pk=fixture['review_record']), 'note', occurred_on='2026',
     note=('D3 年度私人随记\n' + '完整保留的合成文字🌸' * 180)[:1990])
fixture.update(annual_expected=read_review(user, {'year': fixture['year']}))
(root / '.tools' / 'watch-review-d3-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print('D3 synthetic annual export fixtures ready; credentials remain in ignored .tools only.')
