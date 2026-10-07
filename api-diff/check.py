"""Build two service contracts, then preserve and display Crucible's diff report."""

import argparse
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def inside(root, relative):
    root = Path(root).resolve(strict=True)
    relative = Path(relative)
    if relative.is_absolute():
        raise ValueError(f"Expected a checkout-relative path: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Path escapes checkout: {relative}")
    return path


def prepare(root, working_directory, model_path, command, destination):
    root = Path(root).resolve(strict=True)
    working = inside(root, working_directory)
    model = inside(root, str(Path(working_directory) / model_path))
    if command:
        environment = {**os.environ, "CRUCIBLE_MODEL_OUT": str(model), "CARGO_BUILD_JOBS": os.environ.get("CARGO_BUILD_JOBS", "2")}
        subprocess.run(["bash", "-e", "-o", "pipefail", "-c", command], cwd=working, env=environment, check=True)
    if not model.is_file():
        raise ValueError(f"Missing assembled model: {model}. Supply a checked-in AST or a build-command that exports it.")
    shutil.copyfile(model, destination)


def markdown(report):
    lines = ["## Crucible API compatibility", ""]
    findings = report["findings"]
    if not findings:
        return "\n".join(lines + ["No compatibility violations found.", ""])
    for finding in findings:
        # Escape model/config-controlled text so it cannot inject summary markup.
        text = lambda value: html.escape(str(value)).replace("\n", " ").replace("\r", " ")
        lines.append(f"- <strong>{text(finding['severity'])}</strong> <code>{text(finding['rule'])}</code> <code>{text(finding['shape'])}</code>: {text(finding['message'])}")
        if finding.get("exception"):
            lines.append(f"  Exception: {text(finding['exception'])}")
    return "\n".join(lines) + "\n"


def run(args):
    report_path = Path(args.report_path).resolve()
    report_path.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GITHUB_OUTPUT"):
        if "\n" in str(report_path) or "\r" in str(report_path):
            raise ValueError("report-path cannot contain line breaks")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"report-path={report_path}\n")
    proposed = Path(args.proposed).resolve(strict=True)
    config = inside(proposed, str(Path(args.working_directory) / args.config_path))
    if not config.is_file():
        raise ValueError(f"Missing policy: {config}")
    with tempfile.TemporaryDirectory(prefix="crucible-diff-") as temporary:
        baseline = Path(temporary) / "baseline.json"
        proposed_model = Path(temporary) / "proposed.json"
        prepare(args.baseline, args.working_directory, args.model_path, args.build_command, baseline)
        prepare(args.proposed, args.working_directory, args.model_path, args.build_command, proposed_model)
        command = [args.cli, "diff", str(baseline), str(proposed_model), "--config", str(config), "--json"]
        if args.service:
            command += ["--service", args.service]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    (report_path / "checker.log").write_text(result.stderr, encoding="utf-8")
    try:
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
            raise ValueError("checker did not produce a findings array")
        summary = markdown(report)
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"Checker did not produce a valid report: {error}\n{result.stderr}") from error
    (report_path / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (report_path / "report.md").write_text(summary, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(summary)
    print(summary)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cli", "baseline", "proposed"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--model-path", default="model/model.json")
    parser.add_argument("--build-command", default="")
    parser.add_argument("--config-path", default="crucible.toml")
    parser.add_argument("--service", default="")
    parser.add_argument("--report-path", default="api-diff-report")
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        report_path = Path(args.report_path)
        report_path.mkdir(parents=True, exist_ok=True)
        # A setup/build failure remains a failure and leaves an artifact for diagnosis.
        (report_path / "failure.txt").write_text(str(error), encoding="utf-8")
        parser.exit(1, f"API compatibility check failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
