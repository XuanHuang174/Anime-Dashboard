"""Bounded, signed, database-enforced read-only collection image sources."""
import copy
import hashlib
import json
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException
from recommendations.quality_readonly import readonly_snapshot
from .curations import CurationList, entries, entry_data, list_data, MAX_ENTRIES, CurationConflict

SALT = 'journal.curation-export.v1'


class ExportUnavailable(APIException):
    status_code = 503
    default_detail = '片单暂时无法完整读取，未生成部分导出。'


def read_export(user, list_id, token=None, version=None, request=None):
    try:
        with readonly_snapshot():
            collection = get_object_or_404(CurationList.objects.select_related('library'), library__user=user, portable_id=list_id)
            rows = list(entries(collection)[:MAX_ENTRIES+1])
            if len(rows) > MAX_ENTRIES: raise ExportUnavailable()
            revision = collection.library.revision
            if version is not None and revision != version: raise CurationConflict()
            collection._members = rows
            payload = {'contract_version': 'journal.curation-export.v1', 'version': revision,
                       'collection': list_data(collection, request), 'entries': [entry_data(row, request) for row in rows]}
            stable = copy.deepcopy(payload)
            # Storage file names, not expiring signed URLs, identify artwork.
            covers = {}
            for row in rows:
                if row.record_id:
                    metadata = row.record.effective_anime_metadata()
                    image = metadata.get('poster_image')
                    covers[row.record_id] = str(image.name) if image else metadata.get('poster_url', '')
            for row in stable['entries']:
                if row['record']: row['record']['cover'] = covers[row['record']['id']]
            if stable['collection']['cover']:
                cover = stable['collection']['cover']; cover['cover'] = covers[cover['id']]
            digest = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
            identity = {'owner': user.pk, 'list_id': str(list_id), 'digest': digest}
            if token:
                try: previous = signing.loads(token, salt=SALT, max_age=900)
                except signing.BadSignature: raise CurationConflict('片单导出依据已过期，请重新预览。') from None
                if previous != identity: raise CurationConflict('片单或作品资料已变化，请重新预览。')
            payload.update(snapshot=token or signing.dumps(identity, salt=SALT), generated_at=timezone.now().isoformat())
            return payload
    except DatabaseError:
        raise ExportUnavailable() from None
