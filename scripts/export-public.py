"""Export a reviewed source tree without dependencies, runtime data or Git history."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT_FILES = {'.gitattributes', '.env.example', '.gitignore', 'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md'}
TREES = {'backend', 'characters', 'desktop', 'scripts', 'tests', 'licenses'}
SKIP_DIRS = {'node_modules', '.venv', '__pycache__', '.pytest_cache', '.git', 'dist', 'build', 'cache', 'output', 'UserData', 'logs', '.runtime', 'PrivateArchive', 'voice_profiles', 'Build', 'Release', 'Cache', 'Smoke', 'TestData'}
BAD_SUFFIXES = {'.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.log', '.wav', '.mp3', '.flac', '.onnx', '.pt', '.pth', '.ckpt', '.safetensors', '.tmp', '.bak', '.lnk', '.ogg', '.m4a', '.aac', '.gguf', '.ggml', '.bin', '.key', '.pfx', '.p12'}
LICENSE_ARCHIVE = 'licenses/source/electron-44.4.5/ffmpeg-source-2b68d2babae73714846961fb0ee47e3b3d2e39a9.tar.gz'
LICENSE_ARCHIVE_SHA = '115841ee8a7a7afc1a037b031bf14a469b4e9b04f2d37c0ac515e1181574a5fc'
ALLOWED_BINARY = {'desktop/assets/companion-studio.ico', 'desktop/assets/companion-studio.png'}
PRIVATE_NAMES = {'api-keys.json', 'fish-key.json', 'secrets.json'}
SECRET_PATTERNS = [r'\bsk-[A-Za-z0-9_-]{24,}', r'\bgh[pousr]_[A-Za-z0-9]{30,}', r'\bgithub_pat_[A-Za-z0-9_]{50,}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', r'\bAKIA[A-Z0-9]{16}\b', r'\bxox[baprs]-[A-Za-z0-9-]{24,}']
PRIVATE_PATH = re.compile(r'(?i)(?:[a-z]:[\\/]+(?:users[\\/]+(?!public\b|default\b|test\b)[^\\/\s\"\']+|\u684c\u9762[\\/]+))')


def collect(root):
    for name in sorted(ROOT_FILES):
        file = root / name
        if not file.is_file() or file.is_symlink():
            raise ValueError(f'Missing regular root file: {name}')
        yield file
    def visit(folder):
        for file in sorted(folder.iterdir()):
            if file.name.casefold() in {name.casefold() for name in SKIP_DIRS}:
                # Upstream build definitions are retained as source/license evidence.
                upstream_build = file.name.casefold() == 'build' and file.is_relative_to(root / 'licenses/source')
                if not upstream_build:
                    continue
            if file.is_symlink() or file.is_junction():
                raise ValueError(f'Unexpected filesystem link: {file.relative_to(root)}')
            if file.is_dir():
                yield from visit(file)
            else:
                yield file
    for name in sorted(TREES):
        yield from visit(root / name)
    placeholder = root / 'data/.gitkeep'
    if placeholder.is_file():
        yield placeholder


def audit(root):
    files = list(collect(root)); findings = []
    for file in files:
        relative = file.relative_to(root).as_posix(); content = file.read_bytes()
        if relative.startswith('characters/') and relative != 'characters/assistant.json':
            findings.append({'file': relative, 'kind': 'unexpected-bundled-character'})
        lower_name = file.name.casefold()
        if file.suffix.lower() in BAD_SUFFIXES or lower_name in PRIVATE_NAMES or lower_name == '.env' or lower_name.startswith('.env.') and file.name != '.env.example':
            findings.append({'file': relative, 'kind': 'excluded-runtime-file'})
        if relative == LICENSE_ARCHIVE:
            if hashlib.sha256(content).hexdigest() != LICENSE_ARCHIVE_SHA:
                findings.append({'file': relative, 'kind': 'corresponding-source-hash-mismatch'})
            continue
        if content.startswith(b'\x89PNG') or content.startswith(b'\0\0\1\0'):
            if relative not in ALLOWED_BINARY:
                findings.append({'file': relative, 'kind': 'unexpected-image'})
            continue
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            findings.append({'file': relative, 'kind': 'unexpected-binary'})
            continue
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                findings.append({'file': relative, 'kind': 'credential-pattern'})
        if PRIVATE_PATH.search(text):
            findings.append({'file': relative, 'kind': 'private-path-or-identity'})
        if relative == '.env.example':
            for line in text.splitlines():
                if re.match(r'^[A-Z_]*(?:KEY|TOKEN|SECRET|PASSWORD)=.+', line):
                    findings.append({'file': relative, 'kind': 'nonempty-template-credential'})
    selected = {file.relative_to(root).as_posix(): file for file in files}
    manifest = json.loads((root / 'licenses/source-manifest.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        relative = row['path']
        if relative not in selected:
            findings.append({'file': relative, 'kind': 'required-third-party-file-not-exported'})
        elif hashlib.sha256(selected[relative].read_bytes()).hexdigest() != row['sha256']:
            findings.append({'file': relative, 'kind': 'third-party-source-hash-mismatch'})
    report = {'pass': not findings, 'scope': 'source files selected by explicit export policy', 'files': len(files), 'findings': findings,
              'excluded': ['Git history', 'virtual environments', 'node_modules', 'runtime profiles', 'chat databases', 'audio', 'user models', 'private archives', 'build caches'],
              'notes': ['Dummy keys in isolated tests are not account credentials.', 'This scan does not guarantee the absence of every possible software vulnerability.']}
    return files, report


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1]); parser.add_argument('--destination', type=Path); parser.add_argument('--report', type=Path)
    args = parser.parse_args(); root = args.source.resolve(); files, report = audit(root)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True); args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['pass']:
        print(json.dumps(report, ensure_ascii=False)); raise SystemExit(1)
    if args.destination:
        destination = args.destination.resolve()
        if destination.exists() or destination == root or destination.is_relative_to(root):
            raise ValueError('Export must use a new directory outside the source tree')
        destination.mkdir(parents=True)
        for file in files:
            target = destination / file.relative_to(root); target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(file, target)
        archive = destination.with_suffix('.zip')
        if archive.exists():
            raise ValueError('Source archive already exists')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as out:
            for file in files:
                relative = file.relative_to(root).as_posix(); out.write(destination / relative, 'Source/' + relative)
        with zipfile.ZipFile(archive) as check:
            assert len(check.infolist()) == len(files)
            for file in files:
                relative = file.relative_to(root).as_posix()
                assert hashlib.sha256(check.read('Source/' + relative)).digest() == hashlib.sha256((destination / relative).read_bytes()).digest()
        report['archive_verified'] = True
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
