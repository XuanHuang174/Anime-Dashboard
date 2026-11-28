import copy
import json
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import connection, close_old_connections, transaction
from django.utils import timezone
from rest_framework.test import APITransactionTestCase, APIClient
from .models import (Anime,UserAnimeRecord,JournalImportPlan,JournalImportItem,JournalSyncBaseline,
                     JournalImportOperation,WatchEvent,WatchPlan,CurationLibrary)
from .utils.scraper import MetadataScraperError


class ImportPlanTests(APITransactionTestCase):
    def setUp(self):
        caches['throttle'].clear();self.addCleanup(caches['throttle'].clear)
        self.user=get_user_model().objects.create_user('import-owner')
        self.other=get_user_model().objects.create_user('import-other')
        self.client.force_authenticate(self.user)
        self.collections=[self.collection(900001)]
        self.fetch=patch('anime.import_plans.MetadataScraper.get_user_anime_collections',side_effect=lambda uid,offset,limit:{'total':len(self.collections),'data':self.collections[offset:offset+limit]})
        self.details=patch('anime.import_plans.MetadataScraper.get_bangumi_details',side_effect=lambda sid:{'bangumi_id':sid,'chinese_title':f'来源作品 {sid}','japanese_title':f'Source {sid}','airing_period':'2026-01','studio':'来源公司','episodes':'12','description':'来源剧情','poster_url':''})
        self.fetch.start();self.details.start();self.addCleanup(self.fetch.stop);self.addCleanup(self.details.stop)

    def collection(self,sid):
        return {'subject_id':sid,'subject':{'id':sid,'name_cn':f'来源作品 {sid}','name':f'Source {sid}','eps':12},'type':3,'ep_status':4,'rate':8,'comment':'来源短评','tags':['日常']}

    def work(self,sid=900001,**fields):
        anime=Anime.objects.create(bgm_id=sid,chinese_title=f'来源作品 {sid}',japanese_title=f'Source {sid}',airing_period='2026-01',studio='原公共公司')
        return UserAnimeRecord.objects.create(user=self.user,anime=anime,watch_status='watching',watched_eps=3,total_eps=12,personal_score='9.5',review='本地长评',custom_tags=['年度最佳'],**fields)

    def post(self,path,data,code=200):
        r=self.client.post('/api/import-plans/'+path,data,format='json');self.assertEqual(r.status_code,code,r.data);return r.data

    def create(self,**extra): return self.post('',{'uid':'synthetic-source','request_id':str(uuid4()),**extra})
    def read(self,plan,**query):
        r=self.client.get(f"/api/import-plans/{plan['id']}/",query);self.assertEqual(r.status_code,200,r.data);return r.data
    def ready(self):
        plan=self.create()
        for cursor in range(len(self.collections)):self.post(f"{plan['id']}/scan/",{'cursor':cursor})
        return self.read(plan)
    def body(self,plan,decisions): return {'revision':plan['revision'],'token':plan['token'],'operation_id':str(uuid4()),'decisions':decisions}
    def decide(self,plan,action,fields=None,clear_fields=None,index=0): return {'item_id':plan['items'][index]['id'],'action':action,**({'fields':fields} if fields is not None else {}),**({'clear_fields':clear_fields} if clear_fields is not None else {})}
    def apply(self,plan,*decisions,code=200):return self.post(f"{plan['id']}/apply/",self.body(plan,list(decisions)),code)

    def test_preview_has_no_journal_shared_image_or_history_writes_and_reports_full_fields(self):
        record=self.work();before=list(UserAnimeRecord.objects.values());shared=list(Anime.objects.values())
        with patch('anime.utils.scraper.MetadataScraper.download_bangumi_poster',side_effect=AssertionError('no preview images')):
            plan=self.ready()
        self.assertEqual(plan['status'],'ready');self.assertEqual(plan['counts'],{'changed':1})
        fields={r['field']:r for r in plan['items'][0]['comparison']['fields']}
        self.assertEqual(fields['personal_score']['before'],'9.5');self.assertEqual(fields['personal_score']['after'],'8.0')
        self.assertTrue(all(r['default']=='keep' for r in fields.values()))
        self.assertEqual(list(UserAnimeRecord.objects.values()),before);self.assertEqual(list(Anime.objects.values()),shared)
        self.assertEqual(WatchEvent.objects.count()+WatchPlan.objects.count()+CurationLibrary.objects.count(),0)

    def test_source_apply_only_chosen_fields_preserves_custom_data_and_public_metadata(self):
        record=self.work();plan=self.ready()
        result=self.apply(plan,self.decide(plan,'source',['studio','personal_score']))
        record.refresh_from_db();self.assertEqual(record.private_studio,'来源公司');self.assertEqual(str(record.personal_score),'8.0')
        self.assertEqual(record.review,'本地长评');self.assertEqual(record.custom_tags,['年度最佳']);self.assertEqual(record.watched_eps,3)
        self.assertEqual(record.anime.studio,'原公共公司');self.assertEqual(result['results'][0]['fields'],['studio','personal_score'])
        baseline=JournalSyncBaseline.objects.get();self.assertEqual(baseline.local_values['review'],'本地长评');self.assertEqual(baseline.source_values['review'],'来源短评')

    def test_legacy_uninitialized_metadata_is_snapshotted_before_private_update(self):
        record=self.work();UserAnimeRecord.objects.filter(pk=record.pk).update(metadata_initialized=False)
        plan=self.ready();self.apply(plan,self.decide(plan,'source',['studio']))
        record.refresh_from_db();self.assertTrue(record.metadata_initialized)
        self.assertEqual(record.effective_anime_metadata()['studio'],'来源公司')
        self.assertEqual(record.private_chinese_title,record.anime.chinese_title)
        self.assertEqual(record.anime.studio,'原公共公司')

    def test_keep_and_fill_empty_are_explicit_and_zero_progress_is_not_empty(self):
        record=self.work();record.private_studio='';record.watched_eps=0;record.save()
        plan=self.ready();self.apply(plan,self.decide(plan,'fill-empty',['studio','review','watched_eps']))
        record.refresh_from_db();self.assertEqual(record.private_studio,'来源公司');self.assertEqual(record.review,'本地长评');self.assertEqual(record.watched_eps,0)
        plan=self.ready();self.apply(plan,self.decide(plan,'keep'));record.refresh_from_db();self.assertEqual(str(record.personal_score),'9.5')
        self.assertFalse(self.read(plan)['items'][0]['comparison']['first_review'])

    def test_source_empty_requires_confirmation_and_absent_field_cannot_be_written(self):
        record=self.work();self.collections[0]['comment']='';self.collections[0]['rate']=0
        plan=self.ready();self.apply(plan,self.decide(plan,'source',['review','personal_score']),code=400)
        record.refresh_from_db();self.assertEqual(record.review,'本地长评')
        self.apply(plan,self.decide(plan,'source',['review','personal_score'],['review','personal_score']))
        record.refresh_from_db();self.assertEqual(record.review,'');self.assertIsNone(record.personal_score)
        plan=self.ready();self.apply(plan,self.decide(plan,'source',['baike_url']),code=400)

    def test_new_records_batch_creation_is_private_and_original_receipt_is_exactly_once(self):
        self.collections.append(self.collection(900002));plan=self.ready()
        body=self.body(plan,[self.decide(plan,'create',index=i) for i in (0,1)])
        result=self.post(f"{plan['id']}/apply/",body)
        self.assertEqual(UserAnimeRecord.objects.count(),2);self.assertFalse(Anime.objects.filter(is_active=True).exists())
        self.assertEqual(WatchEvent.objects.count(),0)
        self.assertEqual(self.post(f"{plan['id']}/apply/",body)['results'],result['results'])
        self.assertTrue(self.post(f"{plan['id']}/apply/",body)['replayed'])
        body['decisions'].pop();self.post(f"{plan['id']}/apply/",body,409)
        self.assertEqual(JournalImportOperation.objects.count(),1)

    def test_changed_record_expired_token_wrong_revision_and_tamper_do_not_apply(self):
        record=self.work();plan=self.ready();body=self.body(plan,[self.decide(plan,'source',['review'])])
        for change in ({'revision':0},{'token':'invalid'},{'unexpected':True}):self.post(f"{plan['id']}/apply/",{**body,**change},400 if 'unexpected' in change else 409)
        with patch('django.core.signing.time.time',return_value=9999999999):self.post(f"{plan['id']}/apply/",body,409)
        record.review='本地后来填写';record.save();self.post(f"{plan['id']}/apply/",body,409)
        record.refresh_from_db();self.assertEqual(record.review,'本地后来填写');self.assertFalse(JournalSyncBaseline.objects.exists())

    def test_batch_rolls_back_if_later_decision_fails(self):
        record=self.work();self.collections.append(self.collection(900002));plan=self.ready()
        self.apply(plan,self.decide(plan,'source',['review']),self.decide(plan,'source',['review'],index=1),code=400)
        record.refresh_from_db();self.assertEqual(record.review,'本地长评')
        self.assertFalse(JournalSyncBaseline.objects.exists());self.assertFalse(JournalImportOperation.objects.exists())

    def test_progress_uses_observed_history_without_viewing_dates_or_rewatch(self):
        record=self.work();plan=self.ready();self.apply(plan,self.decide(plan,'source',['watched_eps']))
        record.refresh_from_db();self.assertEqual(record.watched_eps,4)
        event=WatchEvent.objects.get();self.assertEqual(event.source,'collection_import');self.assertEqual(event.occurred_on,'')
        self.assertEqual(record.watch_rounds.count(),1);self.assertEqual(record.watch_rounds.get().started_on,'')

    def test_legacy_name_duplicate_or_user_unlink_never_auto_binds(self):
        anime=Anime.objects.create(chinese_title='来源作品 900001',japanese_title='Source 900001',airing_period='2025-01')
        UserAnimeRecord.objects.create(user=self.user,anime=anime)
        plan=self.ready();self.assertEqual(plan['counts'],{'needs_review':1});self.apply(plan,self.decide(plan,'create'),code=409)
        UserAnimeRecord.objects.all().delete()  # Isolated test rows only, no filesystem operations.
        record=self.work()
        from recommendations.models import RecommendationIdentityOverride
        RecommendationIdentityOverride.objects.create(record=record,enabled=True,target=None)
        plan=self.ready();self.assertEqual(plan['counts'],{'needs_review':1})

    def test_scan_replay_duplicate_remote_ids_total_change_and_unavailable_sources(self):
        plan=self.create();first=self.post(f"{plan['id']}/scan/",{'cursor':0})
        self.assertEqual(self.post(f"{plan['id']}/scan/",{'cursor':0})['cursor'],first['cursor'])
        self.collections.append(self.collection(900001));plan=self.create();self.post(f"{plan['id']}/scan/",{'cursor':0})
        self.assertEqual(self.post(f"{plan['id']}/scan/",{'cursor':1})['status'],'invalid')
        plan=self.create();self.collections.append(self.collection(900003))
        self.assertEqual(self.post(f"{plan['id']}/scan/",{'cursor':0})['status'],'invalid')
        with patch('anime.import_plans.MetadataScraper.get_user_anime_collections',side_effect=MetadataScraperError('synthetic')):self.post('',{'uid':'x','request_id':str(uuid4())},502)
        self.assertFalse(UserAnimeRecord.objects.exists())

    def test_source_errors_are_classified_without_silent_truncation(self):
        self.collections[0]['tags']=['x'*51]
        plan=self.ready();self.assertEqual(plan['counts'],{'failed':1})
        self.apply(plan,self.decide(plan,'create'),code=409);self.assertFalse(Anime.objects.exists())

    def test_inconsistent_source_ids_and_unbounded_pages_are_rejected(self):
        self.collections[0]['subject']['id']=900002
        plan=self.ready();self.assertEqual(plan['counts'],{'failed':1})
        response=self.client.get(f"/api/import-plans/{plan['id']}/",{'page':str(10**30)})
        self.assertEqual(response.status_code,400)
        response=self.client.post('/api/import-plans/json-diff/?page='+str(10**30),{'records':[]},format='json')
        self.assertEqual(response.status_code,400)

    def test_owner_scope_create_idempotency_and_no_store_on_errors(self):
        body={'uid':'synthetic-source','request_id':str(uuid4())};first=self.post('',body)
        self.assertEqual(self.post('',body)['id'],first['id']);self.post('',{**body,'uid':'other'},409)
        self.client.force_authenticate(self.other)
        for suffix,method in (('',self.client.get),('scan/',self.client.post),('apply/',self.client.post)):
            r=method(f"/api/import-plans/{first['id']}/{suffix}",{});self.assertIn(r.status_code,(400,404));self.assertIn('no-store',r['Cache-Control'])
        self.client.force_authenticate(None);self.assertIn(self.client.get(f"/api/import-plans/{first['id']}/").status_code,(401,403))

    def test_json_diff_is_paginated_private_and_preserves_existing_restore_token(self):
        record=self.work();raw=json.loads(self.client.get('/api/export/').content)
        raw['records'][0]['review']='恢复后的评价'
        before=list(UserAnimeRecord.objects.values())
        response=self.client.post('/api/import-plans/json-diff/',raw,format='json')
        self.assertEqual(response.status_code,200,response.data);self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response.data['counts'],{'changed':1});self.assertEqual(list(UserAnimeRecord.objects.values()),before)
        fields={r['field']:r for r in response.data['items'][0]['fields']}
        self.assertEqual(fields['review']['before'],'本地长评');self.assertEqual(fields['review']['after'],'恢复后的评价')
        self.assertTrue(response.data['restore']['token'])

    def test_keep_receipt_survives_expiry_and_deleted_baseline_is_not_a_new_import(self):
        record=self.work();plan=self.ready();body=self.body(plan,[self.decide(plan,'keep')])
        self.post(f"{plan['id']}/apply/",body)
        with patch('django.core.signing.time.time',return_value=9999999999):self.assertTrue(self.post(f"{plan['id']}/apply/",body)['replayed'])
        record.delete()
        plan=self.ready();self.assertEqual(plan['counts'],{'needs_review':1})

    def test_effective_alias_is_respected_and_conflicting_or_stale_alias_is_blocked(self):
        record=self.work()
        from recommendations.models import RecommendationAnime,RecommendationIdentityOverride
        target=RecommendationAnime.objects.create(bangumi_id=900002,title_zh='其他季度',title_jp='Other',season='2026-04',nsfw=False)
        RecommendationIdentityOverride.objects.create(record=record,enabled=True,target=target,record_fingerprint='stale',catalog_fingerprint='stale')
        plan=self.ready();self.assertEqual(plan['counts'],{'needs_review':1})

    def test_json_diff_flags_absent_history_as_preserved_and_lists_removed_records(self):
        self.work();raw=json.loads(self.client.get('/api/export/').content)
        raw['records'][0].pop('watch_history',None);raw['records'][0].pop('watch_plan',None)
        response=self.client.post('/api/import-plans/json-diff/',raw,format='json')
        self.assertEqual(response.status_code,200,response.data)
        fields={r['field']:r for r in response.data['items'][0]['fields']}
        self.assertEqual(fields['watch_history']['effect'],'preserve_if_uniquely_matched')
        self.assertFalse(fields['watch_history']['changed'])
        response=self.client.post('/api/import-plans/json-diff/?kind=removed',{'records':[]},format='json')
        self.assertEqual(response.status_code,200,response.data);self.assertEqual(response.data['counts'],{'removed':1})

    def test_concurrent_same_operation_has_one_application(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.work();plan=self.ready();body=self.body(plan,[self.decide(plan,'source',['review'])]);barrier=Barrier(2)
        def write(_):
            close_old_connections()
            try:
                client=APIClient();client.force_authenticate(get_user_model().objects.get(pk=self.user.pk));barrier.wait(timeout=10)
                r=client.post(f"/api/import-plans/{plan['id']}/apply/",body,format='json');return r.status_code,r.data.get('replayed')
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,[0,1]))
        self.assertEqual(sorted(results),[(200,False),(200,True)]);self.assertEqual(JournalImportOperation.objects.count(),1)

    def test_owner_mutex_does_not_deadlock_a_record_writer_foreign_key_check(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL FK and row locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from .import_plans import owner_lock
        record=self.work();record_ready=Event();owner_ready=Event()
        def record_writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:cursor.execute("SET LOCAL lock_timeout = '5s'")
                    row=UserAnimeRecord.objects.select_for_update().get(pk=record.pk)
                    row.review='并发私人更正';row.save()
                    record_ready.set();self.assertTrue(owner_ready.wait(10))
                return True
            finally:close_old_connections()
        def owner_reader():
            close_old_connections()
            try:
                self.assertTrue(record_ready.wait(10))
                with transaction.atomic():
                    with connection.cursor() as cursor:cursor.execute("SET LOCAL lock_timeout = '5s'")
                    owner_lock(self.user);owner_ready.set()
                    return UserAnimeRecord.objects.select_for_update().get(pk=record.pk).review
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer=pool.submit(record_writer);reader=pool.submit(owner_reader)
            self.assertTrue(writer.result());self.assertEqual(reader.result(),'并发私人更正')

    def test_json_diff_reports_private_color_and_reference_url_changes(self):
        self.work();raw=json.loads(self.client.get('/api/export/').content)
        raw['records'][0]['anime'].update(tag_colors={'日常':'slate'},baike_url='https://example.org/work')
        response=self.client.post('/api/import-plans/json-diff/',raw,format='json')
        self.assertEqual(response.status_code,200,response.data)
        fields={r['field']:r for r in response.data['items'][0]['fields']}
        self.assertTrue(fields['tag_colors']['changed']);self.assertTrue(fields['baike_url']['changed'])
