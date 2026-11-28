"""Durable F1 source plans. Scanning never writes journal or shared works.

Network calls run outside write transactions. Apply accepts decisions, never
client-supplied field values, and locks owner -> plan -> private records.
"""
from collections import Counter
from datetime import timedelta
from decimal import Decimal
import hashlib
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException

from .models import (Anime, UserAnimeRecord, JournalImportPlan, JournalImportItem,
                     JournalSyncBaseline, JournalImportOperation)
from .utils.scraper import MetadataScraper, MetadataScraperError

CONTRACT = 'journal.import-plan.v1'
SALT = CONTRACT
MAX_BYTES = 5 * 1024 * 1024
METADATA = ('chinese_title','japanese_title','airing_period','studio','episodes','description','poster_url','bangumi_url','base_tags')
PERSONAL = ('personal_score','watch_status','watched_eps','total_eps','review')
FIELDS = (*METADATA,*PERSONAL)


class PlanChanged(APIException):
    status_code = 409
    default_detail = '导入方案、私人资料或身份已变化，请重新读取并核对；本批未应用。'


class SourceUnavailable(APIException):
    status_code = 502
    default_detail = '来源暂时无法完整读取，请稍后重试；未修改手账。'


class Strict(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data)-set(self.fields):
            raise serializers.ValidationError({'non_field_errors':['请求包含不支持的字段。']})
        return super().to_internal_value(data)


class Create(Strict):
    uid = serializers.RegexField(r'^[A-Za-z0-9_-]{1,64}$')
    request_id = serializers.UUIDField()


class Scan(Strict):
    cursor = serializers.IntegerField(min_value=0, max_value=10000)


class Decision(Strict):
    item_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=('create','source','fill-empty','keep'))
    fields = serializers.ListField(child=serializers.ChoiceField(choices=FIELDS), max_length=len(FIELDS), default=list)
    clear_fields = serializers.ListField(child=serializers.ChoiceField(choices=FIELDS), max_length=len(FIELDS), default=list)

    def validate(self, data):
        if len(set(data['fields']))!=len(data['fields']) or len(set(data['clear_fields']))!=len(data['clear_fields']):
            raise serializers.ValidationError('字段不能重复。')
        if set(data['clear_fields'])-set(data['fields']): raise serializers.ValidationError('清空确认须属于所选字段。')
        if data['action'] in ('keep','create') and (data['fields'] or data['clear_fields']):
            raise serializers.ValidationError('保留或新增按完整预览方案确认，不接受字段覆盖。')
        if data['action'] in ('source','fill-empty') and not data['fields']:
            raise serializers.ValidationError('请选择需要处理的字段。')
        return data


class Apply(Strict):
    revision = serializers.IntegerField(min_value=0)
    token = serializers.CharField(max_length=2048)
    operation_id = serializers.UUIDField()
    decisions = Decision(many=True, min_length=1, max_length=50)

    def validate_decisions(self, values):
        if len({v['item_id'] for v in values})!=len(values): raise serializers.ValidationError('作品不能重复。')
        return values


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def json_value(value):
    return json.loads(json.dumps(value, default=str))


def limit(): return min(10000, settings.USER_ANIME_RECORD_LIMIT)
def blank(value): return value is None or value == '' or value == []


def owner_lock(user):
    # Same owner mutex as curations; FK checks must be able to finish while a
    # concurrent history/record writer holds a record this transaction needs.
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)


def active(plan):
    if timezone.now() >= plan.expires_at or plan.status=='invalid': raise PlanChanged('方案已过期或来源分页已变化，请重新读取。')


def source_page(uid, cursor):
    try: payload=MetadataScraper.get_user_anime_collections(uid,offset=cursor,limit=1)
    except (ValueError, MetadataScraperError): raise SourceUnavailable() from None
    if (not isinstance(payload,dict) or type(payload.get('total')) is not int or not 0<=payload['total']<=limit()
            or not isinstance(payload.get('data'),list) or len(payload['data'])>1): raise SourceUnavailable()
    if len(payload['data']) != min(1,max(0,payload['total']-cursor)): raise SourceUnavailable()
    return payload


