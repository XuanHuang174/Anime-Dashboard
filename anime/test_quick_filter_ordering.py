from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from .models import UserQuickFilter, FeaturedQuickFilter, UserProfile


class QuickFilterOrderingTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('order-owner')
        self.other = get_user_model().objects.create_user('order-other')
        self.admin = get_user_model().objects.create_user('order-admin', is_superuser=True, is_staff=True)
        self.client.force_authenticate(self.owner)
        self.url = '/api/quick-filters/'

    def read(self, url=None):
        response = self.client.get((url or self.url)+'order/')
        self.assertEqual(response.status_code,200,response.data)
        return response.data

    def reorder(self, snapshot, ids=None, url=None):
        return self.client.post((url or self.url)+'order/', {'ids':ids if ids is not None else [row['id'] for row in snapshot['filters']][::-1], 'version':snapshot['version']}, format='json')

    def test_order_persists_without_changing_conditions_timestamps_or_other_owners(self):
        snapshot=self.read();before=list(UserQuickFilter.objects.values('id','name','tags','updated_at'))
        response=self.reorder(snapshot)
        self.assertEqual(response.status_code,200,response.data)
        ids=[row['id'] for row in response.data['filters']]
        self.assertEqual(ids,[row['id'] for row in snapshot['filters']][::-1])
        self.assertEqual([row['id'] for row in self.read()['filters']],ids)
        self.assertEqual(sorted(before,key=lambda row:row['id']),sorted(UserQuickFilter.objects.values('id','name','tags','updated_at'),key=lambda row:row['id']))
        self.assertIn('no-store',response['Cache-Control'])

    def test_private_public_and_showcase_lists_share_author_order(self):
        result=self.reorder(self.read()).data;ids=[row['id'] for row in result['filters']]
        private=self.client.get(self.url,{'page_size':100}).data
        self.assertEqual([row['id'] for row in private['results']],ids)
        profile=UserProfile.objects.get(user=self.owner);profile.public_status='approved';profile.is_public=True;profile.save()
        self.client.force_authenticate(None)
        public=self.client.get('/api/public-journal/users/order-owner/filter-options/').data
        self.assertEqual([row['id'] for row in public['quick_filters']],ids)
        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.owner.username):
            showcase=self.client.get('/api/showcase-filters/',{'page_size':100}).data
        self.assertEqual([row['id'] for row in showcase['results']],ids)

    def test_complete_order_includes_more_than_one_page(self):
        UserQuickFilter.objects.bulk_create([UserQuickFilter(user=self.owner,name=f'额外{i}',tags=['日常'],sort_order=5+i) for i in range(25)])
        page=self.client.get(self.url).data
        self.assertIsNotNone(page['next'])
        snapshot=self.read();self.assertEqual(len(snapshot['filters']),30)
        self.assertEqual(self.reorder(snapshot).status_code,200)

    def test_versions_detect_reorders_and_membership_changes_but_not_condition_edits(self):
        snapshot=self.read();first=snapshot['filters'][0]['id']
        self.assertEqual(self.client.patch(f'{self.url}{first}/',{'name':'重新命名','sort_order':999},format='json').status_code,200)
        self.assertEqual(self.read()['version'],snapshot['version'])
        changed=self.reorder(snapshot);self.assertEqual(changed.status_code,200)
        self.assertEqual(self.reorder(snapshot).status_code,409)
        snapshot=self.read()
        created=self.client.post(self.url,{'name':'末尾新建','tags':['治愈'],'sort_order':0},format='json')
        self.assertEqual(created.status_code,201,created.data)
        self.assertEqual(self.read()['filters'][-1]['id'],created.data['id'])
        self.assertEqual(self.reorder(snapshot).status_code,409)
        snapshot=self.read();self.client.delete(f"{self.url}{created.data['id']}/")
        self.assertEqual(self.reorder(snapshot).status_code,409)

    def test_rejects_duplicates_omissions_foreign_ids_and_malformed_bodies(self):
        snapshot=self.read();ids=[row['id'] for row in snapshot['filters']]
        foreign=UserQuickFilter.objects.filter(user=self.other).first().pk
        for candidate in (ids[:-1],ids[:-1]+[ids[0]],ids[:-1]+[foreign]):
            self.assertEqual(self.reorder(snapshot,candidate).status_code,400)
        for body in ({'ids':ids,'version':'wrong'},{'ids':ids,'version':snapshot['version'],'user':self.other.pk},{'ids':[True],'version':snapshot['version']}):
            self.assertEqual(self.client.post(self.url+'order/',body,format='json').status_code,400)
        self.assertEqual(self.read()['version'],snapshot['version'])
        self.client.force_authenticate(self.other)
        own=self.read();self.assertTrue(set(row['id'] for row in own['filters']).isdisjoint(ids))
        self.assertEqual(self.reorder(own,ids).status_code,400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url+'order/').status_code,(401,403))

    def test_failed_batch_rolls_back_positions_and_no_op_keeps_version(self):
        snapshot=self.read();ids=[row['id'] for row in snapshot['filters']]
        self.assertEqual(self.reorder(snapshot,ids).data['version'],snapshot['version'])
        with patch('django.db.models.query.QuerySet.bulk_update',side_effect=RuntimeError('synthetic order rollback')):
            with self.assertRaises(RuntimeError):self.reorder(snapshot)
        self.assertEqual(self.read()['version'],snapshot['version'])

    def test_global_order_is_separate_and_writable_only_by_administrators(self):
        for index in range(3):FeaturedQuickFilter.objects.create(name=f'全站{index}',tags=['日常'],sort_order=index)
        url='/api/featured-filters/'
        self.assertEqual(self.client.get(url+'order/').status_code,403)
        self.client.force_authenticate(self.admin)
        personal=self.read();global_order=self.read(url)
        result=self.reorder(global_order,url=url);self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(self.read()['version'],personal['version'])
        self.client.force_authenticate(None)
        public=self.client.get(url,{'page_size':100}).data
        self.assertEqual([row['id'] for row in public['results']],[row['id'] for row in result.data['filters']])
        self.assertIn(self.reorder(global_order,url=url).status_code,(401,403))

    def test_migration_preserves_created_time_id_order_within_each_scope(self):
        now=timezone.now();UserQuickFilter.objects.all().update(created_at=now,sort_order=99)
        for index in range(3):FeaturedQuickFilter.objects.create(name=f'迁移全站{index}',tags=['日常'],sort_order=99)
        migration=import_module('anime.migrations.0031_alter_featuredquickfilter_options_and_more')
        migration.preserve_existing_order(apps,SimpleNamespace(connection=connection))
        for user in (self.owner,self.other,self.admin):
            rows=list(UserQuickFilter.objects.filter(user=user))
            self.assertEqual([row.pk for row in rows],sorted(row.pk for row in rows))
            self.assertEqual([row.sort_order for row in rows],list(range(len(rows))))
        self.assertEqual(list(FeaturedQuickFilter.objects.values_list('sort_order',flat=True)),[0,1,2])


