"""Conservative name-field parser, NOT a general wiki renderer or identity merge."""
import re
from .credits_contract import label, normalize_alias

ALIAS_VERSION = 'bangumi.credits-names.v1'
ALIAS_LABELS = frozenset(('英文名', '日文名', '纯假名', '罗马字', '第二中文名', '中文名', '别名', '简体中文名', '曾用名'))


def parse_names(name, infobox):
    names, issues = [label(name)], []
    label(normalize_alias(1, name)['search_key'])
    if not isinstance(infobox, str) or len(infobox) > 100000:
        return {'names': names, 'status': 'partial', 'issues': ['invalid_infobox']}
    if not infobox.strip():
        return {'names': names, 'status': 'observed', 'issues': []}
    lines = infobox.splitlines()
    if not lines[0].strip().startswith('{{Infobox ') or lines[-1].strip() != '}}':
        return {'names': names, 'status': 'partial', 'issues': ['unsupported_infobox_structure']}

    def add(value):
        value = value.strip()
        if not value:
            return
        try:
            if re.search(r'[{}\[\]<>|]', value):
                raise ValueError()
            value = label(value)
            # Normalized key also has a database bound after Unicode expansion.
            label(normalize_alias(1, value)['search_key'])
            if value not in names:
                names.append(value)
            if len(names) > 100:
                raise ValueError()
        except ValueError:
            issues.append('unsupported_name_value')

    index = 1
    while index < len(lines) - 1:
        match = re.fullmatch(r'\|(简体中文名|别名)\s*=\s*(.*)', lines[index].strip())
        index += 1
        if not match:
            continue
        value = match[2]
        if value != '{':
            add(value)
            continue
        closed = False
        while index < len(lines) - 1:
            line = lines[index].strip(); index += 1
            if line == '}':
                closed = True; break
            entry = re.fullmatch(r'\[([^|\[\]]+)\|([^\[\]]*)\]', line)
            if entry and entry[1] in ALIAS_LABELS:
                add(entry[2])
            elif line:
                issues.append('unsupported_alias_entry')
        if not closed:
            issues.append('unclosed_alias_list')
    if len(names) > 100:
        names = names[:100]; issues.append('alias_limit')
    return {'names': names, 'status': 'partial' if issues else 'observed', 'issues': sorted(set(issues))}
