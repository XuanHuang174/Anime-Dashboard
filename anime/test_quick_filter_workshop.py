import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase
from anime.models import Anime, UserAnimeRecord, UserQuickFilter, UserProfile, FeaturedQuickFilter, FeaturedSubmission


class QuickFilterWorkshopTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('quick-owner')
        self.other = get_user_model().objects.create_user('quick-other')
        self.admin = get_user_model().objects.create_superuser('quick-admin', password='synthetic-only')
        self.client.force_authenticate(self.owner)
        self.contract = json.loads((Path(__file__).resolve().parents[1] / 'frontend/tests/fixtures/quickConditionsContract.json').read_text(encoding='utf-8'))
        self.rows = {}
        for row in self.contract['records']:
            anime = Anime.objects.create(chinese_title=row['title'], japanese_title=row.get('japaneseTitle', ''), airing_period=row['period'], base_tags=row['tags'])
            self.rows[row['id']] = UserAnimeRecord.objects.create(user=self.owner, anime=anime, personal_score=row['score'], watch_status=row['status'], review=row['review'])
        UserAnimeRecord.objects.create(user=self.other, anime=self.rows[1].anime, personal_score=10, watch_status='completed', review='别人的私有长评')
        self.url = '/api/quick-filters/'

    def create(self, **options):
        response = self.client.post(self.url, {'name': f'组合{UserQuickFilter.objects.count()}', **options}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_shared_contract_count_pagination_and_all_records_agree(self):
        for case in self.contract['cases']:
            with self.subTest(condition=case['filter']):
                draft = {'name': '预览草稿', **case['filter']}
                preview = self.client.post(self.url+'preview/?status=plan&page=99&search=不存在', draft, format='json')
                self.assertEqual(preview.status_code, 200, preview.data)
                self.assertEqual(preview.data['count'], len(case['ids']))
                saved = self.create(**case['filter'])
                all_rows = self.client.get('/api/records/', {'quick_filter': saved['id'], 'page_size': 'all'}).data
                all_rows = all_rows if isinstance(all_rows, list) else all_rows['results']
                expected = {self.rows[key].pk for key in case['ids']}
                self.assertEqual({row['id'] for row in all_rows}, expected)
                paged = self.client.get('/api/records/', {'quick_filter': saved['id'], 'page_size': 1}).data
                self.assertEqual(paged['count'], len(expected))

    def test_preview_does_not_create_update_or_expose_records_and_allows_existing_name(self):
        self.create(name='已保存', score_state='rated')
        before = list(UserAnimeRecord.objects.values()); configs = list(UserQuickFilter.objects.values())
        response = self.client.post(self.url+'preview/', {'name': '已保存', 'score_min': 9}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'count','scope'})
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(list(UserQuickFilter.objects.values()), configs)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def test_partial_updates_validate_existing_bounds_and_do_not_leave_hidden_unrated_range(self):
        saved = self.create(score_min=8, score_max=10)
        self.assertEqual(saved['score_state'], 'rated')
        url = f"{self.url}{saved['id']}/"
        self.assertEqual(self.client.patch(url, {'score_max': 7}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'score_state': 'unrated'}, format='json').status_code, 400)
        response = self.client.patch(url, {'score_min': None, 'score_max': None, 'score_state': 'unrated'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score_state'], 'unrated')

    def test_invalid_states_precision_ranges_and_empty_filter_are_rejected_on_preview_and_save(self):
        before = UserQuickFilter.objects.count()
        variants = [{}, {'score_min': -1}, {'score_max': 11}, {'score_min': 9.99}, {'score_min': 'NaN'}, {'score_min': True}, {'score_min': 9, 'score_max': 8},
                    {'score_state':'unrated','score_min':0}, {'score_state':'unknown'}, {'watch_statuses':['completed','completed']},
                    {'watch_statuses':['deleted']}, {'review_state':'unknown'}, {'tags':['日常'],'exclude_tags':['日常']}]
        for values in variants:
            for path in (self.url, self.url+'preview/'):
                with self.subTest(values=values, path=path):
                    response = self.client.post(path, {'name':'非法', **values}, format='json')
                    self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(UserQuickFilter.objects.count(), before)

    def test_read_and_write_permissions_and_public_owner_filtering(self):
        saved = self.create(score_min=9, watch_statuses=['completed'])
        profile = UserProfile.objects.get(user=self.owner)
        profile.public_status = 'approved'; profile.is_public = True; profile.save()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(f"{self.url}{saved['id']}/", {'name':'越权'}, format='json').status_code, 404)
        self.assertEqual(self.client.post(self.url+'preview/', {'name':'本人范围','score_min':9}, format='json').data['count'], 1)
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(self.url+'preview/', {'name':'匿名','score_state':'rated'}, format='json').status_code, (401,403))
        public = self.client.get('/api/public-journal/users/quick-owner/records/', {'quick_filter': saved['id'], 'page_size':'all'})
        self.assertEqual(public.status_code, 200, public.data)
        data = public.data if isinstance(public.data,list) else public.data['results']
        self.assertEqual([row['id'] for row in data], [self.rows[4].pk])

    def test_outer_filters_intersect_saved_conditions(self):
        saved = self.create(score_state='rated', watch_statuses=['completed','watching'])
        response = self.client.get('/api/records/', {'quick_filter':saved['id'], 'status':'plan', 'page_size':1})
        self.assertEqual(response.data['count'], 0)

    def test_database_constraints_guard_score_order_and_unrated_bounds(self):
        for fields in ({'score_min':9,'score_max':8}, {'score_state':'unrated','score_min':0}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                UserQuickFilter.objects.create(user=self.owner, name='无效数据库写入', **fields)

    def test_legacy_filter_defaults_do_not_restrict_unrated_records(self):
        old = UserQuickFilter.objects.create(user=self.owner, name='旧标签组合', tags=['日常'])
        response = self.client.get(self.url+f'{old.pk}/')
        self.assertEqual(response.data['score_state'], 'any'); self.assertEqual(response.data['watch_statuses'], [])
        rows = self.client.get('/api/records/', {'quick_filter':old.pk, 'page_size':'all'}).data
        rows = rows if isinstance(rows,list) else rows['results']
        self.assertIn(self.rows[1].pk, [row['id'] for row in rows])

    def test_featured_preview_is_admin_only_uses_author_snapshots_and_excludes_rejected(self):
        def submission(row, state):
            return FeaturedSubmission.objects.create(user=self.owner, source_anime=row.anime, chinese_title=row.anime.chinese_title,
                score=row.personal_score, watch_status=row.watch_status, review=row.review, tags=['日常'], year=2021, audit_status=state)
        submission(self.rows[4], 'approved'); submission(self.rows[6], 'rejected'); submission(self.rows[1], 'pending')
        draft = {'name':'精选高分','score_min':9,'watch_statuses':['completed'], 'review_state':'with','year_from':2020}
        url = '/api/featured-filters/preview/'
        self.assertEqual(self.client.post(url,draft,format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        response = self.client.post(url,draft,format='json')
        self.assertEqual(response.status_code, 200, response.data); self.assertEqual(response.data['count'], 1)
        saved = self.client.post('/api/featured-filters/',draft,format='json')
        self.assertEqual(saved.status_code, 201, saved.data)
        self.client.force_authenticate(None)
        public = self.client.get('/api/featured-filters/').data
        public = public if isinstance(public,list) else public['results']
        self.assertEqual(public[0]['review_state'],'with'); self.assertEqual(FeaturedQuickFilter.objects.count(),1)
