# crucible-actions

Shared GitHub Actions for [Crucible](https://github.com/Operation-Leyline/crucible) services. Install the compiler your service uses, then run ordinary Crucible commands.

## Set up the CLI

Check out your service and add one step:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@v4
  - uses: Operation-Leyline/crucible-actions/setup-crucible@<pinned-sha>
  - run: crucible check model
  - run: crucible sdk generate
```

The last command needs a `crucible-sdk.toml` declaring your SDKs; omit it if you do not generate SDKs. See [SDK configuration](https://github.com/Operation-Leyline/crucible/blob/main/docs/sdk-packaging.md#1-declaring-sdks).

Commit `Cargo.lock`. Setup finds it in the working directory or its nearest parent, stopping at the repository root. It reads the resolved Git commit for `crucible-build`, `crucible-server-runtime-rust`, or `crucible-client-runtime-rust`. Conflicting commits or versions fail with instructions to update them together. It never silently installs the latest compiler.

When the package's release tag resolves to that exact commit, setup downloads a prebuilt CLI and verifies its SHA-256 checksum. Otherwise it builds the selected commit with Cargo. Both installations are cached, and `crucible` is on PATH in subsequent steps. The CLI's embedded revision is checked when present.

Crucible and these actions are public. The default `github.token` handles API rate limits; no GitHub App, organization secrets, or extra checkout is required. Fork pull requests can use the same setup step.

### Inputs and outputs

| Input | Default | Use |
|---|---|---|
| `working-directory` | `.` | Where to start looking for `Cargo.lock`; workspace-relative or absolute |
| `crucible-ref` | read `Cargo.lock` | Override with a release tag such as `v0.9.0` or a full 40-character commit |
| `toolchain` | `stable` | Rust toolchain for a source build; prebuilt installation does not install Rust |
| `token` | `github.token` | GitHub API and source checkout token |

| Output | Meaning |
|---|---|
| `cli` | Absolute executable path, for callers that need it |
| `revision` | Full selected Crucible commit |

A separate CLI-only job can select a release without a service checkout:

```yaml
- uses: Operation-Leyline/crucible-actions/setup-crucible@<pinned-sha>
  with:
    crucible-ref: v0.9.0
- run: crucible --version
```

For a service nested under `backend/`, set `working-directory: backend`. Existing callers supplying `crucible-ref`, `toolchain`, `token`, and reading the `cli` output continue to work. Source builds reserve the `crucible-tooling/` directory in the workspace. Path dependencies, forks, and registry dependencies need an explicit `crucible-ref`.

Supported prebuilt runners: Linux x64/arm64, macOS x64/arm64, Windows x64. Other runners build from source. Setup provisions Python 3.12; Bash is needed to run the composite steps. A source build also needs the native build tools for Rust. Service compilation and SDK consumer tests still need their own language toolchains.

Pin the action to a reviewed commit independently of the Crucible dependency your service pins.

## API compatibility reports

A `.cru` package with a configured frozen release baseline already checks compatibility during builds and `crucible check`. No separate comparison workflow is required for that guard. See [API releases](https://github.com/Operation-Leyline/crucible/blob/main/docs/api-policy.md#guarding-a-cru-package-against-its-last-release).

Use `api-diff` when you also want a report comparing the PR base or another Git revision with the proposal. Copy [the caller template](templates/api-diff.yml), adjust the model paths, and pin its actions to reviewed commits:

```yaml
- uses: Operation-Leyline/crucible-actions/api-diff@<pinned-sha>
  with:
    before: baseline/model
    after: service/model
    config: service/model/crucible.toml
```

Run setup first. `before` and `after` are `.cru` packages, each with its own `crucible.toml`. Paths must stay inside `GITHUB_WORKSPACE`. Optional `service` selects a fully qualified service ID. `cli` defaults to `crucible`; `report-path` defaults to `api-diff-report`.

The action writes JSON and Markdown reports and a job summary. Breaking findings remain failures; invalid models and setup errors never become a compatibility pass. The template uploads the reports even when comparison fails. Findings cover wire and model-level SDK compatibility, not every target language's ABI.

The reusable `.github/workflows/api-diff.yml` provides the same checkout/comparison/report steps. Ordinary service CI should use the compiler's baseline guard; this workflow is optional.

## Validation

```sh
python -m unittest discover -s tests -v
CRUCIBLE_CLI=/absolute/path/to/crucible python -m unittest discover -s tests -v
```

CI installs the released CLI on Linux, macOS, and Windows, creates a fresh service, runs setup again using only its lockfile, checks the model, generates an SDK, builds the service, and verifies HTTP 200 and modeled 400 responses. Real compatibility tests accept identical `.cru` packages and reject an HTTP route change and malformed sources. A separate Linux job checks automatic installation of a non-release commit from source. Unit tests cover conflicting pins, checksum failures, archive handling, and credential redirects.
