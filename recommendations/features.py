from .identity import normalized_title
from .taxonomy import canonical_tags


def feature_tags(raw, titles=()):
    values = raw.replace('，', ',').split(',') if isinstance(raw, str) else raw
    names = {normalized_title(title) for title in titles if title}
    return frozenset(tag for tag in canonical_tags(values) if normalized_title(tag) not in names)
