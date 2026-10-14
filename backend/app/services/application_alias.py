"""Match application names from the installed index, never turn names into commands."""

import difflib
import re
import unicodedata


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\b(?:v?\d+(?:\.\d+){1,4}|x64|x86|64-bit|32-bit|user)\b", "", value)
    return re.sub(r"[\W_]+", "", value)


class ApplicationAliasResolver:
    # Common abbreviations supplement metadata; the index remains the source of apps.
    COMMON = [
        ("qqmusic", "qq音乐"),
        ("neteasecloudmusic", "网易云音乐", "网易云"),
        ("visualstudiocode", "vscode", "vscodeuser"),
        ("英雄联盟", "lol", "leagueoflegends", "英雄联盟wegame版"),
        ("鸣潮", "wutheringwaves"),
        ("记事本", "notepad", "windowsnotepad"),
        ("计算器", "calculator"),
        ("文件资源管理器", "资源管理器", "explorer"),
        ("设置", "windows设置", "settings"),
    ]

    @classmethod
    def aliases(cls, row: dict) -> set[str]:
        values = [
            row.get("display_name") or row.get("name", ""),
            *row.get("aliases", []),
            *row.get("successful_aliases", []),
            str(row.get("shortcut_path") or "")
            .replace("\\", "/")
            .split("/")[-1]
            .removesuffix(".lnk"),
        ]
        normalized = {normalize_name(value) for value in values if isinstance(value, str) and value}
        for group in cls.COMMON:
            if normalized.intersection(group):
                normalized.update(group)
        return normalized

    def match(self, target: str, records: list[dict]) -> list[dict]:
        query, scored = normalize_name(target), []
        if not query:
            return []
        system_ids = {
            "windows.immersivecontrolpanel": {"设置", "windows设置", "settings", "windowssettings"},
            "microsoft.windows.immersivecontrolpanel": {
                "设置",
                "windows设置",
                "settings",
                "windowssettings",
            },
            "microsoft.windows.explorer": {"文件资源管理器", "资源管理器", "explorer"},
            "microsoft.windowscalculator": {"计算器", "calculator"},
            "microsoft.windowsnotepad": {"记事本", "notepad", "windowsnotepad"},
        }
        for row in records:
            if row.get("installed") is False:
                continue
            name = normalize_name(row.get("display_name") or row.get("name", ""))
            aliases = self.aliases(row)
            score = 1.0 if query in aliases else 0.0
            # Publisher matches are candidates, never silently select a vendor's arbitrary app.
            if not score and query == normalize_name(str(row.get("publisher") or "")):
                score = 0.79
            # Generic shortcuts called "设置" occur inside third-party apps.
            # Prefer the OS's registered identity for a direct system-app request.
            identity = str(row.get("app_user_model_id") or "").casefold()
            if any(
                query in names and identity.startswith(app_id)
                for app_id, names in system_ids.items()
            ):
                score = 1.15
            if not score:
                for alias in aliases:
                    if len(query) >= 3 and alias.startswith(query):
                        score = max(score, 0.91 - min(0.12, (len(alias) - len(query)) * 0.007))
                    elif min(len(query), len(alias)) >= 4:
                        score = max(
                            score, 0.88 * difflib.SequenceMatcher(None, query, alias).ratio()
                        )
            if score >= 0.76:
                scored.append(
                    {
                        "app_id": row["app_id"],
                        "name": row.get("display_name") or row.get("name"),
                        "score": round(score, 4),
                        "exact_name": query == name,
                    }
                )
        return sorted(
            scored, key=lambda item: (-item["score"], not item["exact_name"], item["name"])
        )[:8]

    @staticmethod
    def unique(matches: list[dict]) -> dict | None:
        if not matches:
            return None
        if len(matches) > 1 and matches[0]["score"] - matches[1]["score"] < 0.08:
            return None
        return matches[0] if matches[0]["score"] >= 0.8 else None