def normalize_source(collection):
    if not isinstance(collection,dict): raise serializers.ValidationError('收藏条目格式错误。')
    subject=collection.get('subject') or {}
    if not isinstance(subject,dict): raise serializers.ValidationError('收藏作品结构无效。')
    sid=collection.get('subject_id',subject.get('id'))
    if type(sid) is not int or not 1<=sid<=2147483647: raise serializers.ValidationError('作品编号无效。')
    if 'id' in subject and (type(subject['id']) is not int or subject['id']!=sid): raise serializers.ValidationError('收藏作品编号不一致。')
    details=MetadataScraper.get_bangumi_details(sid)
    if not isinstance(details,dict) or details.get('bangumi_id',sid)!=sid: raise serializers.ValidationError('作品详情身份不一致。')
    values={}
    mapping={'chinese_title':details.get('chinese_title') or subject.get('name_cn') or subject.get('name'),
             'japanese_title':details.get('japanese_title',subject.get('name','')),
             'airing_period':details.get('airing_period') or MetadataScraper._to_airing_period(subject.get('date','')) or '未定档',
             'bangumi_url':f'https://bgm.tv/subject/{sid}'}
    for key in ('studio','episodes','description','poster_url'):
        if key in details: mapping[key]=details[key]
    # Validate without the legacy importer's truncation or tag dropping.
    from .serializers import JournalAnimeImportSerializer
    anime=JournalAnimeImportSerializer(data=mapping);anime.is_valid(raise_exception=True)
    values.update({key:json_value(anime.validated_data[key]) for key in mapping})
    if 'tags' in collection:
        tags=collection['tags']
    elif 'tags' in details:
        tags=details['tags'] if isinstance(details['tags'],list) else str(details['tags']).replace('，',',').split(',')
    else: tags=None
    if tags is not None:
        if not isinstance(tags,list) or any(not isinstance(tag,str) for tag in tags): raise serializers.ValidationError('标签必须是完整字符串列表。')
        validator=serializers.ListField(child=serializers.CharField(max_length=50,allow_blank=False),max_length=100)
        values['base_tags']=list(dict.fromkeys(validator.run_validation(tags)))
    if 'rate' in collection:
        raw=collection['rate']
        if isinstance(raw,bool): raise serializers.ValidationError('评分格式无效。')
        values['personal_score']=None if raw in (0,'0',None) else str(serializers.DecimalField(max_digits=3,decimal_places=1,min_value=Decimal('0'),max_value=Decimal('10')).run_validation(raw))
    if 'type' in collection:
        if type(collection['type']) is not int or collection['type'] not in (1,2,3,4,5): raise serializers.ValidationError('收藏状态无效。')
        values['watch_status']={1:'plan',2:'completed',3:'watching',4:'on_hold',5:'on_hold'}[collection['type']]
    for target, raw, present in (('watched_eps',collection.get('ep_status'),'ep_status' in collection),('total_eps',subject.get('eps'),'eps' in subject)):
        if present:
            if type(raw) is not int or not 0<=raw<=2147483647: raise serializers.ValidationError('集数无效，未截断来源。')
            values[target]=raw
    if 'comment' in collection: values['review']=serializers.CharField(allow_blank=True,max_length=20000,trim_whitespace=False).run_validation(collection['comment'])
    if any(len(str(values.get(k,'')))>20000 for k in ('description','review')): raise serializers.ValidationError('来源文字过长，未截断。')
    return {'subject_id':sid,'values':values,'collection_type':collection.get('type'),'fetched_at':timezone.now().isoformat()}


def local_values(record):
    m=record.effective_anime_metadata()
    result={key:json_value(m.get(key,'')) for key in METADATA if key!='bangumi_url'}
    result['bangumi_url']=record.private_bangumi_url
    image=m.get('poster_image')
    if image and image.name: result['poster_url']={'stored_image':image.name,'fallback_url':m['poster_url']}
    result.update({key:json_value(getattr(record,key)) for key in PERSONAL})
    return result


