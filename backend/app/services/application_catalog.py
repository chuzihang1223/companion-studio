"""Read Electron's discovery cache without scanning disks or running programs."""
import json
from app.core.config import settings

def applications() -> list[dict]:
    try:
        rows = json.loads((settings.data_root / 'application-registry.json').read_text(encoding='utf-8'))['applications']
        return [r for r in rows if isinstance(r, dict) and r.get('installed') is True
                and isinstance(r.get('app_id'), str) and r['app_id']]
    except (OSError, ValueError, KeyError, TypeError):
        return []

def application_ids() -> set[str]:
    ids = {value for r in applications() for value in [r['app_id'], *r.get('legacy_ids', [])]}
    try:
        custom = json.loads((settings.data_root / 'known-applications.json').read_text(encoding='utf-8')).get('custom', {})
        ids.update(key for key, value in custom.items() if isinstance(key, str) and isinstance(value, dict))
    except (OSError, ValueError, TypeError):
        pass
    return ids


def resolvable_applications() -> list[dict]:
    rows = applications()
    builtins = {'notepad':'记事本', 'explorer':'文件资源管理器', 'calculator':'计算器', 'settings':'Windows 设置'}
    from app.services.application_alias import ApplicationAliasResolver
    for app_id, name in builtins.items():
        query = ApplicationAliasResolver.aliases({'name':name})
        if not any(query.intersection(ApplicationAliasResolver.aliases(row)) for row in rows):
            rows.append({'app_id':app_id, 'name':name, 'installed':True})
    return rows
