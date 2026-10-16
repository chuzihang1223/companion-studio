"""只读审计公开源码、全部可达 Git 历史和可选目录版；不输出命中内容。

复用公开导出的模式与白名单，不执行 ASAR 脚本或冻结 Python 字节码。
归档读取失败会阻断审计，而不是将未扫描的内容当成安全通过。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from collections.abc import Callable
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import types
from typing import Any, Iterator

MAX_BLOB_BYTES = 64 * 1024 * 1024
RUNTIME_DIRS = {"userdata", "legacyarchive", "privatearchive", "voice_profiles", "logs"}
PRIVATE_NAMES = {
    "api-keys.json",
    "fish-key.json",
    "fish-settings.json",
    "api-presets.json",
    "auth.json",
    "credentials.json",
    "extension-credentials.json",
    "secrets.json",
    "agent-preferences.json",
    "trusted-actions.json",
    "personalization.json",
}
ASSIGNMENT = re.compile(
    r"(?im)[\"']?(?:api_key|access_token|auth_token|password|client_secret|fish_api_key|"
    r"openai_api_key|gh_token|github_token)[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_+/=-]{16,})[\"']"
)
DUMMY_TOKEN = re.compile(r"^(?:sk-(?:test|dummy|example)-|github_pat_test_)(?:0{24,}|x{24,})$")
# 只识别已核验的固定合成占位，且下方仍要求 tests/ 或 backend/tests/ 路径。
# Workflow 安全拒绝用例在本地历史中也保留这个明显虚构的字符串。
DUMMY_VALUES = {"isolated-test-key", "not-a-real-password"}


def load_export_policy(root: Path) -> Any:
    """读取当前公开导出规则，避免维护另一套漂移的密钥表达式。"""
    spec = importlib.util.spec_from_file_location(
        "release_export_policy", root / "scripts/export-public.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("Missing export policy")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Audit:
    """报告只保留规则和相对定位；路径本身敏感时使用摘要替代。"""

    def __init__(self, policy: Any) -> None:
        self.policy = policy
        self.findings: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = {}
        self._seen: set[tuple[str, str, str, str | None]] = set()

    def add(self, scope: str, location: str, kind: str, *, blob: str | None = None) -> None:
        if self.policy.PRIVATE_PATH.search(location):
            location = (
                "redacted-path-"
                + hashlib.sha256(location.encode("utf-8", errors="replace")).hexdigest()[:16]
            )
        key = (scope, location, kind, blob)
        if key in self._seen:
            return
        self._seen.add(key)
        finding: dict[str, Any] = {"scope": scope, "file": location, "kind": kind}
        if blob:
            finding["blob"] = blob
        self.findings.append(finding)

    def text(
        self,
        content: str,
        scope: str,
        location: str,
        *,
        blob: str | None = None,
        vendor: bool = False,
    ) -> None:
        """已知测试占位仅在测试路径豁免；其他测试文件照常检查真实模式。"""
        testing = location.startswith(("tests/", "backend/tests/"))
        for expression in self.policy.SECRET_PATTERNS:
            for hit in re.finditer(expression, content):
                if testing and DUMMY_TOKEN.fullmatch(hit.group()):
                    continue
                self.add(scope, location, "credential-pattern", blob=blob)
        for hit in ASSIGNMENT.finditer(content):
            if testing and (DUMMY_TOKEN.fullmatch(hit.group(1)) or hit.group(1) in DUMMY_VALUES):
                continue
            self.add(scope, location, "credential-assignment", blob=blob)
        # 第三方许可证/源码中出现上游开发路径，不代表本项目个人资料；
        # vendor 仍扫描密钥模式，许可内容必须另行通过清单校验。
        if not vendor and self.policy.PRIVATE_PATH.search(content):
            self.add(scope, location, "private-path-or-identity", blob=blob)

    def path(
        self,
        scope: str,
        location: str,
        *,
        blob: str | None = None,
        allow_native_binary: bool = False,
    ) -> None:
        parts = PurePosixPath(location.replace("\\", "/")).parts
        if any(part.casefold() in RUNTIME_DIRS for part in parts):
            self.add(scope, location, "runtime-profile-path", blob=blob)
        name = parts[-1].casefold() if parts else ""
        if (
            name in PRIVATE_NAMES
            or name == ".env"
            or name.startswith(".env.")
            and name != ".env.example"
        ):
            self.add(scope, location, "private-configuration-file", blob=blob)
        if Path(name).suffix in self.policy.BAD_SUFFIXES and not (
            allow_native_binary and Path(name).suffix == ".bin"
        ):
            self.add(scope, location, "excluded-runtime-file", blob=blob)


def decode_text(content: bytes) -> str | None:
    """只将无 NUL 的 UTF-8 内容视为文本；二进制另行分类，不伪报已解码。"""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return text if "\x00" not in text else None


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def regular_files(root: Path, on_directory: Callable[[Path], None] | None = None) -> Iterator[Path]:
    """不沿 symlink / Windows junction 读取根目录之外的文件。"""
    for child in sorted(root.iterdir()):
        if child.is_symlink() or child.is_junction():
            raise ValueError("Filesystem link is not allowed")
        if child.is_dir():
            if on_directory is not None:
                on_directory(child)
            yield from regular_files(child, on_directory)
        elif child.is_file():
            yield child


def license_integrity(
    root: Path, audit: Audit, scope: str, expected_manifest: Path | None = None
) -> None:
    """逐项检查 corresponding source/许可文件的路径、大小和 SHA256。"""
    manifest = json.loads((root / "licenses/source-manifest.json").read_text(encoding="utf-8"))
    if expected_manifest is not None and sha256(root / "licenses/source-manifest.json") != sha256(
        expected_manifest
    ):
        audit.add(scope, "licenses/source-manifest.json", "license-manifest-mismatch")
    checked = 0
    required = {"licenses/source-manifest.json"}
    for row in manifest["files"]:
        relative = row["path"]
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in relative
            or ":" in relative
            or not relative.startswith("licenses/")
        ):
            audit.add(scope, "licenses/source-manifest.json", "unsafe-manifest-path")
            continue
        target = root / relative
        required.add(relative)
        if any(
            part.is_symlink() or part.is_junction()
            for part in (target, *target.parents[: len(path.parts)])
        ):
            audit.add(scope, relative, "license-filesystem-link")
        elif not target.is_file():
            audit.add(scope, relative, "required-third-party-file-missing")
        elif target.stat().st_size != row["bytes"] or sha256(target) != row["sha256"]:
            audit.add(scope, relative, "third-party-source-hash-mismatch")
        else:
            checked += 1
    for file in regular_files(root / "licenses"):
        relative = file.relative_to(root).as_posix()
        if relative not in required:
            audit.add(scope, relative, "unlisted-third-party-file")
    audit.stats[scope + "_license_files"] = checked


def source_audit(root: Path, audit: Audit) -> None:
    """保留导出审计门，同时独立检查当前源码中无前缀的显式凭据赋值。"""
    files, report = audit.policy.audit(root)
    for finding in report["findings"]:
        # 同一密钥表达式在下方重新扫描，增加仅测试路径的明确占位例外；
        # 不修改导出工具，也不将含真实模式的全部测试文件加入白名单。
        if finding["kind"] == "credential-pattern":
            continue
        if finding["kind"] == "private-path-or-identity" and finding["file"].startswith(
            "licenses/"
        ):
            continue
        audit.add("source", finding["file"], finding["kind"])
    for file in files:
        relative = file.relative_to(root).as_posix()
        content = decode_text(file.read_bytes())
        if content is not None:
            audit.text(content, "source", relative, vendor=relative.startswith("licenses/"))
    audit.stats["source_files"] = len(files)
    license_integrity(root, audit, "source")


def git_read(root: Path, *arguments: str) -> bytes:
    """仅使用读取子命令，错误不回显 Git stderr 或本机绝对路径。"""
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Git history could not be read")
    return result.stdout


@contextmanager
def blob_reader(root: Path) -> Iterator[Any]:
    """复用只读 cat-file 管道；一次读取一条 blob，避免每文件启动 Git。"""
    process = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield process
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()


def history_audit(root: Path, audit: Audit) -> None:
    """读取所有 refs 及 HEAD 可达提交树，覆盖曾提交后已删除的文本 blob。"""
    top = Path(git_read(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if top != root:
        raise ValueError("Audit must target the repository root")
    commits = git_read(root, "rev-list", "--all", "HEAD").decode().splitlines()
    blobs: dict[str, set[str]] = {}
    for commit in commits:
        for record in git_read(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
            if not record:
                continue
            header, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode().split()
            relative = raw_path.decode("utf-8", errors="replace")
            if kind != "blob":
                audit.add("history", relative, "unscanned-git-submodule", blob=object_id)
                continue
            audit.path("history", relative, blob=object_id)
            if mode == "120000":
                audit.add("history", relative, "tracked-filesystem-link", blob=object_id)
            blobs.setdefault(object_id, set()).add(relative)
    text_blobs = binary_blobs = 0
    with blob_reader(root) as process:
        assert process.stdin is not None and process.stdout is not None
        for object_id, names in blobs.items():
            process.stdin.write((object_id + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().decode().split()
            if len(header) != 3 or header[1] != "blob":
                raise RuntimeError("Unexpected Git blob response")
            size = int(header[2])
            if size > MAX_BLOB_BYTES:
                for name in names:
                    audit.add("history", name, "blob-too-large-to-scan", blob=object_id)
                # 排空当前条目，确保后续 blob 的帧边界仍然正确。
                remaining = size
                while remaining:
                    chunk = process.stdout.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise RuntimeError("Truncated Git blob")
                    remaining -= len(chunk)
                process.stdout.read(1)
                continue
            content = process.stdout.read(size)
            if len(content) != size or process.stdout.read(1) != b"\n":
                raise RuntimeError("Truncated Git blob")
            text = decode_text(content)
            if text is None:
                binary_blobs += 1
                continue
            text_blobs += 1
            for name in sorted(names):
                audit.text(
                    text, "history", name, blob=object_id, vendor=name.startswith("licenses/")
                )
    audit.stats.update(
        history_commits=len(commits),
        history_unique_blobs=len(blobs),
        history_text_blobs=text_blobs,
        history_binary_blobs=binary_blobs,
    )


ASAR_READER = r"""
const crypto = require('node:crypto');
const path = require('node:path');
const { TextDecoder } = require('node:util');
const asar = require(process.argv[1]);
const archive = process.argv[2];
const rows = [];
for (const raw of asar.listPackage(archive)) {
  const name = raw.replace(/\\/g, '/').replace(/^\/+/, '');
  if (name.split('/').some(part => part === '..' || part === '.') || name.includes(':')) throw Error('unsafe member');
  const member = name.split('/').join(path.sep);
  const stat = asar.statFile(archive, member, false);
  if (stat.files) continue;
  if (stat.link) { rows.push({ name, link: true }); continue; }
  if (stat.unpacked) { rows.push({ name, unpacked: true }); continue; }
  if (stat.size > 67108864) { rows.push({ name, too_large: true }); continue; }
  const data = asar.extractFile(archive, member);
  let text = null;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(data); } catch {}
  if (text?.includes('\0')) text = null;
  rows.push({ name, size: data.length, sha256: crypto.createHash('sha256').update(data).digest('hex'),
              text });
}
process.stdout.write(JSON.stringify(rows));
"""


def approved_images(root: Path, audit: Audit) -> set[str]:
    """仅认可公开白名单图片及原创 SVG 的内容哈希。"""
    names = set(audit.policy.ALLOWED_BINARY) | {"desktop/assets/companion-studio.svg"}
    return {sha256(root / name) for name in names if (root / name).is_file()}


def image_file(name: str) -> bool:
    return Path(name).suffix.casefold() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".ico",
        ".svg",
    }


def asar_audit(root: Path, unpacked: Path, audit: Audit, images: set[str]) -> None:
    """通过已安装 @electron/asar 读取成员，不执行 JS 或提取到用户目录。"""
    library = root / "desktop/node_modules/@electron/asar/lib/asar.js"
    result = subprocess.run(
        ["node", "-e", ASAR_READER, str(library), str(unpacked / "resources/app.asar")],
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("ASAR members could not be inspected")
    entries = json.loads(result.stdout)
    for entry in entries:
        name = entry["name"]
        audit.path("asar", name)
        if entry.get("link") or entry.get("too_large"):
            audit.add("asar", name, "unreadable-or-linked-member")
        elif not entry.get("unpacked"):
            vendor = name.startswith("node_modules/")
            if image_file(name) and not vendor and entry["sha256"] not in images:
                audit.add("asar", name, "unexpected-image")
            if entry["text"] is not None:
                audit.text(entry["text"], "asar", name, vendor=vendor)
            elif not vendor and not image_file(name):
                audit.add("asar", name, "unexpected-binary-member")
    audit.stats["asar_members"] = len(entries)


def code_strings(code: types.CodeType) -> Iterator[str]:
    """读取常量和编译文件名，不调用任何冻结模块函数。"""
    yield code.co_filename

    def values(items: tuple[Any, ...] | frozenset[Any]) -> Iterator[str]:
        for value in items:
            if isinstance(value, str):
                yield value
            elif isinstance(value, bytes):
                text = decode_text(value)
                if text is not None:
                    yield text
            elif isinstance(value, types.CodeType):
                yield from code_strings(value)
            elif isinstance(value, (tuple, frozenset)):
                yield from values(value)

    yield from values(code.co_consts)


def frozen_audit(executable: Path, audit: Audit) -> None:
    """用同版本 Python/PyInstaller 读取入口和 app.* 字节码常量。"""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    app_modules = scripts = 0
    for name, row in archive.toc.items():
        if row[-1] == "z":
            pyz = archive.open_embedded_archive(name)
            for module in pyz.toc:
                if module != "app" and not module.startswith("app."):
                    continue
                code = pyz.extract(module)
                if not isinstance(code, types.CodeType):
                    raise ValueError("Unexpected application module payload")
                app_modules += 1
                for text in code_strings(code):
                    audit.text(text, "frozen-python", module)
        elif row[-1] == "s" and name == "run":
            code = marshal.loads(archive.extract(name))
            if not isinstance(code, types.CodeType):
                raise ValueError("Unexpected backend entry payload")
            scripts += 1
            for text in code_strings(code):
                audit.text(text, "frozen-python", "run")
    if not app_modules or not scripts:
        raise ValueError("Application bytecode or entry missing")
    audit.stats.update(frozen_app_modules=app_modules, frozen_entry_scripts=scripts)


def delivery_audit(root: Path, delivery: Path, audit: Audit) -> None:
    """检查新生成目录版，拒绝个人资料、非白名单图片和语音/模型资源。"""
    unpacked = delivery / "win-unpacked" if (delivery / "win-unpacked").is_dir() else delivery
    files = list(
        regular_files(
            unpacked,
            lambda directory: audit.path("delivery", directory.relative_to(unpacked).as_posix()),
        )
    )
    images = approved_images(root, audit)
    official = root / "desktop/node_modules/electron/dist"
    for file in files:
        name = file.relative_to(unpacked).as_posix()
        vendor = name.startswith("resources/licenses/")
        # ffmpeg corresponding source 是法律要求的归档，不是模型包。
        if vendor:
            text = decode_text(file.read_bytes())
            if text is not None:
                audit.text(text, "delivery", name, vendor=True)
            continue
        if name.startswith("resources/app.asar.unpacked/node_modules/"):
            vendor = True
        counterpart = official / name
        electron_vendor = counterpart.is_file() and sha256(counterpart) == sha256(file)
        audit.path("delivery", name, allow_native_binary=electron_vendor)
        if image_file(name) and not vendor and not electron_vendor and sha256(file) not in images:
            audit.add("delivery", name, "unexpected-image")
        if (
            name.startswith("resources/")
            and file.suffix.casefold() == ".bin"
            and not electron_vendor
        ):
            audit.add("delivery", name, "unexpected-model-or-data-binary")
        if file.name == "assistant.json" and "/characters/" in name:
            if file.read_bytes() != (root / "characters/assistant.json").read_bytes():
                audit.add("delivery", name, "bundled-character-mismatch")
        elif "/characters/" in name:
            audit.add("delivery", name, "unexpected-bundled-character")
        if not electron_vendor:
            text = decode_text(file.read_bytes())
            if text is not None:
                audit.text(text, "delivery", name, vendor=vendor)
    license_integrity(
        unpacked / "resources", audit, "delivery", root / "licenses/source-manifest.json"
    )
    asar_audit(root, unpacked, audit, images)
    frozen_audit(unpacked / "resources/companion-backend/companion-backend.exe", audit)
    audit.stats["delivery_files"] = len(files)


def run_audit(root: Path, delivery: Path | None = None, *, history: bool = True) -> dict[str, Any]:
    audit = Audit(load_export_policy(root))
    for scope, operation in (
        ("source", lambda: source_audit(root, audit)),
        ("history", lambda: history_audit(root, audit)),
    ):
        if scope == "history" and not history:
            continue
        try:
            operation()
        except Exception as error:
            # 不回显可能含敏感值的异常文本/子进程输出。
            audit.add(scope, "audit-input", "inspection-error-" + type(error).__name__)
    if delivery is not None:
        try:
            delivery_audit(root, delivery, audit)
        except Exception as error:
            audit.add("delivery", "audit-input", "inspection-error-" + type(error).__name__)
    return {
        "pass": not audit.findings,
        "findings": audit.findings,
        "stats": audit.stats,
        "scope": {
            "source": True,
            "all_reachable_git_history": history,
            "delivery": delivery is not None,
        },
        "limitations": [
            "Pattern scanning is not proof of absence of all credentials or vulnerabilities.",
            "Git history includes all refs and HEAD; unreachable/reflog-only objects are not scanned.",
            "Binary Git blobs are counted but not decoded as text; current license archives are hash checked.",
            "Frozen Python checks application modules and entry constants, not all vendor bytecode or native DLLs.",
            "ASAR is read without executing code; native binaries are not a malware/signature audit.",
            "The directory build is checked; NSIS setup execution and installed GUI require separate acceptance.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--delivery", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Only for exported source without .git; release gates keep history enabled",
    )
    args = parser.parse_args()
    report = run_audit(
        args.source.resolve(),
        args.delivery.resolve() if args.delivery else None,
        history=not args.no_history,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
