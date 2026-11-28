"""Fresh E2 UI fixtures in the existing explicitly isolated local SQLite DB."""
import json
import runpy
from pathlib import Path

root = Path(__file__).resolve().parents[1]
scope = runpy.run_path(str(root / 'scripts' / 'seed-curations-e1-e2e.py'))
user, send = scope['user'], scope['send']
from anime.models import Anime, UserAnimeRecord, CurationEntry

fixture = json.loads((root / '.tools' / 'curations-e1-fixture.json').read_text(encoding='utf-8'))
send('update', list_id=fixture['list_id'], title='E2 跨页主题', description='完整编排及独立入选理由')
records = [UserAnimeRecord.objects.create(user=user,
    anime=Anime.objects.get_or_create(chinese_title=f'E2 合成作品 {i:03}', airing_period='2026-01', defaults={'base_tags': ['日常'], 'episodes': '12'})[0],
    watch_status='on_hold', watched_eps=3, total_eps=12) for i in range(115)]
send('add', list_id=fixture['list_id'], record_ids=[row.pk for row in records[:100]])
send('add', list_id=fixture['list_id'], record_ids=[row.pk for row in records[100:103]])
for i in range(13): send('create', title=f'E2 片单 {i:02}', description='用于首页分页和位置返回')
placeholder = CurationEntry.objects.get(collection__portable_id=fixture['list_id'], record=None)
fixture.update(full_count=105, free_record_id=records[103].pk, free_title=records[103].effective_anime_metadata()['chinese_title'],
               placeholder_id=str(placeholder.portable_id))
(root / '.tools' / 'curations-e2-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print('E2 synthetic fixtures ready; account credentials remain in ignored .tools only.')
