from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import override_settings, TransactionTestCase
from django.db import connection, close_old_connections
from rest_framework.test import APITestCase, APIClient
from .models import Anime, UserAnimeRecord, WatchPlan, WatchPlanQueue, WatchEvent, WatchRound, UserQuickFilter
from .views import _apply_record_filters


@override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_CLASSES': []})
class WatchPlanB3Tests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('b3-owner')
        self.other = get_user_model().objects.create_user('b3-other')
        self.client.force_authenticate(self.user)
        self.records = [self.work(f'B3-{n}') for n in range(4)]
        for record in self.records[:3]:
            result = self.client.post(f'/api/records/{record.pk}/watch-plan/', {'action':'enroll','version':self.version(),'operation_id':str(uuid4()),'priority':2,'note':'保留独立备注'},format='json')
            self.assertEqual(result.status_code,200,result.data)

    def work(self,title,user=None):
        return UserAnimeRecord.objects.create(user=user or self.user,anime=Anime.objects.create(chinese_title=title,airing_period='2020-1'),personal_score='9.1',review='私人长评')

    def version(self):
        queue = WatchPlanQueue.objects.filter(user=self.user).first()
        return queue.revision if queue else 0

    def body(self,**fields):
        return {'action':'update','version':self.version(),'operation_id':str(uuid4()),'record_ids':[r.pk for r in self.records[:2]],'priority':1,**fields}

    def post(self,body):
        return self.client.post('/api/watch-plans/batch/',body,format='json')

    def test_batch_preserves_work_history_and_stable_relative_order(self):
        before=list(UserAnimeRecord.objects.values())
        result=self.post(self.body(planned_for='2031-02'))
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['contract_version'],'journal.watch-plan-batch.v1')
        self.assertEqual(list(UserAnimeRecord.objects.values()),before)
        self.assertEqual(WatchRound.objects.count(),0);self.assertEqual(WatchEvent.objects.count(),0)
        rows=list(WatchPlan.objects.filter(priority=1).order_by('position'))
        self.assertEqual([p.record_id for p in rows],[r.pk for r in self.records[:2]])
        self.assertEqual([p.planned_for for p in rows],['2031-02']*2)
        self.assertTrue(all(p.note=='保留独立备注' for p in rows))
        self.assertEqual(WatchPlan.objects.get(record=self.records[2]).priority,2)

    def test_unknown_ack_retry_returns_current_state_without_reapplying_old_values(self):
        body=self.body();self.assertEqual(self.post(body).status_code,200)
        self.assertEqual(self.post(self.body(record_ids=[self.records[0].pk],priority=3)).status_code,200)
        version=self.version();result=self.post(body)
        self.assertEqual(result.status_code,200);self.assertTrue(result.data['replayed'])
        self.assertEqual(self.version(),version)
        self.assertEqual(WatchPlan.objects.get(record=self.records[0]).priority,3)
        self.assertEqual(self.post({**body,'priority':2}).status_code,409)

    def test_validation_no_foreign_partial_archived_or_bulk_start(self):
        foreign=self.work('other',self.other)
        bad=[{'record_ids':None},{'record_ids':True},{'record_ids':[]},{'record_ids':[True]}, {'record_ids':[self.records[0].pk]*2},
             {'record_ids':[self.records[0].pk,foreign.pk]},{'record_ids':[self.records[0].pk,self.records[3].pk]},
             {'action':'start'},{'priority':True},{'planned_for':'2021-02-29'},{'note':'不允许批量覆盖'}, {'record_ids':[999999]}]
        before=list(WatchPlan.objects.values());version=self.version()
        for fields in bad:
            with self.subTest(fields=fields): self.assertIn(self.post(self.body(**fields)).status_code,(400,409))
        self.assertEqual(list(WatchPlan.objects.values()),before);self.assertEqual(self.version(),version)

    def test_remove_and_empty_date_are_explicit_and_conflicts_do_not_write(self):
        old=self.body();self.assertEqual(self.post(self.body(planned_for='2030')).status_code,200)
        self.assertEqual(self.post(old).status_code,409)
        self.assertEqual(self.post(self.body(planned_for='')).status_code,200)
        body=self.body(action='remove');body.pop('priority')
        self.assertEqual(self.post(body).status_code,200)
        self.assertEqual(WatchPlan.objects.filter(state='archived').count(),2)
        self.assertEqual(UserAnimeRecord.objects.count(),4)
        self.assertEqual(self.post(self.body()).status_code,409)

    def test_batch_transaction_rolls_back_if_a_plan_save_fails(self):
        original=WatchPlan.save;calls=0
        def fail(instance,*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2: raise RuntimeError('synthetic rollback')
            return original(instance,*args,**kwargs)
        version=self.version()
        with patch.object(WatchPlan,'save',fail),self.assertRaises(RuntimeError): self.post(self.body())
        self.assertEqual(self.version(),version);self.assertEqual(WatchPlan.objects.filter(priority=1).count(),0)

    def test_private_quick_filter_preview_pagination_and_summary_are_consistent(self):
        self.post(self.body())
        payload={'name':'优先待看','plan_state':'pending','plan_priority':1}
        result=self.client.post('/api/quick-filters/preview/',payload,format='json')
        self.assertEqual(result.status_code,200,result.data);self.assertEqual(result.data['count'],2)
        result=self.client.post('/api/quick-filters/',payload,format='json')
        self.assertEqual(result.status_code,201,result.data);self.assertEqual(result.data['plan_priority'],1)
        records=self.client.get('/api/records/',{'quick_filter':result.data['id'],'page_size':1})
        # Record API calls this parameter quick_filter; result is filtered before pagination.
        self.assertEqual(records.data['count'],2,records.data)
        self.assertEqual(records.data['results'][0]['watch_plan_summary'],{'state':'pending','priority':1})
        none=self.client.post('/api/quick-filters/preview/',{'name':'未安排','plan_state':'none'},format='json')
        self.assertEqual(none.data['count'],1)
        self.assertEqual(self.client.post('/api/quick-filters/',{**payload,'plan_state':'none'},format='json').status_code,400)

    def test_public_surfaces_never_offer_or_evaluate_private_plan_conditions(self):
        filt=UserQuickFilter.objects.create(user=self.user,name='秘密安排',plan_state='active')
        query=UserAnimeRecord.objects.filter(user=self.user)
        public=_apply_record_filters(query,SimpleNamespace(query_params={'quick_filter':str(filt.pk)}),self.user)
        self.assertEqual(public.count(),0)
        with override_settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            self.assertNotIn(filt.pk,[row['id'] for row in self.client.get('/api/showcase-filters/').data['results']])
        profile=self.user.profile;profile.is_public=True;profile.public_status='approved';profile.save()
        response=self.client.get(f'/api/public-journal/users/{self.user.username}/filter-options/')
        self.assertEqual(response.status_code,200,response.data)
        self.assertNotIn(filt.pk,[row['id'] for row in response.data['quick_filters']])
        self.assertTrue(all('plan_state' not in row for row in response.data['quick_filters']))
        public=self.client.get(f'/api/public-journal/users/{self.user.username}/records/')
        self.assertTrue(all('watch_plan_summary' not in r for r in public.data['results']))

    def test_batch_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertIn(self.post(self.body()).status_code,(401,403))

    def test_priority_group_counts_cover_full_filtered_result_not_current_page(self):
        self.post(self.body())
        response=self.client.get('/api/watch-plans/',{'priority':1,'page_size':1})
        self.assertEqual(response.data['priority_counts'],{'1':2,'2':0,'3':0})
        self.assertEqual(len(response.data['results']),1)


class WatchPlanB3ConcurrencyTests(TransactionTestCase):
    def race(self,same_operation):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user=get_user_model().objects.create_user(f'b3-race-{uuid4().hex}')
        records=[UserAnimeRecord.objects.create(user=user,anime=Anime.objects.create(chinese_title=f'race-{uuid4().hex}',airing_period='2020-1')) for _ in range(2)]
        client=APIClient();client.force_authenticate(user)
        for version,record in enumerate(records):
            self.assertEqual(client.post(f'/api/records/{record.pk}/watch-plan/',{'action':'enroll','version':version,'operation_id':str(uuid4())},format='json').status_code,200)
        body={'action':'update' if same_operation else 'remove','record_ids':[r.pk for r in records],'version':2,'operation_id':str(uuid4())}
        if same_operation: body['priority']=1
        barrier=Barrier(2)
        def write(index):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '4s'");cursor.execute("SET statement_timeout = '6s'")
                api=APIClient();api.force_authenticate(get_user_model().objects.get(pk=user.pk))
                barrier.wait(timeout=8)
                if same_operation or not index: return api.post('/api/watch-plans/batch/',body,format='json').status_code
                return api.post(f'/api/records/{records[1].pk}/watch-plan/',{'action':'start','version':2,'watch_version':0,'operation_id':str(uuid4())},format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(write,index) for index in range(2)]
            results=[future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(results),[200,200] if same_operation else [200,409])
        self.assertEqual(WatchPlanQueue.objects.get(user=user).revision,3)
        if same_operation:
            self.assertEqual(WatchPlan.objects.filter(queue__user=user,priority=1).count(),2)
            self.assertEqual(WatchEvent.objects.filter(record__user=user).count(),0)
        else:
            self.assertIn(WatchEvent.objects.filter(record__user=user).count(),(0,1))

    def test_same_batch_operation_is_applied_once_across_two_connections(self): self.race(True)
    def test_batch_remove_races_actual_start_without_deadlock_or_partial_write(self): self.race(False)
