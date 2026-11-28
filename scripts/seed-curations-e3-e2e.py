"""Synthetic E3 integration, annual review and complete export samples only."""
import json
import runpy
from pathlib import Path
from uuid import uuid4
root=Path(__file__).resolve().parents[1]
scope=runpy.run_path(str(root/'scripts'/'seed-watch-review-d3-e2e.py'))
user=scope['user']
from anime.models import UserAnimeRecord,CurationEntry,Anime
from anime.curations import command,version
from recommendations.models import RecommendationAnime
fixture=json.loads((root/'.tools'/'watch-review-d3-fixture.json').read_text(encoding='utf-8'))
def send(action,**fields):return command(user,{'action':action,'version':version(user),'operation_id':str(uuid4()),**fields})
collection=send('create',title='E3 完整主题片单',description='E3 私人专题简介：默认不应导出。')['list_id']
records=list(UserAnimeRecord.objects.filter(user=user).order_by('pk')[:105])
send('add',list_id=collection,record_ids=[r.pk for r in records[:100]])
send('add',list_id=collection,record_ids=[r.pk for r in records[100:]])
entry=CurationEntry.objects.filter(collection__portable_id=collection).first()
send('update-entry',list_id=collection,entry_id=str(entry.portable_id),reason=('E3 私人入选理由🌸\n'+'完整保留的文字🌸'*200)[:1990])
dead=UserAnimeRecord.objects.create(user=user,anime=Anime.objects.get_or_create(chinese_title='E3 待保留的失效条目',airing_period='2026-01')[0])
send('add',list_id=collection,record_ids=[dead.pk]);dead.delete()
fixture.update(list_id=collection,entry_id=str(entry.portable_id),list_count=106)
(root/'.tools'/'curations-e3-fixture.json').write_text(json.dumps(fixture),encoding='utf-8')
print('E3 synthetic fixtures ready; credentials remain in ignored .tools only.')