def context(user, lock=False):
    query=UserAnimeRecord.objects.filter(user=user).order_by('pk')
    if lock: query=query.select_for_update(of=('self',))
    records=list(query.select_related('anime')[:limit()+1])
    if len(records)>limit(): raise PlanChanged('私人记录超过处理上限。')
    from recommendations.resolution import resolved_records
    identities=resolved_records(user,max_rows=limit())
    return records,{row['pk']:row for row in identities}


def comparison(user, uid, source, records, identities):
    sid=source['subject_id'];remote=source['values']
    from recommendations.identity import normalized_title
    names={normalized_title(remote.get(k,'')) for k in ('chinese_title','japanese_title')}- {''}
    exact=[];suspect=[]
    for record in records:
        row=identities[record.pk]
        evidence={row.get(k) for k in ('verified_bangumi_id','anime__bgm_id','recommendation_identity__catalog_anime__bangumi_id','identity_override__target__bangumi_id')}-{None}
        if sid in evidence:
            valid=(row['identity_state'] in ('linked','not_in_catalog') and (row.get('resolved_bangumi_id')==sid or row['identity_state']=='not_in_catalog' and not row.get('identity_override__enabled')))
            (exact if valid else suspect).append(record)
        elif names.intersection({normalized_title(v) for v in (record.effective_anime_metadata()['chinese_title'],record.effective_anime_metadata()['japanese_title'])}): suspect.append(record)
    baseline=JournalSyncBaseline.objects.filter(user=user,source_uid=uid,subject_id=sid).first()
    legacy_shared = not exact and Anime.objects.filter(**{k:remote[k] for k in ('chinese_title','japanese_title','airing_period')}).exclude(bgm_id=sid).exists()
    if len(exact)>1 or suspect or legacy_shared or baseline and (not exact or baseline.record_id!=exact[0].pk):
        return None,'needs_review',{'reason':'作品身份、季度、私人关联或已移除的同步记录需要核对。','fields':[]},digest(['needs_review'])
    record=exact[0] if exact else None
    before=local_values(record) if record else {}
    fields=[{'field':key,'before':before.get(key),'after':value,'changed':before.get(key)!=value,'source_empty':blank(value),'default':'keep' if record else 'source'} for key,value in remote.items()]
    kind='new' if not record else 'changed' if any(f['changed'] for f in fields) else 'unchanged'
    base={'source':baseline.source_values,'local':baseline.local_values} if baseline else None
    report={'fields':fields,'baseline':base,'first_review':base is None}
    binding=digest([record.pk if record else None,before,record.watch_revision if record else None,
        record.custom_tags if record else None,identities.get(record.pk) if record else None,base])
    return record,kind,report,binding


def token(plan): return signing.dumps({'owner':plan.user_id,'plan':str(plan.pk),'revision':plan.revision},salt=SALT)


def plan_data(plan):
    counts=dict(Counter(plan.items.values_list('kind',flat=True)))
    return {'contract_version':CONTRACT,'id':str(plan.pk),'source':'bangumi','uid':plan.source_uid,'status':plan.status,
            'total':plan.total,'cursor':plan.cursor,'revision':plan.revision,'counts':counts,
            'applied':plan.items.filter(applied=True).count(),'created_at':plan.created_at.isoformat(),'expires_at':plan.expires_at.isoformat(),
            'token':token(plan) if plan.status=='ready' and plan.expires_at>timezone.now() else None}


def create_plan(user, raw):
    serializer=Create(data=raw);serializer.is_valid(raise_exception=True);data=serializer.validated_data
    existing=JournalImportPlan.objects.filter(user=user,request_id=data['request_id']).first()
    if existing:
        if existing.source_uid!=data['uid']: raise PlanChanged('同一请求编号不能更换来源。')
        return plan_data(existing)
    payload=source_page(data['uid'],0)
    with transaction.atomic():
        owner_lock(user)
        existing=JournalImportPlan.objects.filter(user=user,request_id=data['request_id']).first()
        if existing:
            if existing.source_uid!=data['uid']: raise PlanChanged()
            return plan_data(existing)
        if JournalImportPlan.objects.filter(user=user,created_at__gte=timezone.now()-timedelta(days=1)).count()>=20:
            raise serializers.ValidationError('当天导入方案数量已达上限，请使用已有方案。')
        plan=JournalImportPlan.objects.create(user=user,request_id=data['request_id'],source_uid=data['uid'],total=payload['total'],
            status='reading' if payload['total'] else 'ready',expires_at=timezone.now()+timedelta(hours=24))
        return plan_data(plan)


