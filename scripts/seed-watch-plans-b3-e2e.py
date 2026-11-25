"""Fresh B3 browser fixtures. Local synthetic database only; no existing rows removed."""
import json
import runpy
from pathlib import Path

root = Path(__file__).resolve().parents[1]
runpy.run_path(str(root / 'scripts' / 'seed-watch-plans-e2e.py'))
from recommendations.models import RecommendationAnime
from django.contrib.auth import get_user_model
catalog, _ = RecommendationAnime.objects.get_or_create(bangumi_id=940001, defaults={
    'title_zh': 'B3 推荐安排合成作品', 'title_jp': 'B3 Synthetic Recommendation',
    'season': '2020-01', 'status_type': 'TV', 'nsfw': False, 'score': 8.8,
    'tags': '日常', 'synopsis': 'Local synthetic recommendation only',
})
fixture_path = root / '.tools' / 'watch-plans-b2-fixture.json'
fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
profile = get_user_model().objects.get(pk=fixture['user_id']).profile
profile.nickname = 'XuanHuang'; profile.save(update_fields=['nickname'])
fixture['recommendation_id'] = catalog.bangumi_id
fixture_path.write_text(json.dumps(fixture), encoding='utf-8')
print('Synthetic B3 recommendation ready; test-only fixture remains ignored.')
