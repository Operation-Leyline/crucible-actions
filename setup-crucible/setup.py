"""Select and install the CLI matching a consumer's locked Crucible dependency."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

REPOSITORY = "Operation-Leyline/crucible"
# Match `crucible sdk`'s compiler check. A consumer of an older SDK can
# legitimately bring an older client runtime into the same lockfile.
PINNED = {"crucible-build", "crucible-server-runtime-rust"}
COMMIT = re.compile(r"[0-9a-f]{40}")
TAG = re.compile(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")


class OriginBoundRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, *args, **kwargs):
        redirected = super().redirect_request(request, *args, **kwargs)
        if redirected and urlsplit(request.full_url).netloc != urlsplit(redirected.full_url).netloc:
            redirected.remove_header("Authorization")
        if redirected and urlsplit(redirected.full_url).scheme != "https":
            raise ValueError("Refusing a non-HTTPS download redirect")
        return redirected


def download(url):
    headers = {"User-Agent": "crucible-actions", "Accept": "application/vnd.github+json"}
    # Release assets are public. Credentials are only for the GitHub API's rate limit.
    if urlsplit(url).netloc == "api.github.com" and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    try:
        with build_opener(OriginBoundRedirect()).open(Request(url, headers=headers), timeout=90) as response:
            return response.read()
    except HTTPError as error:
        if error.code == 404:
            return None
        raise ValueError(f"GitHub download failed (HTTP {error.code}); check token permissions or rate limits") from None


def tag_revision(tag):
    data = download(f"https://api.github.com/repos/{REPOSITORY}/git/ref/tags/{quote(tag, safe='')}")
    if data is None:
        return None
    obj = json.loads(data)["object"]
    for _ in range(5):
        if obj["type"] == "commit" and COMMIT.fullmatch(obj["sha"]):
            return obj["sha"]
        if obj["type"] != "tag" or not COMMIT.fullmatch(obj["sha"]):
            break
        data = download(f"https://api.github.com/repos/{REPOSITORY}/git/tags/{obj['sha']}")
        if data is None:
            break
        obj = json.loads(data)["object"]
    raise ValueError(f"Cannot resolve release tag {tag} to a commit")


def locked(root):
    for directory in (root, *root.parents):
        lock = directory / "Cargo.lock"
        if lock.is_file():
            with lock.open("rb") as source:
                packages = tomllib.load(source).get("package", [])
            packages = [p for p in packages if p.get("name") in PINNED]
            if not packages:
                raise ValueError(f"{lock} has no Crucible dependency; set crucible-ref to a release tag or full commit")
            pins = set()
            versions = set()
            for package in packages:
                source = package.get("source", "")
                versions.add(package["version"])
                if source.startswith("git+"):
                    url = urlsplit(source[4:])
                    if url.hostname != "github.com" or url.path.rstrip("/").removesuffix(".git").lower() != "/" + REPOSITORY.lower():
                        raise ValueError("The locked Crucible comes from another repository; set crucible-ref explicitly")
                    if not COMMIT.fullmatch(url.fragment):
                        raise ValueError("Cargo.lock has no full resolved Crucible commit; regenerate it with Cargo")
                    pins.add(url.fragment)
                else:
                    raise ValueError("A path or registry Crucible dependency needs an explicit crucible-ref")
            if len(pins) != 1 or len(versions) != 1:
                raise ValueError("Cargo.lock pins multiple Crucible revisions or versions; update the Crucible dependencies together")
            return pins.pop(), "v" + versions.pop()
        if (directory / ".git").exists():
            break
    raise ValueError("No Cargo.lock found; run cargo generate-lockfile and commit it, or set crucible-ref explicitly")


def target():
    return {
        ("Linux", "X64"): "x86_64-unknown-linux-musl",
        ("Linux", "ARM64"): "aarch64-unknown-linux-musl",
        ("macOS", "X64"): "x86_64-apple-darwin",
        ("macOS", "ARM64"): "aarch64-apple-darwin",
        ("Windows", "X64"): "x86_64-pc-windows-msvc",
    }.get((os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"]), "source")


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for name, value in values.items():
            if "\n" in str(value) or "\r" in str(value):
                raise ValueError(f"{name} cannot contain line breaks")
            stream.write(f"{name}={value}\n")


def resolve():
    ref = os.environ.get("CRUCIBLE_REF", "").strip()
    candidate = ref if TAG.fullmatch(ref) else ""
    if not ref:
        directory = Path(os.environ["WORKING_DIRECTORY"])
        if not directory.is_absolute():
            directory = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())) / directory
        ref, candidate = locked(directory.resolve(strict=True))
    if not COMMIT.fullmatch(ref) and not TAG.fullmatch(ref):
        raise ValueError("crucible-ref must be a release tag (v0.9.0) or a full 40-character commit, not a moving branch")
    release_revision = tag_revision(candidate) if candidate else None
    if TAG.fullmatch(ref):
        if release_revision is None:
            raise ValueError(f"Release tag {ref} does not exist")
        revision, release = release_revision, ref
    else:
        revision = ref
        release = candidate if release_revision == revision else ""
    triple = target()
    root = Path(os.environ["RUNNER_TEMP"]) / "crucible" / f"{revision}-{triple}"
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    key = f"crucible-{digest}-{revision}-{triple}-{os.environ['RUNNER_OS']}-{os.environ['RUNNER_ARCH']}-{os.environ['TOOLCHAIN']}"
    output(revision=revision, release=release, target=triple, root=root, **{"cache-key": key})
    print(f"Selected Crucible {revision}" + (f" ({release}, prebuilt preferred)" if release else " (source build)"))


def install_archive(data, checksum, filename, root):
    fields = checksum.decode("utf-8").split()
    expected = fields[0] if fields else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or hashlib.sha256(data).hexdigest() != expected.lower():
        raise ValueError("Crucible release archive checksum does not match")
    name = "crucible.exe" if filename.endswith(".zip") else "crucible"
    binary = root / "bin" / name
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / filename
        archive.write_bytes(data)
        # Only copy the executable; never extract paths or links supplied by an archive.
        if filename.endswith(".zip"):
            with zipfile.ZipFile(archive) as source:
                members = [m for m in source.infolist() if Path(m.filename).name == name and not m.is_dir()]
                if len(members) != 1:
                    raise ValueError("Release archive must contain exactly one Crucible executable")
                contents = source.read(members[0])
        else:
            with tarfile.open(archive) as source:
                members = [m for m in source.getmembers() if Path(m.name).name == name and m.isfile()]
                if len(members) != 1:
                    raise ValueError("Release archive must contain exactly one Crucible executable")
                contents = source.extractfile(members[0]).read()
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(contents)
        binary.chmod(0o755)


def prebuilt():
    release, triple = os.environ["RELEASE"], os.environ["TARGET"]
    if not release or triple == "source":
        output(installed="false")
        return
    extension = "zip" if "windows" in triple else "tar.xz"
    filename = f"crucible-cli-{triple}.{extension}"
    url = f"https://github.com/{REPOSITORY}/releases/download/{quote(release, safe='')}/{filename}"
    data = download(url)
    if data is None:
        print(f"No prebuilt CLI for {release}/{triple}; building the selected commit")
        output(installed="false")
        return
    checksum = download(url + ".sha256")
    if checksum is None:
        raise ValueError("Release archive has no SHA-256 checksum")
    install_archive(data, checksum, filename, Path(os.environ["INSTALL_ROOT"]))
    output(installed="true")


def finish():
    root = Path(os.environ["INSTALL_ROOT"])
    binary = root / "bin" / ("crucible.exe" if os.environ["RUNNER_OS"] == "Windows" else "crucible")
    source = os.environ.get("SOURCE_BINARY", "")
    if source:
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, binary)
    version = subprocess.run([str(binary), "--version"], check=True, capture_output=True, text=True).stdout.strip()
    embedded = re.search(r"\(([0-9a-f]{40})\)", version)
    if embedded and embedded[1] != os.environ["REVISION"]:
        raise ValueError("The installed CLI's embedded revision does not match the selected commit")
    print(version)
    output(cli=binary, revision=os.environ["REVISION"])
    if "\n" in str(binary.parent) or "\r" in str(binary.parent):
        raise ValueError("Installation path cannot contain line breaks")
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as stream:
        stream.write(str(binary.parent) + "\n")


if __name__ == "__main__":
    try:
        {"resolve": resolve, "download": prebuilt, "finish": finish}[sys.argv[1]]()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"::error::Crucible setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