def scan_plan(user, plan_id, raw):
    serializer=Scan(data=raw);serializer.is_valid(raise_exception=True);cursor=serializer.validated_data['cursor']
    plan=get_object_or_404(JournalImportPlan,user=user,pk=plan_id);active(plan)
    if cursor<plan.cursor: return plan_data(plan)
    if cursor!=plan.cursor or plan.status!='reading': raise PlanChanged()
    payload=source_page(plan.source_uid,cursor)
    source=None;problem=None
    if payload['total']==plan.total:
        try: source=normalize_source(payload['data'][0])
        except (serializers.ValidationError,ValueError,TypeError,MetadataScraperError): problem='来源字段或详情读取失败，未截断、未写入手账。'
    with transaction.atomic():
        owner_lock(user);plan=JournalImportPlan.objects.select_for_update().get(pk=plan.pk);active(plan)
        if cursor<plan.cursor: return plan_data(plan)
        if cursor!=plan.cursor or plan.status!='reading': raise PlanChanged()
        if payload['total']!=plan.total:
            plan.status='invalid';plan.revision+=1;plan.save();return plan_data(plan)
        records,identities=context(user)
        if source and plan.items.filter(subject_id=source['subject_id']).exists():
            plan.status='invalid';plan.revision+=1;plan.save();return plan_data(plan)
        record,kind,report,binding=comparison(user,plan.source_uid,source,records,identities) if source else (None,'failed',{'reason':problem,'fields':[]},'')
        size=len(json.dumps([source,report],ensure_ascii=False,default=str).encode())
        if plan.payload_bytes+size>MAX_BYTES: raise serializers.ValidationError('导入方案超过 5MB 上限，请减少来源范围。')
        JournalImportItem.objects.create(plan=plan,position=cursor,subject_id=source['subject_id'] if source else None,record=record,
            kind=kind,source=source or {},comparison=json_value(report),binding=binding)
        plan.cursor+=1;plan.revision+=1;plan.payload_bytes+=size
        if plan.cursor==plan.total: plan.status='ready'
        plan.save();return plan_data(plan)


def read_plan(user, plan_id, query):
    if set(query)-{'page','kind'} or any(len(query.getlist(k))!=1 for k in query): raise serializers.ValidationError('方案分页无效。')
    try: page=int(query.get('page',1));assert 1<=page<=10000
    except (ValueError,AssertionError): raise serializers.ValidationError('方案分页无效。') from None
    kind=query.get('kind','all')
    if kind not in ('all','new','changed','unchanged','needs_review','failed'): raise serializers.ValidationError('差异分类无效。')
    plan=get_object_or_404(JournalImportPlan,user=user,pk=plan_id)
    rows=plan.items.all()
    if kind!='all': rows=rows.filter(kind=kind)
    return {**plan_data(plan),'page':page,'page_size':20,'count':rows.count(),'items':[
        {'id':str(row.pk),'position':row.position,'subject_id':row.subject_id,'record_id':row.record_id,'kind':row.kind,
         'source':row.source,'comparison':row.comparison,'applied':row.applied} for row in rows[(page-1)*20:page*20]]}


