"""Paginated JSON restore field comparisons, without changing restore semantics."""
import json
from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError
from . import curations
from .import_plans import CONTRACT, local_values, digest, json_value
from .models import UserAnimeRecord
from .serializers import JournalRecordImportSerializer
from .journal_restore import restore_preview


def restore_diff(request):
    if set(request.query_params)-{'page','kind'} or any(len(request.query_params.getlist(k))!=1 for k in request.query_params): raise ValidationError('恢复差异参数无效。')
    try: page=int(request.query_params.get('page',1));assert 1<=page<=10000
    except (ValueError,AssertionError): raise ValidationError('恢复差异分页无效。') from None
    kind=request.query_params.get('kind','all')
    if kind not in ('all','new','changed','unchanged','removed','needs_review'): raise ValidationError('恢复差异分类无效。')
    from .views import UserJournalImportView
    try: payload=UserJournalImportView()._read_payload(request)
    except ValueError as error: raise ValidationError(str(error)) from None
    if len(json.dumps(payload,ensure_ascii=False).encode())>UserJournalImportView.max_file_size: raise ValidationError('恢复文件不能超过 5MB。')
    records=payload.get('records') if isinstance(payload,dict) else payload
    if not isinstance(records,list) or len(records)>settings.USER_ANIME_RECORD_LIMIT: raise ValidationError('恢复作品数量或格式无效。')
    serializer=JournalRecordImportSerializer(data=records,many=True);serializer.is_valid(raise_exception=True);records=serializer.validated_data
    keys=[tuple(row['anime'][k] for k in ('chinese_title','japanese_title','airing_period')) for row in records]
    if len(keys)!=len(set(keys)): raise ValidationError('恢复文件包含重复作品。')
    rating=payload.get('rating_preferences') if isinstance(payload,dict) else None
    if isinstance(payload,dict) and 'rating_preferences' in payload:
        from .rating_config import validate_rating_preferences
        from django.core.exceptions import ValidationError as DjangoValidationError
        try: validate_rating_preferences(rating)
        except DjangoValidationError as error: raise ValidationError(error.messages) from None
    backup=curations.validate_backup(payload.get('curations',curations.MISSING) if isinstance(payload,dict) else curations.MISSING,records)
    with transaction.atomic():
        curations.lock_owner(request.user)
        existing=list(UserAnimeRecord.objects.filter(user=request.user).select_related('anime').prefetch_related('watch_rounds','watch_events','watch_plan'))
        impacts=restore_preview(request.user,records,rating,backup)
        matched=set();items=[]
        for index,(row,key) in enumerate(zip(records,keys)):
            candidates=[r for r in existing if tuple(r.effective_anime_metadata()[k] for k in ('chinese_title','japanese_title','airing_period'))==key]
            candidates=candidates or [r for r in existing if (r.anime.chinese_title,r.anime.japanese_title,r.anime.airing_period)==key]
            matched.update(r.pk for r in candidates)
            previous=candidates[0] if len(candidates)==1 else None
            before=local_values(previous) if previous else {}
            after={**{k:json_value(v) for k,v in row['anime'].items() if k in before or k in ('chinese_title','japanese_title','airing_period','studio','episodes','description','poster_url','bangumi_url','base_tags','baike_url','tag_colors')},
                   **{k:json_value(row[k]) for k in ('personal_score','watch_status','watched_eps','total_eps','review','custom_tags')}}
            if previous:
                from .tag_services import get_record_tag_colors
                before.update(custom_tags=previous.custom_tags,baike_url=previous.effective_anime_metadata()['baike_url'],tag_colors=get_record_tag_colors(previous))
            fields=[{'field':k,'before':before.get(k),'after':v,'changed':before.get(k)!=v,'effect':'replace'} for k,v in after.items()]
            for nested in ('watch_history','watch_plan'):
                current=None
                if previous:
                    if nested=='watch_history':
                        from .watch_history import export_history
                        current=export_history(previous)
                    else:
                        from .watch_plans import plan_data
                        current=plan_data(getattr(previous,'watch_plan',None))
                # Large ledgers are compared exactly, with bounded summaries in the response.
                def summary(value):
                    if value is None:return None
                    return {'digest':digest(value),**({'rounds':len(value['rounds']),'events':len(value['events'])} if nested=='watch_history' else {'state':value['state'],'purpose':value['purpose']})}
                fields.append({'field':nested,'supplied':nested in row,'changed':nested in row and digest(current)!=digest(row[nested]),
                    'before':summary(current),'after':summary(row[nested]) if nested in row else None,
                    'effect':('clear' if row[nested] is None else 'replace') if nested in row else 'preserve_if_uniquely_matched'})
            category='needs_review' if len(candidates)>1 else 'new' if previous is None else 'changed' if any(f.get('changed') for f in fields) else 'unchanged'
            items.append({'file_index':index,'record_id':previous.pk if previous else None,'title':row['anime']['chinese_title'],'kind':category,'fields':fields})
        items += [{'record_id':r.pk,'kind':'removed','title':r.effective_anime_metadata()['chinese_title'],'fields':[]} for r in existing if r.pk not in matched]
        from collections import Counter
        counts=dict(Counter(item['kind'] for item in items));selected=[item for item in items if kind=='all' or item['kind']==kind]
        return {'contract_version':CONTRACT,'source':'json','file_digest':digest(records),'restore':impacts,'counts':counts,'page':page,'page_size':20,'count':len(selected),'items':selected[(page-1)*20:page*20]}