class QuickFilterOrderConcurrencyTests(TransactionTestCase):
    def run_parallel(self, actions):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(len(actions))
        def request(action):
            close_old_connections()
            try:
                user,path,body=action;client=APIClient();client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                barrier.wait(timeout=10)
                response=client.post(path,body,format='json')
                return response.status_code,response.data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:return list(pool.map(request,actions))

    def test_two_order_writers_use_version_conflict_instead_of_overwriting(self):
        if connection.vendor!='postgresql':self.skipTest('Requires PostgreSQL row locks')
        user=get_user_model().objects.create_user('order-race');client=APIClient();client.force_authenticate(user)
        snapshot=client.get('/api/quick-filters/order/').data;ids=[row['id'] for row in snapshot['filters']]
        responses=self.run_parallel([(user,'/api/quick-filters/order/',{'ids':ids[::-1],'version':snapshot['version']}),(user,'/api/quick-filters/order/',{'ids':ids[1:]+ids[:1],'version':snapshot['version']})])
        self.assertEqual(sorted(status for status,_ in responses),[200,409])

    def test_global_creates_share_a_lock_even_when_no_filters_exist(self):
        if connection.vendor!='postgresql':self.skipTest('Requires PostgreSQL row locks')
        admins=[get_user_model().objects.create_user(f'global-order-{index}',is_superuser=True,is_staff=True) for index in range(2)]
        responses=self.run_parallel([(user,'/api/featured-filters/',{'name':f'并发全站{index}','tags':['日常']}) for index,user in enumerate(admins)])
        self.assertEqual([status for status,_ in responses],[201,201])
        self.assertEqual(list(FeaturedQuickFilter.objects.values_list('sort_order',flat=True)),[0,1])
