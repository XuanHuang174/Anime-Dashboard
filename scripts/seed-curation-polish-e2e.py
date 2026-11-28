"""Private, disposable local account; never production settings."""
import json
import runpy
from pathlib import Path
root=Path(__file__).resolve().parents[1]
scope=runpy.run_path(str(root/'scripts/seed-curations-e2-e2e.py'))
user=scope['user']
from anime.models import CurationEntry
fixture=json.loads((root/'.tools/curations-e2-fixture.json').read_text(encoding='utf-8'))
rows=list(CurationEntry.objects.filter(collection__portable_id=fixture['list_id'],record__isnull=False).select_related('record').order_by('position'))
for index,row in enumerate(rows):
    record=row.record
    record.private_poster_url='http://127.0.0.1:5191/tests/fixtures/curationPolishPoster.svg'
    record.private_airing_period=f'{1950+index%77}-01'
    record.private_japanese_title=f'合成の物語 {index:03}' if index%3 else ''
    record.private_base_tags=[f'题材 {index:03}','日常']
    if index==100:
        record.private_japanese_title='遠い星の合成物語'
        record.private_base_tags=['夜间','日常']
        fixture['target_id']=record.pk
        fixture['target_title']=record.private_chinese_title
        fixture['target_position']=row.position+1
    record.save(update_fields=['private_poster_url','private_airing_period','private_japanese_title','private_base_tags'])
fixture['cover_record_id']=rows[0].record_id
(root/'.tools/curation-polish-fixture.json').write_text(json.dumps(fixture),encoding='utf-8')
print('Curation polish fixtures ready; credentials remain in ignored .tools.')