def apply_plan(user, plan_id, raw):
    serializer=Apply(data=raw);serializer.is_valid(raise_exception=True);data=serializer.validated_data
    request_hash=digest(data)
    with transaction.atomic():
        owner_lock(user);plan=get_object_or_404(JournalImportPlan.objects.select_for_update(),user=user,pk=plan_id)
        previous=plan.operations.filter(operation_id=data['operation_id']).first()
        if previous:
            if previous.request_hash!=request_hash: raise PlanChanged('同一操作编号不能复用为其他取舍。')
            return {**previous.receipt,'replayed':True}
        active(plan)
        try: proof=signing.loads(data['token'],salt=SALT,max_age=900)
        except signing.BadSignature: raise PlanChanged('确认依据已过期，请重新读取方案。') from None
        if plan.status!='ready' or data['revision']!=plan.revision or proof!={'owner':user.pk,'plan':str(plan.pk),'revision':plan.revision}: raise PlanChanged()
        records,identities=context(user,lock=True)
        items={row.pk:row for row in plan.items.select_for_update().filter(pk__in=[d['item_id'] for d in data['decisions']])}
        if len(items)!=len(data['decisions']): raise serializers.ValidationError('所选作品不属于本人的该方案。')
        outcomes=[]
        for decision in data['decisions']:
            item=items[decision['item_id']]
            if item.applied or item.kind in ('needs_review','failed'): raise PlanChanged('该作品已处理或需先核对来源身份。')
            record,kind,report,binding=comparison(user,plan.source_uid,item.source,records,identities)
            if binding!=item.binding or kind!=item.kind: raise PlanChanged()
            action=decision['action'];remote=item.source['values'];before=local_values(record) if record else {}
            if (record is None)!=(action=='create'): raise serializers.ValidationError('新增与更新取舍不匹配。')
            if set(decision['fields'])-set(remote): raise serializers.ValidationError('不能应用来源未提供的字段。')
            selected=remote if action=='create' else {k:remote[k] for k in decision['fields'] if action=='source' or action=='fill-empty' and blank(before[k]) and not blank(remote[k])}
            if action=='source' and any(blank(v) and k not in decision['clear_fields'] for k,v in selected.items()):
                raise serializers.ValidationError('采用来源空值需要逐字段明确确认清空。')
            merged={**before,**selected}
            if merged.get('total_eps',0) and merged.get('watched_eps',0)>merged['total_eps']: raise serializers.ValidationError('已看集数超过总集数，请核对所选进度字段；未自动截断。')
            from .watch_history import watch_observation
            if action=='create':
                if UserAnimeRecord.objects.filter(user=user).count()>=settings.USER_ANIME_RECORD_LIMIT: raise serializers.ValidationError('私人手账容量已满。')
                # Create missing shared identity stubs only; never update existing shared metadata.
                anime,_=Anime.objects.get_or_create(bgm_id=item.subject_id,defaults={**{k:remote[k] for k in METADATA if k in remote and k!='bangumi_url'},'is_active':False,'hidden_reason':Anime.HiddenReason.PRIVATE_ONLY})
                if UserAnimeRecord.objects.filter(user=user,anime=anime).exists(): raise PlanChanged()
                record=UserAnimeRecord(user=user,anime=anime,metadata_initialized=True,verified_bangumi_id=item.subject_id)
            if selected:
                if not record.metadata_initialized:
                    record.initialize_private_metadata()
                for key,value in selected.items():
                    field='private_'+key if key in METADATA else key
                    setattr(record,field,value)
                    if key=='poster_url': record.private_poster_image=None
                with watch_observation('collection_import'): record.save()
                if action=='create':
                    from .tag_services import initialize_record_tag_colors
                    initialize_record_tag_colors(record=record,user=user)
                    from .showcase_services import publish_new_showcase_record
                    publish_new_showcase_record(record=record,created=True)
            JournalSyncBaseline.objects.update_or_create(user=user,source_uid=plan.source_uid,subject_id=item.subject_id,
                defaults={'record':record,'source_values':remote,'local_values':{k:local_values(record)[k] for k in remote}})
            item.record=record;item.applied=True;item.save(update_fields=['record','applied'])
            outcomes.append({'item_id':str(item.pk),'record_id':record.pk,'action':action,'fields':list(selected)})
        plan.revision+=1;plan.save(update_fields=['revision'])
        receipt={'contract_version':CONTRACT,'plan_id':str(plan.pk),'revision':plan.revision,'results':outcomes,'replayed':False}
        JournalImportOperation.objects.create(plan=plan,operation_id=data['operation_id'],request_hash=request_hash,receipt=receipt)
        return receipt
