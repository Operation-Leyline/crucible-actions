# crucible-actions

Shared GitHub Actions for Crucible services. This initial release provides a
pinned Crucible tool setup, API compatibility checking, and a reusable workflow.
Static naming, path, and documentation policy runs in the service's Cargo build
through `crucible-build`; the compatibility job compares two contracts.

## Private access across organizations

Copy [the caller template](templates/api-diff.yml) to your service's
`.github/workflows/api-diff.yml`. It authenticates with the existing
[crucible-ci-read App](https://github.com/apps/crucible-ci-read), checks out this
private repository at a pinned commit, and runs its composite actions locally.
The source repositories remain private. This also works when the caller belongs
to another organization: the token targets the Operation-Leyline installation,
rather than the caller's organization.

```yaml
- uses: actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349 # v2
  id: app-token
  with:
    app-id: ${{ vars.CRUCIBLE_APP_ID }}
    private-key: ${{ secrets.CRUCIBLE_APP_PRIVATE_KEY }}
    owner: Operation-Leyline
    repositories: |
      crucible
      crucible-actions
    permission-contents: read
```

The App has Contents: read and Metadata: read permissions. Its selected
repository installation must include **crucible** and **crucible-actions**;
an installation administrator can select these under the organization's
GitHub Apps settings. No broader permissions are needed. Before the first run,
confirm that the new Actions repository is selected.

Each trusted consumer needs repository variable `CRUCIBLE_APP_ID` (currently
`5189718`) and secret `CRUCIBLE_APP_PRIVATE_KEY`. These names match Leyline's
existing integration. Secrets cannot be copied out through GitHub's API;
provision other consumers from the key holder's existing secure copy. The App
only needs installation on the organization hosting the tooling repositories,
not on each consuming organization.

The token action creates a short-lived token and revokes it when the job ends.
Checkout has credential persistence disabled. See the
[token action documentation](https://github.com/actions/create-github-app-token)
for authentication and repository scoping. Fork PRs cannot receive repository
secrets. Keep this template on `pull_request`; do not switch it to
`pull_request_target` to execute fork code with credentials.

The template pins the shared actions and checker independently. The checker
commit must contain `crucible diff`; earlier Crucible release tags do not provide
it. The initial implementation is on `claude/api-policy`; no `v1` release tag is
published until review and merge.

## Comparing revisions

The template baseline is the pull request's base SHA; the proposed contract is
the PR head SHA. Change the baseline checkout's `ref` to a released tag or
immutable commit when preserving a published contract.
A missing baseline, model, policy, failed build, or malformed checker report
fails the job. There is no first-run compatibility bypass.

## Building models instead of checking in ASTs

For a single-crate service with the new `crucible-build` export hook:

```yaml
- uses: ./crucible-actions/api-diff
  with:
    cli: ${{ steps.tooling.outputs.cli }}
    baseline-directory: baseline
    proposed-directory: service
    working-directory: .
    model-path: target/crucible-model.json
    build-command: cargo build --locked
```

The configured command runs separately in both checkouts using each revision's
own dependency lockfile and pinned compiler. `CRUCIBLE_MODEL_OUT` is set to the
absolute model path; `crucible-build` writes the AST after generation succeeds.
Choose exactly one service package, for example `cargo build --locked -p
leyline-service`. Several generators exporting to one path can overwrite it.

If the baseline uses an older build helper, use a checked-in AST or a command
that assembles the AST explicitly. The new export hook cannot be applied to an
old dependency retroactively. Model and config paths are relative to
`working-directory`; that directory is relative to each checkout.

The command runs in an ordinary read-only `pull_request` job. It needs any
service-specific build prerequisites and private dependency authentication the
service normally uses in CI. The checker checkout token is not persisted for
the service build. For custom prerequisites, use the composite actions directly
in your existing CI job. Fork PRs cannot receive repository secrets.

## Configuring policy

The proposed revision's required `crucible.toml` selects compatibility severity,
wire checks, SDK symbol checks, and exact finding exceptions. See
[Crucible API policy](https://github.com/Operation-Leyline/crucible/blob/claude/api-policy/docs/api-policy.md)
for build integration and the schema.

```toml
schema_version = 1
[compatibility]
severity = "error"
wire = true
sdk_source = true
```

Wire compatibility initially covers Crucible JSON and AWS restJson1.
Unsupported advanced features are explicit `compatibility.unchecked` findings.
Public-symbol checks are conservative model-level checks; use native consumer
tests for target-language source/ABI guarantees. Exceptions remain visible.

The job summary contains every finding and its exception reason. The
`api-diff-report` artifact preserves `report.json`, `report.md`, and checker
stderr. Preparation failures leave `failure.txt` when the action has started.

## Using the composite actions

`setup-crucible` accepts `crucible-ref`, optional `token`, and optional
`toolchain`; it returns the absolute `cli` path. It installs Rust, caches Cargo
dependencies, and compiles the pinned CLI under `crucible-tooling/target`.
Reserve the `crucible-tooling` checkout directory in your workspace.

`api-diff` accepts a `cli` path plus `baseline-directory` and
`proposed-directory`. It also accepts the workflow's model/config/build options,
an optional `service` shape ID, and `report-path`. It returns the absolute report
directory. Python 3.9+ and Bash must be available. The reusable workflow supplies
these on Ubuntu; custom callers provision their own environment.

## Optional reusable workflow

`.github/workflows/api-diff.yml` provides a reusable workflow for callers that
already have GitHub's private workflow sharing enabled within the supported
organization or enterprise scope. It accepts `baseline-ref`, the model/build
options, a pinned `crucible-ref`, and optional secret `CRUCIBLE_READ_TOKEN` for
the checker checkout. Outside a PR event, `baseline-ref` is required.

An App token does not grant access to a private remote `jobs.<job>.uses` workflow;
GitHub resolves that workflow before any token-generation step runs. Use the
authenticated checkout template for consumers across separate organizations.
This implementation does not enable organization-wide Actions sharing.

## Validating changes

```sh
python -m unittest discover -s tests -v
CRUCIBLE_CLI=/absolute/path/to/crucible python -m unittest discover -s tests -v
actionlint .github/workflows/api-diff.yml .github/workflows/ci.yml templates/api-diff.yml
```

The second command also runs the real checker against an unchanged contract
and an intentional HTTP path break. Unit tests cover path containment, build
export configuration, missing models, escaped summaries, and preserving failing
reports. Make changes on `claude/<slug>` branches and land them by pull request.
