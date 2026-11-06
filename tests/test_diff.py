import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("api_diff", ROOT / "api-diff/check.py")
api_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_diff)


class DiffTests(unittest.TestCase):
    def args(self, root, cli="crucible"):
        return argparse.Namespace(cli=cli, before="baseline", after="proposed", config="proposed/crucible.toml", service="", report_path=str(root / "report"))

    def test_paths_cannot_escape_checkouts(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertRaises(ValueError, api_diff.inside, directory, "../outside.json")
            self.assertRaises(ValueError, api_diff.inside, directory, str(Path(directory).resolve()))

    def test_missing_sources_fail_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": str(root)}, clear=True), patch.object(api_diff.subprocess, "run") as run:
                self.assertRaises(ValueError, api_diff.run, self.args(root))
                run.assert_not_called()

    def test_breaking_report_is_preserved_and_returns_the_checker_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for side in ("baseline", "proposed"):
                (root / side).mkdir()
                (root / side / "model.json").write_text("{}", encoding="utf-8")
            (root / "proposed/crucible.toml").write_text("schema_version = 1", encoding="utf-8")
            report = {"findings": [{"rule": "compatibility.http", "shape": "example.api#Get", "severity": "error", "message": "<script>changed</script>", "exception": "approved <change>"}]}
            args = self.args(root)
            result = subprocess.CompletedProcess([], 1, json.dumps(report), "API policy check failed")
            with patch.object(api_diff.subprocess, "run", return_value=result) as run, patch.dict(os.environ, {"GITHUB_WORKSPACE": str(root)}, clear=True):
                self.assertEqual(api_diff.run(args), 1)
                self.assertEqual(run.call_args.args[0], ["crucible", "diff", str(root / "baseline"), str(root / "proposed"), "--config", str(root / "proposed/crucible.toml"), "--json"])
            self.assertEqual(json.loads((root / "report/report.json").read_text()), report)
            summary = (root / "report/report.md").read_text()
            self.assertIn("&lt;script&gt;", summary)
            self.assertIn("Exception:", summary)
            self.assertNotIn("<script>", summary)

    @unittest.skipUnless(os.environ.get("CRUCIBLE_CLI"), "Set CRUCIBLE_CLI to exercise the real checker")
    def test_real_checker_accepts_identity_and_rejects_an_http_break(self):
        model = '''service Example {
    version "1"
    protocol json
    operations [GetItem]
}
readonly operation GetItem {
    http GET "/v1/items" -> 200
    output { name: string }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for side in ("baseline", "proposed"):
                (root / side).mkdir()
                (root / side / "api.cru").write_text(model, encoding="utf-8")
                (root / side / "crucible.toml").write_text('schema_version = 1\n[model]\nnamespace = "example.api"\nlanguage = 1\nsources = ["."]\nimports = []\n', encoding="utf-8")
            args = self.args(root, os.environ["CRUCIBLE_CLI"])
            # The two required package inputs also select the proposed package's policy.
            args.config = ""
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": str(root)}):
                self.assertEqual(api_diff.run(args), 0)
                (root / "proposed/api.cru").write_text(model.replace("/v1/items", "/v2/items"), encoding="utf-8")
                self.assertEqual(api_diff.run(args), 1)
                (root / "proposed/api.cru").write_text("not a Crucible model", encoding="utf-8")
                self.assertRaises(ValueError, api_diff.run, args)


if __name__ == "__main__":
    unittest.main()
