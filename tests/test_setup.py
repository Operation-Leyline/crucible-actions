import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request
import zipfile

spec = importlib.util.spec_from_file_location("setup", Path(__file__).resolve().parents[1] / "setup-crucible/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
A, B = "a" * 40, "b" * 40


def lock(root, entries):
    text = "version = 4\n"
    for name, version, source in entries:
        text += f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        if source:
            text += f'source = "{source}"\n'
    (root / "Cargo.lock").write_text(text, encoding="utf-8")


def git(revision=A):
    return f"git+https://github.com/Operation-Leyline/crucible?tag=v0.9.0#{revision}"


class SetupTests(unittest.TestCase):
    def test_finds_the_resolved_commit_from_a_nested_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "service"
            nested.mkdir()
            (root / ".git").write_text("gitdir: elsewhere")
            lock(root, [("crucible-build", "0.9.0", git()), ("crucible-server-runtime-rust", "0.9.0", git()),
                        ("crucible-client-runtime-rust", "0.8.0", git(B))])
            self.assertEqual(setup.locked(nested), (A, "v0.9.0"))

    def test_missing_lock_does_not_select_a_parent_repository_or_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock(root, [("crucible-build", "0.9.0", git())])
            nested = root / "different-repository"
            nested.mkdir()
            (nested / ".git").mkdir()
            with self.assertRaisesRegex(ValueError, "No Cargo.lock"):
                setup.locked(nested)

    def test_conflicting_revisions_versions_and_source_types_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for other in (("0.9.0", git(B)), ("0.8.0", git())):
                lock(root, [("crucible-build", "0.9.0", git()), ("crucible-server-runtime-rust", *other)])
                with self.assertRaisesRegex(ValueError, "update.*together"):
                    setup.locked(root)

    def test_path_custom_registry_and_fork_need_an_explicit_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in (None, "registry+https://example.com/index", "registry+https://github.com/rust-lang/crates.io-index", f"git+https://example.com/crucible#{A}"):
                lock(Path(directory), [("crucible-build", "0.9.0", source)])
                with self.assertRaisesRegex(ValueError, "crucible-ref"):
                    setup.locked(Path(directory))

    def test_credentials_in_lock_sources_are_never_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            lock(Path(directory), [("crucible-build", "0.9.0", "git+https://user:sentinel-secret@example.com/crucible#short")])
            with self.assertRaises(ValueError) as error:
                setup.locked(Path(directory))
            self.assertNotIn("sentinel-secret", str(error.exception))

    def plan(self, root, ref="", release=A):
        env = {
            "CRUCIBLE_REF": ref, "WORKING_DIRECTORY": str(root), "RUNNER_TEMP": str(root),
            "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "TOOLCHAIN": "stable", "GITHUB_OUTPUT": str(root / "output"),
        }
        with patch.dict(os.environ, env), patch.object(setup, "tag_revision", return_value=release):
            setup.resolve()
        return dict(line.split("=", 1) for line in (root / "output").read_text().splitlines())

    def test_prebuilt_must_match_the_exact_locked_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock(root, [("crucible-build", "0.9.0", git())])
            self.assertEqual(self.plan(root)["release"], "v0.9.0")
            self.assertEqual(self.plan(root, release=B)["release"], "")
            self.assertEqual(self.plan(root, release=None)["revision"], A)

    def test_explicit_tag_and_commit_work_without_a_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(self.plan(root, "v0.9.0")["revision"], A)
            self.assertEqual(self.plan(root, B)["revision"], B)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                self.plan(root, "v0.9.0", release=None)
            with self.assertRaisesRegex(ValueError, "moving branch"):
                self.plan(root, "main")

    def test_annotated_release_tags_are_resolved(self):
        responses = [json.dumps({"object": {"type": "tag", "sha": B}}).encode(), json.dumps({"object": {"type": "commit", "sha": A}}).encode()]
        with patch.object(setup, "download", side_effect=responses):
            self.assertEqual(setup.tag_revision("v0.9.0"), A)

    def test_redirect_does_not_forward_api_credentials(self):
        request = Request("https://api.github.com/old", headers={"Authorization": "Bearer sentinel"})
        redirected = setup.OriginBoundRedirect().redirect_request(request, None, 302, "Found", {}, "https://example.com/new")
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaisesRegex(ValueError, "non-HTTPS"):
            setup.OriginBoundRedirect().redirect_request(request, None, 302, "Found", {}, "http://api.github.com/new")

    def test_zip_and_tar_only_install_the_checksummed_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for extension in ("zip", "tar.xz"):
                payload = io.BytesIO()
                name = "crucible.exe" if extension == "zip" else "crucible"
                if extension == "zip":
                    with zipfile.ZipFile(payload, "w") as archive:
                        archive.writestr(f"package/{name}", b"binary")
                        archive.writestr("../../outside", b"ignored")
                else:
                    with tarfile.open(fileobj=payload, mode="w:xz") as archive:
                        member = tarfile.TarInfo(f"package/{name}")
                        member.size = 6
                        archive.addfile(member, io.BytesIO(b"binary"))
                data = payload.getvalue()
                checksum = hashlib.sha256(data).hexdigest().encode()
                setup.install_archive(data, checksum, "archive." + extension, root)
                self.assertEqual((root / "bin" / name).read_bytes(), b"binary")
                self.assertFalse((root / "outside").exists())
                with self.assertRaisesRegex(ValueError, "checksum"):
                    setup.install_archive(data + b"corrupt", checksum, "archive." + extension, root)

    def test_missing_archive_falls_back_but_missing_checksum_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"RELEASE": "v0.9.0", "TARGET": "x86_64-unknown-linux-musl", "INSTALL_ROOT": directory}
            with patch.dict(os.environ, env), patch.object(setup, "output") as output:
                with patch.object(setup, "download", return_value=None):
                    setup.prebuilt()
                    output.assert_called_with(installed="false")
                with patch.object(setup, "download", side_effect=[b"archive", None]):
                    with self.assertRaisesRegex(ValueError, "no SHA-256"):
                        setup.prebuilt()

    def test_finish_exports_a_path_with_spaces_and_refuses_a_wrong_binary(self):
        with tempfile.TemporaryDirectory(prefix="crucible setup ") as directory:
            root = Path(directory)
            env = {"INSTALL_ROOT": directory, "RUNNER_OS": "Linux", "REVISION": A,
                   "SOURCE_BINARY": "", "GITHUB_OUTPUT": str(root / "output"), "GITHUB_PATH": str(root / "path")}
            with patch.dict(os.environ, env), patch.object(setup.subprocess, "run", return_value=SimpleNamespace(stdout=f"crucible-cli 0.9.0 ({A})\n")):
                setup.finish()
            self.assertEqual((root / "path").read_text().strip(), str(root / "bin"))
            (root / "path").unlink()
            with patch.dict(os.environ, env), patch.object(setup.subprocess, "run", return_value=SimpleNamespace(stdout=f"crucible-cli 0.9.0 ({B})\n")):
                with self.assertRaisesRegex(ValueError, "embedded revision"):
                    setup.finish()
            self.assertFalse((root / "path").exists())


if __name__ == "__main__":
    unittest.main()
