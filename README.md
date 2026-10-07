# crucible-actions

Private shared GitHub Actions for Crucible services. API compatibility checks
accept the Smithy directories from two revisions. Naming, path, and documentation
rules remain build-time checks selected through `crucible-build`.

## Comparing Smithy directories

After checking out baseline and proposed revisions, run:

```yaml
- uses: ./crucible-actions/api-diff
  with:
    cli: ${{ steps.tooling.outputs.cli }}
    before: baseline/smithy
    after: proposed/smithy
    config: proposed/crucible.toml
```

`before`, `after`, and `config` are paths relative to `GITHUB_WORKSPACE`. They
must stay within that workspace. JSON AST files are accepted in place of source
directories. An optional `service` input selects a Smithy service ID.

The pinned Crucible CLI assembles both directories with its official Smithy
compiler, imports bundled Crucible traits and errors, and flattens mixins before
comparing the lowered APIs. Additional model dependencies must be present in
those source directories. Compilation uses temporary directories outside the
sources. No Cargo service build, lockfile, export script, or build command is
needed. Missing sources, invalid Smithy, or invalid policy fail the job.

The first assembly provisions a checksum-verified managed Java runtime; later
runs reuse its cache. `SMITHY_TOOLING_CACHE` selects the cache directory and
`SMITHY_OFFLINE=1` requires it to be populated. The checker revision must support
Smithy-directory inputs; the initial AST-only implementation does not.

## Private access across organizations

Copy [the caller template](templates/api-diff.yml) into your service repository.
It uses the existing [crucible-ci-read App](https://github.com/apps/crucible-ci-read)
to check out this private action repository, then invokes its composite actions
locally. Both tooling repositories remain private.

Set repository variable `CRUCIBLE_APP_ID` (currently `5189718`) and secret
`CRUCIBLE_APP_PRIVATE_KEY`. The App's Operation-Leyline installation must select
`crucible` and `crucible-actions` with Contents: read. The caller may belong to
another organization; token generation explicitly targets the Operation-Leyline
installation. The App need not be installed on each consumer organization.

Checkout disables credential persistence. The token is short-lived and revoked
at job completion. See the [token action documentation](https://github.com/actions/create-github-app-token)
for scoping. Fork PRs cannot receive repository secrets.

The template uses PR base/head SHAs. To preserve a released contract instead,
set the baseline checkout's ref to that tag or commit. Pin the shared actions
and checker independently to reviewed commits.

## Reports and policy

The proposed `crucible.toml` selects compatibility severity, wire checks,
model-level SDK symbol checks, and exact exceptions with reasons. Exceptions
remain visible. See the [API policy reference](https://github.com/Operation-Leyline/crucible/blob/main/docs/api-policy.md).

Wire checks cover Crucible JSON and AWS restJson1; unassessed features produce
explicit `compatibility.unchecked` findings. SDK symbol checks are conservative
model-level checks, not target-language ABI proofs.

The job summary shows each finding. The `api-diff-report` artifact preserves
JSON, Markdown, and checker diagnostics. Assembly/setup failures leave
`failure.txt` when the action has started. Breaking findings retain a nonzero
exit status. There is no first-run compatibility bypass.

## Tool setup and reusable workflow

`setup-crucible` accepts `crucible-ref`, optional checkout `token`, and optional
Rust `toolchain`; its `cli` output is the absolute executable path. Reserve the
`crucible-tooling` checkout directory. Python 3.9+ and Bash are required for the
composite action; the templates use Ubuntu runners.

The optional `.github/workflows/api-diff.yml` reusable workflow accepts a pinned
checker ref, `smithy-path`, `config-path`, optional `service`, and `baseline-ref`
(defaulting to the PR base SHA). It requires GitHub's native private workflow
sharing. An App token cannot authorize remote reusable-workflow resolution;
use the authenticated local-action caller template across organizations.

## Validation

```sh
python -m unittest discover -s tests -v
CRUCIBLE_CLI=/absolute/path/to/crucible python -m unittest discover -s tests -v
actionlint .github/workflows/api-diff.yml .github/workflows/ci.yml templates/api-diff.yml
```

The real-checker test uses raw Smithy directories without a Cargo project,
accepts identical models, rejects a changed HTTP path, and rejects malformed
Smithy. Unit checks cover path containment, missing sources, visible exceptions,
escaped summaries, and preserving failing reports.
