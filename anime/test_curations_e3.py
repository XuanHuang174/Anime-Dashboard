import copy
from uuid import uuid4
from unittest.mock import patch
from django.db import connection, close_old_connections, DatabaseError
from django.core.cache import caches
from rest_framework.test import APITransactionTestCase, APIClient
from . import test_curations as base_tests
from .models import UserAnimeRecord, CurationEntry, CurationList, CurationLibrary, WatchEvent, WatchPlan, WatchRound
from .watch_history import execute


class CurationE3Tests(APITransactionTestCase):
    setUp = base_tests.CurationTests.setUp
    work = base_tests.CurationTests.work
    read = base_tests.CurationTests.read
    command = base_tests.CurationTests.command
    create = base_tests.CurationTests.create
    add = base_tests.CurationTests.add

    def review(self, **params):
        response=self.client.get('/api/watch-review/',params or {'year':'2026'})
        self.assertEqual(response.status_code,200,response.data)
        return response.data

    def viewed(self, record=None, date='2026-01'):
        record=record or self.record;record.refresh_from_db()
        execute(record.pk,self.user,{'action':'transition','version':record.watch_revision,'operation_id':str(uuid4()),'watch_status':'completed','watched_eps':12,'occurred_on':date})

    def source(self, **params):
        data=self.review(**params)
        return {**(params or {'year':'2026'}),'snapshot':data['snapshot']}

    def test_manual_creation_and_members_are_atomic_and_idempotent(self):
        other=self.work('另一部')
        body={'action':'create-with-members','title':'一次保存','record_ids':[other.pk,self.record.pk],'version':0,'operation_id':str(uuid4())}
        response=self.client.post('/api/curations/',body,format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual([r['record']['id'] for r in self.read(response.data['list_id'])['results']],[other.pk,self.record.pk])
        self.assertTrue(self.client.post('/api/curations/',body,format='json').data['replayed'])
        self.assertEqual(WatchPlan.objects.count()+WatchRound.objects.count()+WatchEvent.objects.count(),0)
        before=CurationList.objects.count()
        for ids in ([self.record.pk,9999999],[True],[],[self.record.pk]*2):
            result=self.command('create-with-members',title='不能留下空片单',record_ids=ids)
            self.assertEqual(result.status_code,400)
        self.assertEqual(CurationList.objects.count(),before)

    def test_review_creation_uses_definite_selected_records_and_preserves_private_facts(self):
        self.viewed();outside=self.work('未确定属于本期');self.viewed(outside,'2025')
        source=self.source()
        before=list(UserAnimeRecord.objects.values());events=list(WatchEvent.objects.values())
        result=self.command('create-from-review',title='年末自选',description='自己的专题',record_ids=[self.record.pk],review=source)
        self.assertEqual(result.status_code,200,result.data)
        self.assertIn('2026-01-01 至 2026-12-31',result.data['collection']['description'])
        self.assertEqual(self.read(result.data['list_id'])['count'],1)
        self.assertEqual(list(UserAnimeRecord.objects.values()),before);self.assertEqual(list(WatchEvent.objects.values()),events)
        bad=self.command('create-from-review',title='错归属',record_ids=[outside.pk],review=source)
        self.assertEqual(bad.status_code,400)

    def test_review_changed_expired_or_other_period_cannot_create_partial_collection(self):
        self.viewed();source=self.source()
        changed=copy.deepcopy(source);changed['year']='2025'
        for evidence in (changed,{**source,'snapshot':'invalid'},{'year':'2026'},{**source,'page':2}):
            response=self.command('create-from-review',title='拒绝',record_ids=[self.record.pk],review=evidence)
            self.assertIn(response.status_code,(400,409))
        self.record.refresh_from_db();self.record.personal_score='8.0';self.record.save()
        response=self.command('create-from-review',title='变化拒绝',record_ids=[self.record.pk],review=source)
        self.assertEqual(response.status_code,409)
        self.assertFalse(CurationList.objects.exists());self.assertFalse(CurationLibrary.objects.exists())

    def test_review_source_uses_actual_month_and_original_year_precision(self):
        self.viewed(date='2026')
        response=self.command('create-from-review',title='年度可选',record_ids=[self.record.pk],review=self.source(year='2026'))
        self.assertEqual(response.status_code,200)
        response=self.command('create-from-review',title='月度不可猜测',record_ids=[self.record.pk],review=self.source(month='2026-01'))
        self.assertEqual(response.status_code,400)

    def export_source(self,list_id,**params):
        return self.client.get(f'/api/curations/{list_id}/export/',params)

    def test_export_is_full_private_signed_and_rejects_metadata_change_without_library_revision_change(self):
        list_id=self.create(description='私人简介');entry=self.add(list_id)[0]
        self.command('update-entry',list_id=list_id,entry_id=entry['id'],reason='私人理由')
        source=self.export_source(list_id)
        self.assertEqual(source.status_code,200,source.data);self.assertIn('no-store',source['Cache-Control'])
        self.assertEqual(source.data['entries'][0]['reason'],'私人理由')
        old_revision=source.data['version']
        self.record.personal_score='8.4';self.record.save()
        self.assertEqual(self.read()['version'],old_revision)
        self.assertEqual(self.export_source(list_id,snapshot=source.data['snapshot']).status_code,409)
        current=self.export_source(list_id).data
        self.assertEqual(current['entries'][0]['record']['personal_score'],'8.4')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.export_source(list_id).status_code,404)

    def test_export_proof_expiry_version_and_format_are_fail_closed(self):
        list_id=self.create();self.add(list_id);source=self.export_source(list_id).data
        self.assertEqual(self.export_source(list_id,snapshot=source['snapshot']).status_code,200)
        self.assertEqual(self.export_source(list_id,version=source['version']-1).status_code,409)
        with patch('django.core.signing.time.time',return_value=9999999999):
            self.assertEqual(self.export_source(list_id,snapshot=source['snapshot']).status_code,409)
        for params in ({'page':1},{'snapshot':''},{'version':'bad'}): self.assertEqual(self.export_source(list_id,**params).status_code,400)
        self.record.delete();current=self.export_source(list_id)
        self.assertEqual(current.status_code,200);self.assertTrue(current.data['entries'][0]['unavailable'])

    def test_export_runs_in_enforced_readonly_transaction_and_database_failure_is_not_partial(self):
        list_id=self.create();self.add(list_id)
        from .curations import entry_data
        def check(*args,**kwargs):
            with connection.cursor() as cursor:
                if connection.vendor=='postgresql':
                    cursor.execute('SHOW transaction_read_only');self.assertEqual(cursor.fetchone()[0],'on')
                    cursor.execute('SHOW transaction_isolation');self.assertEqual(cursor.fetchone()[0],'repeatable read')
                else:self.assertEqual(cursor.execute('PRAGMA query_only').fetchone()[0],1)
            return entry_data(*args,**kwargs)
        with patch('anime.curation_exports.entry_data',side_effect=check):self.assertEqual(self.export_source(list_id).status_code,200)
        with patch('anime.curation_exports.entries',side_effect=DatabaseError('synthetic')):self.assertEqual(self.export_source(list_id).status_code,503)

    def test_review_creation_receipt_replays_after_source_changes_without_reapplying(self):
        self.viewed();source=self.source();body={'action':'create-from-review','title':'只创建一次','record_ids':[self.record.pk],'review':source,'version':0,'operation_id':str(uuid4())}
        first=self.client.post('/api/curations/',body,format='json');self.assertEqual(first.status_code,200)
        self.record.personal_score='7.0';self.record.save()
        retry=self.client.post('/api/curations/',body,format='json')
        self.assertEqual(retry.status_code,200);self.assertTrue(retry.data['replayed']);self.assertEqual(CurationList.objects.count(),1)

    def test_review_creation_and_real_history_edit_keep_lock_order_and_source_boundary(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL row locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.contrib.auth import get_user_model
        self.viewed();source=self.source();self.record.refresh_from_db();barrier=Barrier(2)
        user_id, record_id, revision=self.user.pk,self.record.pk,self.record.watch_revision
        def write(index):
            close_old_connections()
            try:
                client=APIClient();client.force_authenticate(get_user_model().objects.get(pk=user_id));barrier.wait(timeout=10)
                if index==0:return client.post('/api/curations/',{'action':'create-from-review','title':'并发专题','record_ids':[record_id],'review':source,'version':0,'operation_id':str(uuid4())},format='json').status_code
                return client.post(f'/api/records/{record_id}/watch-history/',{'action':'update-round','round_number':1,'version':revision,'operation_id':str(uuid4()),'note':'并发更正'},format='json').status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(write,[0,1]))
        self.assertIn(codes[0],(200,409));self.assertEqual(codes[1],200)
        self.assertEqual(CurationEntry.objects.count(),1 if codes[0]==200 else 0)
