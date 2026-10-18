"""Hash every program resource using Windows long-path-safe enumeration."""

import argparse
import hashlib
import json
import os
from pathlib import Path


def native(path):
    value = str(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return value


def inventory(root):
    original = Path(root).absolute()
    for ancestor in (original, *original.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError("Program inventory must not follow filesystem links")
    root = Path(native(original.resolve()))
    if not root.is_dir():
        raise ValueError("Program directory missing")
    pending, files, names = [root], {}, set()
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink() or path.is_junction():
                    raise ValueError("Program inventory contains a filesystem link")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    relative = path.relative_to(root).as_posix()
                    if relative.casefold() in names:
                        raise ValueError("Ambiguous case-insensitive program filename")
                    names.add(relative.casefold())
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    files[relative] = {
                        "bytes": entry.stat(follow_symlinks=False).st_size,
                        "sha256": digest,
                    }
    if not files:
        raise ValueError("Program inventory cannot be empty")
    return dict(sorted(files.items()))


def compare(expected, actual, *, allow_uninstaller=False):
    permitted = {"Uninstall CompanionStudio.exe"} if allow_uninstaller else set()
    missing = sorted(set(expected) - set(actual))
    changed = sorted(name for name in set(expected) & set(actual) if expected[name] != actual[name])
    extra = sorted(set(actual) - set(expected) - permitted)
    if missing or changed or extra:
        raise ValueError(
            f"Program resource mismatch: missing={missing}, changed={changed}, extra={extra}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--compare")
    parser.add_argument("--allow-uninstaller", action="store_true")
    parser.add_argument("--report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    files = inventory(args.root)
    if args.compare:
        compare(files, inventory(args.compare), allow_uninstaller=args.allow_uninstaller)
    report = {
        "status": "PASS",
        "files": files,
        "file_count": len(files),
        "inventory_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            report
            if args.json
            else {
                "status": "PASS",
                "file_count": len(files),
                "all_resources_compared": bool(args.compare),
            }
        )
    )


if __name__ == "__main__":
    main()
