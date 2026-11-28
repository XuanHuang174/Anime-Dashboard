from . import test_curations as base_tests
from rest_framework.test import APITransactionTestCase


class CurationFilterTests(APITransactionTestCase):
    setUp = base_tests.CurationTests.setUp
    work = base_tests.CurationTests.work
    read = base_tests.CurationTests.read
    command = base_tests.CurationTests.command
    create = base_tests.CurationTests.create
    add = base_tests.CurationTests.add

    def test_filters_cover_all_pages_keep_original_order_and_effective_private_metadata(self):
        records = [self.work(f'跨页作品 {i:03}') for i in range(105)]
        target = records[100]
        target.private_japanese_title = '遠い星の物語'
        target.private_airing_period = '2001-07'
        target.private_base_tags = ['夜间']
        target.custom_tags = ['自选']
        target.save()
        list_id = self.create()
        self.add(list_id, *records[:100]); self.add(list_id, *records[100:])
        for query in ({'search':'遠い星'}, {'tag':'夜间'}, {'tag':'自选'}, {'year':'2001'}, {'search':'跨页作品 100','status':'on_hold','year':'2001','availability':'available'}):
            result = self.read(list_id, page_size=12, **query)
            self.assertEqual(result['count'], 1)
            self.assertEqual(result['total_count'], 105)
            self.assertEqual(result['results'][0]['position'], 100)
            self.assertEqual(result['results'][0]['record']['japanese_title'], '遠い星の物語')
            self.assertIn('自选', result['filter_options']['tags'])
            self.assertEqual(result['collection']['entry_count'], 105)
        first = self.read(list_id, status='on_hold', page_size=100)
        last = self.read(list_id, status='on_hold', page=2, page_size=100, version=first['version'])
        self.assertEqual(len(last['results']),5)
        self.assertEqual([r['position'] for r in last['results']],list(range(100,105)))
        self.assertEqual(self.read(list_id,tag='不存在')['count'],0)

    def test_placeholders_are_searchable_but_have_no_live_status_tag_or_year(self):
        list_id=self.create();self.add(list_id)
        self.record.delete()
        result=self.read(list_id,search='Synthetic',availability='unavailable')
        self.assertEqual(result['count'],1)
        self.assertTrue(result['results'][0]['unavailable'])
        for query in ({'status':'on_hold'},{'tag':'夜间'},{'year':'2026'},{'availability':'available'}):
            self.assertEqual(self.read(list_id,**query)['count'],0)

    def test_invalid_filters_fail_closed_and_owner_scope_is_preserved(self):
        list_id=self.create();self.add(list_id)
        for query in ({'status':'bad'},{'availability':'bad'},{'year':'999'},{'year':'２０２６'},{'tag':'x'*101},{'search':'x'*101},{'pinned':'true'}):
            self.assertEqual(self.client.get(f'/api/curations/{list_id}/',query).status_code,400)
        self.assertEqual(self.client.get(f'/api/curations/{list_id}/?tag=a&tag=b').status_code,400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/curations/{list_id}/',{'search':'雨天'}).status_code,404)

    def test_filtered_browsing_does_not_change_full_export_or_library_revision(self):
        list_id=self.create();self.add(list_id,self.record,self.work('另外一部'))
        before=self.read(list_id)
        self.assertEqual(self.read(list_id,search='雨天')['count'],1)
        exported=self.client.get(f'/api/curations/{list_id}/export/').data
        self.assertEqual(len(exported['entries']),2)
        self.assertEqual(exported['version'],before['version'])
        self.assertEqual(self.read(list_id)['count'],2)
