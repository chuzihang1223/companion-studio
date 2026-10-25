"""Collect public dependency notices; never copy an environment or its metadata.

Run with the same virtual environment and installed npm packages used to build.
Only package identities, upstream URLs, license texts and certifi's public source
are copied. Absolute installation paths and wheel cache metadata are not emitted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata as metadata
import json
import re
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from urllib.request import Request, urlopen

from packaging.requirements import Requirement


LICENSE_NAMES = ("license", "licence", "copying", "notice", "authors")
SPDX_FALLBACK = {
    "mcp": "MIT",
    "mcp-types": "MIT",
    "pyyaml": "MIT",
    # Inventory the complete wheel's notices. The MCP runtime uses win32 APIs;
    # adodbapi and Pythonwin are not deliberately imported by Studio.
    "pywin32": "LicenseRef-PyWin32 AND PSF-2.0 AND MIT AND LGPL-2.1-or-later",
    "altgraph": "MIT",
    "certifi": "MPL-2.0",
    "colorama": "BSD-3-Clause",
    "fish-audio-sdk": "Apache-2.0",
    "h11": "MIT",
    "httpx": "BSD-3-Clause",
    "httpx-ws": "MIT",
    "pefile": "MIT",
    "pip": "MIT",
    "pluggy": "MIT",
    "pyinstaller": "GPL-2.0-or-later WITH Bootloader-exception",
    "pyinstaller-hooks-contrib": "GPL-2.0-or-later AND Apache-2.0",
    "python-docx": "MIT",
    "python-dotenv": "BSD-3-Clause",
    "pywin32-ctypes": "BSD-3-Clause",
    "sqlalchemy": "MIT",
    # tzdata 2026.5 has legacy License metadata and includes both Apache notices.
    "tzdata": "Apache-2.0",
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(root: Path, relative: str, data: bytes) -> dict:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": relative, "sha256": sha(data), "bytes": len(data)}


def fetch(url: str) -> bytes:
    # Public upstream source/notice URLs only; no user headers or credentials.
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "CompanionStudio-LicenseCollector/1"}),
                timeout=45,
            ) as response:
                return response.read()
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.5)
    raise RuntimeError(f"Could not retrieve public upstream notice: {url}") from last_error


def public_copy(root: Path, relative: str, url: str, network: bool) -> dict:
    target = root / relative
    if network and not target.is_file():
        return save(root, relative, fetch(url))
    if not target.is_file():
        raise RuntimeError(f"Required upstream notice missing: {relative}")
    return {"path": relative, "sha256": sha(target.read_bytes()), "bytes": target.stat().st_size}


def native_components(root: Path, network: bool) -> list[dict]:
    import ssl
    import sqlite3
    import zlib
    from xml.parsers import expat

    openssl_version = ssl.OPENSSL_VERSION.split()[1]
    python_version = ".".join(map(str, sys.version_info[:3]))
    components = []
    data = [
        (
            "Expat",
            expat.EXPAT_VERSION.removeprefix("expat_"),
            "MIT",
            "https://github.com/libexpat/libexpat/tree/R_"
            + expat.EXPAT_VERSION.removeprefix("expat_").replace(".", "_"),
            "https://raw.githubusercontent.com/libexpat/libexpat/R_"
            + expat.EXPAT_VERSION.removeprefix("expat_").replace(".", "_")
            + "/expat/COPYING",
        ),
        (
            "OpenSSL",
            openssl_version,
            "Apache-2.0",
            f"https://github.com/openssl/openssl/tree/openssl-{openssl_version}",
            f"https://raw.githubusercontent.com/openssl/openssl/openssl-{openssl_version}/LICENSE.txt",
        ),
        (
            "zlib",
            zlib.ZLIB_RUNTIME_VERSION,
            "Zlib",
            f"https://github.com/madler/zlib/tree/v{zlib.ZLIB_RUNTIME_VERSION}",
            f"https://raw.githubusercontent.com/madler/zlib/v{zlib.ZLIB_RUNTIME_VERSION}/LICENSE",
        ),
    ]
    for name, version, expression, upstream_url, license_url in data:
        relative = f"licenses/native/{canonical(name)}-{version}/LICENSE"
        components.append(
            {
                "name": name,
                "version": version,
                "ecosystem": "native",
                "scope": "Python runtime native library",
                "license": expression,
                "upstream": [upstream_url],
                "files": [public_copy(root, relative, license_url, network)],
            }
        )
    # PyJWT[crypto] brings a separate OpenSSL linked into the cryptography wheel;
    # the interpreter's ssl.OPENSSL_VERSION does not describe that binary.
    from cryptography.hazmat.bindings.openssl.binding import Binding

    binding = Binding()
    crypto_version = binding.ffi.string(binding.lib.OpenSSL_version(0)).decode("ascii").split()[1]
    components.append(
        {
            "name": "OpenSSL (cryptography wheel)",
            "version": crypto_version,
            "ecosystem": "native",
            "scope": "cryptography wheel statically linked library",
            "license": "Apache-2.0",
            "upstream": [f"https://github.com/openssl/openssl/tree/openssl-{crypto_version}"],
            "files": [
                public_copy(
                    root,
                    f"licenses/native/cryptography-openssl-{crypto_version}/LICENSE.txt",
                    f"https://raw.githubusercontent.com/openssl/openssl/openssl-{crypto_version}/LICENSE.txt",
                    network,
                )
            ],
        }
    )
    # The exact Windows dependency pins used by this Python release are public,
    # and add context to the runtime's original LICENSE.txt and acknowledgements.
    pins_url = (
        f"https://raw.githubusercontent.com/python/cpython/v{python_version}/PCbuild/python.props"
    )
    pins_relative = f"licenses/python-runtime-{python_version}/PCbuild-python.props"
    public_copy(root, pins_relative, pins_url, network)
    pins = (root / pins_relative).read_text(encoding="utf-8")
    # These Windows libraries are separately versioned even though the complete
    # original notices are already included in the PSF runtime's LICENSE.txt.
    for name, element, prefix, expression in (
        ("libffi (CPython)", "libffiDir", "libffi-", "MIT"),
        ("bzip2 (CPython)", "bz2Dir", "bzip2-", "bzip2-1.0.6"),
    ):
        match = re.search(
            r"<" + element + r"\b[^>]*>[^<]*" + prefix + r"([0-9.]+)\\</" + element + ">", pins
        )
        if not match:
            raise RuntimeError("Review the CPython Windows dependency pin: " + name)
        components.append(
            {
                "name": name,
                "version": match.group(1),
                "ecosystem": "native",
                "scope": "Python runtime native library",
                "license": expression,
                "upstream": [pins_url],
                "files": [
                    save(
                        root,
                        f"licenses/native/{canonical(name)}-{match.group(1)}/LICENSE.txt",
                        (Path(sys.base_prefix) / "LICENSE.txt").read_bytes(),
                    )
                ],
            }
        )
    xz_match = re.search(r"<lzmaDir\b[^>]*>[^<]*xz-([0-9.]+)\\</lzmaDir>", pins)
    if not xz_match:
        raise RuntimeError("Review the CPython liblzma dependency version")
    xz_version = xz_match.group(1)
    xz_source = f"https://github.com/python/cpython-source-deps/tree/xz-{xz_version}"
    xz_copying = (
        f"https://raw.githubusercontent.com/python/cpython-source-deps/xz-{xz_version}/COPYING"
    )
    components.append(
        {
            "name": "liblzma (CPython)",
            "version": xz_version,
            "ecosystem": "native",
            "scope": "Python runtime statically linked library",
            "license": "LicenseRef-XZ-Public-Domain",
            "upstream": [xz_source],
            "notes": "The liblzma library is public domain; GPL build scripts and tools are not linked into it.",
            "files": [
                public_copy(
                    root,
                    f"licenses/native/cpython-liblzma-{xz_version}/COPYING",
                    xz_copying,
                    network,
                )
            ],
        }
    )
    components.append(
        {
            "name": "SQLite",
            "version": sqlite3.sqlite_version,
            "ecosystem": "native",
            "scope": "Python runtime native library",
            "license": "blessing",
            "upstream": ["https://www.sqlite.org/copyright.html"],
            "files": [
                public_copy(
                    root,
                    f"licenses/native/sqlite-{sqlite3.sqlite_version}/copyright.html",
                    "https://www.sqlite.org/copyright.html",
                    network,
                )
            ],
        }
    )
    pillow = metadata.distribution("pillow")
    license_relative = f"licenses/python/pillow-{pillow.version}/licenses/LICENSE"
    license_file = root / license_relative
    body = license_file.read_text(encoding="utf-8")
    expressions = {
        "brotli": "MIT",
        "freetype": "FTL",
        "harfbuzz": "MIT",
        "lcms2": "MIT",
        "libavif": "BSD-2-Clause",
        "libjpeg-turbo": "IJG AND BSD-3-Clause AND Zlib",
        "libpng": "libpng-2.0",
        "libwebp": "BSD-3-Clause",
        "openjpeg": "BSD-2-Clause",
        "tiff": "libtiff",
        "xz": "0BSD AND LGPL-2.1-or-later AND GPL-2.0-or-later",
        "zlib-ng": "Zlib",
    }
    for name, version in re.findall(r"^===== ([A-Za-z0-9-]+)-(\d[^\s]+) =====$", body, re.M):
        expression = expressions.get(name)
        if expression is None:
            raise RuntimeError(f"Review Pillow native dependency notice: {name}")
        # This inventory describes the complete license-bearing wheel; native
        # libraries may be statically included in Pillow extensions. XZ's
        # complete source includes build tools under different terms; the
        # linked liblzma component itself is covered by the notice's 0BSD terms.
        if name == "xz":
            expression = "0BSD"
        components.append(
            {
                "name": name,
                "version": version,
                "ecosystem": "native",
                "scope": "Pillow wheel native component",
                "license": expression,
                "upstream": ["https://github.com/python-pillow/Pillow"],
                "files": [
                    {
                        "path": license_relative,
                        "sha256": sha(license_file.read_bytes()),
                        "bytes": license_file.stat().st_size,
                    }
                ],
            }
        )
    return components


def upstream(dist: metadata.Distribution) -> list[str]:
    values = []
    for field in dist.metadata.get_all("Project-URL") or []:
        label, _, url = field.partition(",")
        if label.strip().lower() in {
            "source",
            "source code",
            "repository",
            "home",
            "homepage",
            "documentation",
        }:
            if url.strip().startswith("https://"):
                values.append(url.strip())
    home = dist.metadata.get("Home-page", "")
    if home.startswith("https://") and home not in values:
        values.append(home)
    if not values:
        # Some wheels (e.g. pluggy 1.6) omit project URLs entirely. Keep a
        # version-specific distribution origin, not an invented repository.
        name = dist.metadata.get("Name")
        if not name:
            raise RuntimeError("Distribution identity missing for upstream provenance")
        values.append(
            f"https://pypi.org/project/{quote(name, safe='')}/{quote(dist.version, safe='')}/"
        )
    return values


def notice_relative_paths(values: list[str]) -> dict[str, str]:
    paths = {value: PurePosixPath(value) for value in values}
    if any(path.is_absolute() or ".." in path.parts for path in paths.values()):
        raise RuntimeError("Redistribution notice must have a package-relative path")
    containers = {path.parts[0] for path in paths.values() if len(path.parts) > 1}
    # Multi-package wheels such as pywin32 have distinct adodbapi/license.txt,
    # pythonwin/License.txt and win32com/License.txt. Dropping their container
    # overwrites different grants on Windows' case-insensitive filesystem.
    return {
        value: value if len(containers) > 1 or len(path.parts) == 1 else "/".join(path.parts[1:])
        for value, path in paths.items()
    }


def python_packages(root: Path) -> list[dict]:
    declared = []
    for line in (root / "backend/requirements.txt").read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            declared.append(Requirement(line))
    runtime = [str(r) for r in declared if canonical(r.name) != "pytest"]
    development = [str(r) for r in declared if canonical(r.name) == "pytest"] + [
        "pyinstaller",
        "pyinstaller-hooks-contrib",
    ]
    found: dict[str, str] = {}
    visited = set()

    def closure(names: list[str], scope: str) -> None:
        queue = list(names)
        while queue:
            request = Requirement(queue.pop())
            name = canonical(request.name)
            marker_key = (name, scope, tuple(sorted(request.extras)))
            if marker_key in visited:
                continue
            visited.add(marker_key)
            dist = metadata.distribution(name)
            if found.get(name) != "runtime":
                found[name] = scope
            for value in dist.requires or []:
                requirement = Requirement(value)
                if requirement.marker is None or any(
                    requirement.marker.evaluate({"extra": extra}) for extra in {"", *request.extras}
                ):
                    queue.append(str(requirement))

    closure(runtime, "runtime")
    closure(development, "build/test")
    results = []
    for name, scope in sorted(found.items()):
        dist = metadata.distribution(name)
        actual_name = dist.metadata["Name"]
        folder = f"licenses/python/{canonical(actual_name)}-{dist.version}"
        copies = []
        seen = set()
        notices = []
        for installed in dist.files or []:
            value = str(installed).replace("\\", "/")
            filename = PurePosixPath(value).name
            if not any(token in filename.lower() for token in LICENSE_NAMES):
                continue
            notices.append((installed, value))
        relative_paths = notice_relative_paths([value for _, value in notices])
        for installed, value in notices:
            if value in seen:
                continue
            seen.add(value)
            source = Path(dist.locate_file(installed))
            if not source.is_file():
                continue
            # Preserve vendor notice subdirectories; never copy RECORD/METADATA,
            # direct_url.json, installation logs or absolute package paths.
            relative = relative_paths[value]
            copies.append(save(root, f"{folder}/{relative}", source.read_bytes()))
        if not copies:
            raise RuntimeError(f"Missing redistribution notice: {actual_name} {dist.version}")
        expression = dist.metadata.get("License-Expression") or SPDX_FALLBACK.get(name)
        if not expression:
            raise RuntimeError(f"Review SPDX expression for: {actual_name} {dist.version}")
        results.append(
            {
                "name": actual_name,
                "version": dist.version,
                "ecosystem": "PyPI",
                "scope": scope,
                "license": expression,
                "upstream": upstream(dist),
                "files": copies,
            }
        )
    certifi = metadata.distribution("certifi")
    folder = f"licenses/source/certifi-{certifi.version}"
    copies = []
    for installed in certifi.files or []:
        value = str(installed).replace("\\", "/")
        if value.startswith("certifi/") and (value.endswith(".py") or value.endswith(".pem")):
            copies.append(
                save(root, f"{folder}/{value}", Path(certifi.locate_file(installed)).read_bytes())
            )
    if not copies:
        raise RuntimeError("certifi corresponding source was not found")
    results.append(
        {
            "name": "certifi-corresponding-source",
            "version": certifi.version,
            "ecosystem": "source",
            "scope": "MPL corresponding source",
            "license": "MPL-2.0",
            "upstream": upstream(certifi),
            "files": copies,
        }
    )
    return results


def npm_packages(root: Path, modules: Path) -> list[dict]:
    results = []
    for name in ("react", "react-dom", "scheduler", "electron"):
        source = modules / name
        package = json.loads((source / "package.json").read_text(encoding="utf-8"))
        copies = []
        for file in source.iterdir():
            if file.is_file() and any(word in file.name.lower() for word in LICENSE_NAMES):
                copies.append(
                    save(
                        root,
                        f"licenses/npm/{name}-{package['version']}/{file.name}",
                        file.read_bytes(),
                    )
                )
        if name == "electron":
            for file in ("LICENSE", "LICENSES.chromium.html"):
                copies.append(
                    save(
                        root,
                        f"licenses/electron-{package['version']}/{file}",
                        (source / "dist" / file).read_bytes(),
                    )
                )
        if not copies:
            raise RuntimeError(f"Missing npm notice: {name}")
        repository = package.get("repository", {})
        url = repository.get("url", "") if isinstance(repository, dict) else str(repository)
        results.append(
            {
                "name": name,
                "version": package["version"],
                "ecosystem": "npm",
                "scope": "runtime",
                "license": package.get("license", "SEE LICENSE"),
                "upstream": [url.replace("git+", "")],
                "files": copies,
            }
        )
    return results


def standard_runtime(root: Path, network: bool) -> dict:
    version = ".".join(map(str, sys.version_info[:3]))
    source = Path(sys.base_prefix) / "LICENSE.txt"
    if not source.is_file():
        raise RuntimeError("The Python distribution must provide LICENSE.txt")
    copies = [save(root, f"licenses/python-runtime-{version}/LICENSE.txt", source.read_bytes())]
    if network:
        copies.append(
            save(
                root,
                f"licenses/python-runtime-{version}/license-and-acknowledgements.html",
                fetch(
                    f"https://docs.python.org/{sys.version_info.major}.{sys.version_info.minor}/license.html"
                ),
            )
        )
        copies.append(
            save(
                root,
                "licenses/MPL-2.0.txt",
                fetch("https://www.mozilla.org/media/MPL/2.0/index.txt"),
            )
        )
    else:
        for relative in (
            f"licenses/python-runtime-{version}/license-and-acknowledgements.html",
            "licenses/MPL-2.0.txt",
        ):
            file = root / relative
            if not file.is_file():
                raise RuntimeError(
                    "Run collection once with --fetch-upstream to obtain runtime notices"
                )
            copies.append(
                {"path": relative, "sha256": sha(file.read_bytes()), "bytes": file.stat().st_size}
            )
    return {
        "name": "CPython",
        "version": version,
        "ecosystem": "runtime",
        "scope": "runtime",
        "license": "Python-2.0",
        "upstream": [
            f"https://github.com/python/cpython/tree/v{version}",
            f"https://docs.python.org/{sys.version_info.major}.{sys.version_info.minor}/license.html",
        ],
        "files": copies,
    }


def electron_sources(root: Path, modules: Path, network: bool) -> dict:
    package = json.loads((modules / "electron/package.json").read_text(encoding="utf-8"))
    version = package["version"]
    folder = f"licenses/source/electron-{version}"
    provenance_file = root / folder / "provenance.json"
    if not network:
        if not provenance_file.is_file():
            raise RuntimeError("Electron source provenance is missing; run --fetch-upstream once")
        provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
        if provenance.get("electron_version") != version:
            raise RuntimeError("Electron source provenance does not match the build")
        for file in provenance["files"]:
            if sha((root / file["path"]).read_bytes()) != file["sha256"]:
                raise RuntimeError(f"Source evidence hash mismatch: {file['path']}")
        if (
            sha((modules / "electron/dist/ffmpeg.dll").read_bytes())
            != provenance["ffmpeg_dll_sha256"]
        ):
            raise RuntimeError("FFmpeg binary does not match the recorded official build")
        return provenance
    deps_url = f"https://raw.githubusercontent.com/electron/electron/v{version}/DEPS"
    deps = fetch(deps_url)
    chromium_version = re.search(
        r"['\"]chromium_version['\"]\s*:\s*['\"]([^'\"]+)", deps.decode()
    ).group(1)
    chromium_url = (
        f"https://chromium.googlesource.com/chromium/src/+/{chromium_version}/DEPS?format=TEXT"
    )
    chromium_deps = base64.b64decode(fetch(chromium_url))
    save(root, f"{folder}/DEPS", deps)
    save(root, f"{folder}/chromium-DEPS", chromium_deps)
    match = re.search(
        r"['\"]src/third_party/ffmpeg['\"]\s*:\s*(.*?)(?:,\s*\n)", chromium_deps.decode(), re.S
    )
    if not match:
        raise RuntimeError("Chromium DEPS did not identify FFmpeg")
    revision = re.search(r"['\"]([a-f0-9]{40})['\"]", match.group(1))
    if revision is None and "Var('ffmpeg_revision')" in match.group(1):
        revision = re.search(
            r"['\"]ffmpeg_revision['\"]\s*:\s*['\"]([a-f0-9]{40})['\"]", chromium_deps.decode()
        )
    if not revision:
        raise RuntimeError("Chromium FFmpeg revision needs manual review")
    revision = revision.group(1)
    patch_url = (
        f"https://raw.githubusercontent.com/electron/electron/v{version}/patches/ffmpeg/.patches"
    )
    patch_list = fetch(patch_url)
    files = [
        save(root, f"{folder}/DEPS", deps),
        save(root, f"{folder}/chromium-DEPS", chromium_deps),
        save(root, f"{folder}/ffmpeg-patches/.patches", patch_list),
    ]
    patches = []
    for name in patch_list.decode().splitlines():
        name = name.strip()
        if not name or name.startswith("#"):
            continue
        if "/" in name or "\\" in name or not name.endswith(".patch"):
            raise RuntimeError("Unsafe upstream patch filename")
        url = (
            f"https://raw.githubusercontent.com/electron/electron/v{version}/patches/ffmpeg/{name}"
        )
        files.append(save(root, f"{folder}/ffmpeg-patches/{name}", fetch(url)))
        patches.append(url)
    # A direct exact-revision source link and locally retained build/dependency
    # evidence accompany the upstream binary. LGPL files remain replaceable DLLs.
    source_url = f"https://chromium.googlesource.com/chromium/third_party/ffmpeg/+/{revision}"
    for filename in (
        "COPYING.LGPLv2.1",
        "LICENSE.md",
        "BUILD.gn",
        "README.chromium",
        "CREDITS.chromium",
        "chromium/config/Chrome/win/x64/config.h",
    ):
        url = f"https://chromium.googlesource.com/chromium/third_party/ffmpeg/+/{revision}/{filename}?format=TEXT"
        files.append(save(root, f"{folder}/ffmpeg/{filename}", base64.b64decode(fetch(url))))
    for filename in ("build/args/release.gn", "build/args/all.gn", "patches/config.json"):
        url = f"https://raw.githubusercontent.com/electron/electron/v{version}/{filename}"
        files.append(save(root, f"{folder}/electron/{filename}", fetch(url)))
    for filename in ("media/ffmpeg/README.md", "media/ffmpeg/scripts/build_ffmpeg.py"):
        url = f"https://chromium.googlesource.com/chromium/src/+/{chromium_version}/{filename}?format=TEXT"
        files.append(save(root, f"{folder}/chromium/{filename}", base64.b64decode(fetch(url))))
    archive_url = (
        f"https://chromium.googlesource.com/chromium/third_party/ffmpeg/+archive/{revision}.tar.gz"
    )
    archive_relative = f"{folder}/ffmpeg-source-{revision}.tar.gz"
    files.append(public_copy(root, archive_relative, archive_url, network))
    with tarfile.open(root / archive_relative, "r:gz") as archive:
        names = set(archive.getnames())
        if not {"COPYING.LGPLv2.1", "configure", "BUILD.gn", "libavcodec/avcodec.h"}.issubset(
            names
        ):
            raise RuntimeError("Incomplete FFmpeg source archive")
    value = {
        "electron_version": version,
        "electron_tag": f"v{version}",
        "chromium_version": chromium_version,
        "ffmpeg_revision": revision,
        "ffmpeg_license": "LGPL-2.1-or-later",
        "ffmpeg_source": source_url,
        "ffmpeg_source_archive": archive_url,
        "electron_source": f"https://github.com/electron/electron/tree/v{version}",
        "electron_build_instructions": "https://www.electronjs.org/docs/latest/development/build-instructions-gn",
        "electron_ffmpeg_patches": patches,
        "modified_by_companion_studio": False,
        "ffmpeg_dll_sha256": sha((modules / "electron/dist/ffmpeg.dll").read_bytes()),
        "files": files,
    }
    save(
        root,
        f"{folder}/provenance.json",
        (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--node-modules", type=Path)
    parser.add_argument("--fetch-upstream", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    modules = args.node_modules or root / "desktop/node_modules"
    components = python_packages(root) + npm_packages(root, modules)
    components.append(standard_runtime(root, args.fetch_upstream))
    components.extend(native_components(root, args.fetch_upstream))
    provenance = electron_sources(root, modules, args.fetch_upstream)
    chromium_notice = f"licenses/electron-{provenance['electron_version']}/LICENSES.chromium.html"
    components.append(
        {
            "name": "Chromium and embedded third-party components",
            "version": provenance["chromium_version"],
            "ecosystem": "native",
            "scope": "Electron runtime",
            "license": "LicenseRef-Chromium-ThirdParty",
            "upstream": [
                f"https://chromium.googlesource.com/chromium/src/+/{provenance['chromium_version']}"
            ],
            "files": [
                {
                    "path": chromium_notice,
                    "sha256": sha((root / chromium_notice).read_bytes()),
                    "bytes": (root / chromium_notice).stat().st_size,
                }
            ],
        }
    )
    components.append(
        {
            "name": "FFmpeg",
            "version": provenance["ffmpeg_revision"],
            "ecosystem": "native",
            "scope": "Electron runtime dynamically linked DLL",
            "license": "LGPL-2.1-or-later",
            "upstream": [provenance["ffmpeg_source"]],
            "files": provenance["files"],
        }
    )
    value = {
        "format_version": 1,
        "application": "Companion Studio",
        "components": components,
        "electron_provenance": provenance,
    }
    save(
        root,
        "licenses/inventory.json",
        (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    lines = [
        "# Third-party notices",
        "",
        "Companion Studio's original source is MIT licensed. The components below retain their own licenses.",
        "",
        "This inventory records the actual dependency versions in the build environment. `runtime` dependencies may be bundled; `build/test` tools are used to produce or verify it and are listed separately. Package source/notice files are copied unchanged, with no virtual environment, installation cache or personal metadata.",
        "",
        "| Component | Version | Scope | License | Notice files |",
        "|---|---|---|---|---|",
    ]
    for component in components:
        first = component["files"][0]["path"]
        lines.append(
            f"| {component['name']} | {component['version']} | {component['scope']} | {component['license']} | [Files]({first}) |"
        )
    lines.extend(
        [
            "",
            "## Component-specific terms",
            "",
            "- **Electron / Chromium / FFmpeg:** the installer retains Electron's unmodified `LICENSE` and `LICENSES.chromium.html`; a copy is under `licenses/`. Chromium's third-party notice set covers its embedded components. `ffmpeg.dll` is an unmodified dynamically linked library under LGPL-2.1-or-later, not under the application's MIT license. You may replace it with an ABI-compatible build and reverse engineer the combined program to debug your modifications to that library. No application term restricts those LGPL rights.",
            f"- **Corresponding FFmpeg source:** a complete exact-revision archive for [{provenance['ffmpeg_revision']}]({provenance['ffmpeg_source']}) is included at `licenses/source/electron-{provenance['electron_version']}/ffmpeg-source-{provenance['ffmpeg_revision']}.tar.gz`. Electron [{provenance['electron_tag']}]({provenance['electron_source']}) contains the dependency pins and FFmpeg patches. The exact version pins, original build scripts and patches are retained in that same directory. Build an ABI-compatible DLL following the linked Electron build instructions and retained FFmpeg scripts. Companion Studio makes no modifications to Electron or FFmpeg. Keep this source archive with the installer when redistributing. Electron's original build enables proprietary codecs; copyright licenses do not grant third-party codec patent rights.",
            "- **certifi:** MPL-2.0 notice and the corresponding, unmodified `.py` and `cacert.pem` source files are included under `licenses/source/certifi-<version>/`. Only these files, rather than Companion Studio's unrelated original source, are covered by MPL. The full MPL text is `licenses/MPL-2.0.txt`.",
            "- **PyInstaller:** its bootloader exception allows application bundles to use their own licenses while retaining the licenses of their dependencies. The complete upstream `COPYING.txt` is included. See [official license explanation](https://pyinstaller.org/en/stable/license.html). Build-tool files are not relabeled MIT.",
            "- **Fish Audio:** the distributed Python SDK is Apache-2.0. It is a client for an optional external service, not a distribution of Fish model weights, training data or character voices. Service usage, uploaded reference audio and model access remain subject to that service's separate terms and users' authorization.",
            "- **IndexTTS / other local engines:** the app can connect to an independently installed local worker. No IndexTTS implementation, model weights, voice pack, reference recording or third-party character asset is distributed in this clean release. Users install and license those optional components separately.",
            "- **CPython and native libraries:** Python's complete license and its official third-party acknowledgements are preserved. The Python runtime's components keep their upstream terms; do not relabel the runtime as the application's original MIT source.",
            "- **CPython liblzma:** the version pinned by the corresponding CPython Windows build (`PCbuild-python.props`) is listed separately from Pillow's newer XZ library. `LicenseRef-XZ-Public-Domain` refers to its original `COPYING` grant: liblzma is in the public domain, while separately licensed upstream build tools do not enter the compiled library.",
            "- **Pillow / FreeType:** Pillow's original combined license includes notices for its native image/codec libraries. The FreeType component is used under the FreeType License (FTL), not its alternative GPL. Portions of this software are copyright © 1996–2026 The FreeType Project (https://freetype.org). All rights reserved. The Python and Pillow native components listed above retain their individual license texts.",
            "",
            "## Rebuilding the inventory",
            "",
            "After installing dependencies, use the same Python environment as the backend build:",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe .\\scripts\\collect-third-party.py --fetch-upstream",
            "```",
            "",
            "Later builds may omit `--fetch-upstream` to reuse the pinned source evidence already included, provided dependency versions have not changed. The collector fails if a required license or source provenance is missing. `licenses/inventory.json` stores relative file names and SHA-256 digests. Include this document and the complete `licenses/` directory in source archives and installed `resources/`.",
            "",
        ]
    )
    (root / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")
    manifest = [
        {
            "path": file.relative_to(root).as_posix(),
            "sha256": sha(file.read_bytes()),
            "bytes": file.stat().st_size,
        }
        for file in sorted((root / "licenses").rglob("*"))
        if file.is_file() and file.name != "source-manifest.json"
    ]
    save(
        root,
        "licenses/source-manifest.json",
        (json.dumps({"files": manifest}, indent=2) + "\n").encode("utf-8"),
    )
    print(
        json.dumps(
            {
                "components": len(components),
                "notice_files": sum(len(c["files"]) for c in components),
                "electron": provenance["electron_version"],
                "chromium": provenance["chromium_version"],
                "ffmpeg_revision": provenance["ffmpeg_revision"],
            }
        )
    )


if __name__ == "__main__":
    main()
