"""Local synthetic polish fixtures; never touches production or removes existing rows."""
import json
import runpy
from pathlib import Path

root = Path(__file__).resolve().parents[1]
runpy.run_path(str(root / 'scripts' / 'seed-watch-plans-b3-e2e.py'))
from anime.models import UserAnimeRecord
from recommendations.models import RecommendationAnime

path = root / '.tools' / 'watch-plans-b2-fixture.json'
fixture = json.loads(path.read_text(encoding='utf-8'))
for identity, title, season in [(940011, '原始标准作品', '2020-01'), (940012, '正确的标准作品', '2020-04')]:
    RecommendationAnime.objects.get_or_create(bangumi_id=identity, defaults={
        'title_zh': title, 'title_jp': f'Synthetic {identity}', 'season': season, 'status_type': 'TV', 'nsfw': False})
UserAnimeRecord.objects.filter(pk=fixture['records'][0], user_id=fixture['user_id']).update(verified_bangumi_id=940011)
print('Synthetic private identity fixtures ready.')
