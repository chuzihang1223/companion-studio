"""Local policy decisions; model parameters never select a permission mode."""
from app.services.desktop.preferences import read_preferences


class PermissionPolicyEngine:
    def decide(self, action: str, risk: str, *, task_allowed: bool = False) -> str:
        mode = read_preferences()['permission_mode']
        category = action.split(':', 1)[0]
        # Critical includes unknown executables and attempts at security/admin access.
        if risk not in {'low', 'medium', 'high'}:
            return 'deny'
        if risk == 'high':
            return 'ask'
        readonly = category in {'app.detect', 'app.status', 'system.get_status',
                                 'media.current', 'media.sessions', 'code.read_file',
                                 'filesystem.read', 'filesystem.list', 'workspace.read',
                                 'workspace.list', 'workspace.search'}
        if readonly:
            return 'allow'
        if task_allowed and risk not in {'high', 'critical'}:
            return 'allow'
        if mode == 'ask':
            return 'ask'
        if mode == 'smart':
            return 'ask' if risk == 'high' else 'allow'
        # High-risk execution already returned ask above in every mode.
        # No UAC interaction or system-permission bypass is implemented.
        if category in {'filesystem.delete', 'workspace.delete', 'app.close',
                         'system.install', 'system.modify'}:
            return 'ask'
        return 'allow'
