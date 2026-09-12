# Contributing to BensPDF

Everything about working on the code. For what the tools do and how to install
them, see the [README](README.md); for what each tool returns, the
[tool reference](docs/tools.md).

## Getting set up

```bash
git clone https://github.com/benbergner/BensPDF.git
cd BensPDF

conda env create -f environment.yml
conda activate benspdf
pip install -e .

python -m pytest tests/ -v
```

## Layout

`src/` holds two packages. `benspdf` is the PDF side: the server
(`mcp_server.py`), one module per tool under `tools/`, a client that talks to the
server over stdio (`mcp_client.py`), the Ollama chat CLI built on that client
(`cli.py`), and model selection (`models.py`). `benscore` is the shared artifact
layer underneath, and knows nothing about PDFs.

Both ship in this one distribution today. `benscore` is separate because a later
package for another file type needs the same scratch folder and the same result
shape, and because nothing domain-specific belongs in a name that a second domain
will have to import.

## Adding a tool

Adding a tool means two things:

1. The logic goes in `src/benspdf/tools/<verb>.py`, named after the verb, and its
   public name gets listed in `src/benspdf/__init__.py`.
2. A thin wrapper in `src/benspdf/mcp_server.py`, decorated with `@mcp.tool()`,
   resolves the `ref` and calls it.

Keeping those separate means the logic is testable without going through MCP. The
wrapper's type hints and docstring become the schema the model sees, so the
docstring is worth writing carefully. `pdf_metadata` over `tools/metadata.py` is
the pattern to copy. Restart the server in your client afterwards to pick up the
change.

Descriptions are context the model pays for on every turn, so keep them to what
is specific to the tool: the question it answers, what it does not do and what to
use instead, and any field whose name doesn't explain it. Skip the list of
returned fields — results are self-describing dicts. Anything shared by all tools
goes in `INSTRUCTIONS` in `mcp_server.py`, which the server sends once.

Tools that take a page range share one parser, `tools/page_spec.py`, so `"1-20"`,
`"3"`, `"1,5,9-12"` and `"all"` mean the same thing everywhere and the error
messages match.

If a tool produces a file, use the helpers in `src/benscore/` instead of writing
to disk yourself:

- `benscore.resolve(ref)` takes a file path or a scratch id and gives you a path to read
- `benscore.save(data, ".pdf")` stores a result and returns its id
- `benscore.artifact_path(id)` gives the path to report alongside that id, so the
  user can open the file without knowing where the scratch folder is
- `benscore.ok(...)` and `benscore.err(...)` keep the result shape the same across tools

`create_test_pdf_file` in `mcp_server.py` is a short working example.

## Running the checks the way CI does

CI installs `.[dev]` and nothing else — in particular not the `ollama` extra. A
development environment that has more installed than that can pass a check CI
then fails, so when a CI failure will not reproduce, reproduce the environment:

```bash
python -m venv /tmp/ci && /tmp/ci/bin/pip install -e ".[dev]"
/tmp/ci/bin/python -m pytest tests/ -q
/tmp/ci/bin/python -m mypy src
/tmp/ci/bin/python -m black --target-version py311 --check src tests scripts
```

Optional dependencies are guarded at their import site and given a mypy
`ignore_missing_imports` override, so absent is a supported state rather than a
type error.

## Checking a build

`pytest` covers the source tree. It says nothing about the built package, which
can lose an entry point or a module and still pass every test. To check that,
build and drive the result the way a client does:

```bash
python -m build
python -m venv /tmp/check && /tmp/check/bin/pip install dist/*.whl
/tmp/check/bin/python scripts/verify_install.py /tmp/check/bin/benspdf-mcp
```

That completes the handshake, checks the expected tools are all present, chains
two calls, and confirms a missing file comes back as an error rather than an
exception. The same script runs in CI on every push, and again against TestPyPI
during a release rehearsal. Point it at any installed copy:

```bash
scripts/verify_install.py benspdf-mcp
```

## Releasing

Three workflows under `.github/workflows/`:

- `ci.yml` — on every push and pull request. Tests, mypy and black on Python
  3.11, 3.12 and 3.13, then builds a wheel and verifies the installed result.
- `dry-run.yml` — manual, from the Actions tab. A full rehearsal against
  TestPyPI: builds `<version>.devN`, uploads, installs it back down, and runs
  `scripts/verify_install.py` against it.
- `release.yml` — on a published GitHub Release. The real upload.

Both uploads use [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/):
GitHub vouches for the workflow run, PyPI issues a short-lived token in exchange.
No API token is stored in the repository.

### One-time setup

Each index needs a publisher rule, created at
`https://pypi.org/manage/account/publishing/` and the same page on
`test.pypi.org`. Before the project exists on an index, this is a *pending*
publisher. Every field is exact-match:

| Field | PyPI | TestPyPI |
| --- | --- | --- |
| Project name | `benspdf-mcp` | `benspdf-mcp` |
| Owner | `benbergner` | `benbergner` |
| Repository | `BensPDF` | `BensPDF` |
| Workflow | `release.yml` | `dry-run.yml` |
| Environment | `pypi` | `testpypi` |

The GitHub environments are created on first use; nothing to configure there.

### Cutting a release

A version number is consumed permanently the first time it is uploaded. A bad
`0.1.0` cannot be replaced, only abandoned in favour of `0.1.1`. Hence the
rehearsal.

1. Rehearse. Actions → **Dry run to TestPyPI** → Run workflow, with a `dev_suffix`
   you haven't used before (`dev1`, `dev2`, ...). TestPyPI enforces the same
   write-once rule, so the suffix has to change on every run.
2. Read the job summary. It should report the version it uploaded and verified.
3. Bump `version` in `pyproject.toml` if this release needs a new number, and
   commit.
4. Releases → Draft a new release → tag `v<version>` → Publish.

`release.yml` tests, builds, runs `twine check --strict`, and refuses to upload if
the release tag and the version in `pyproject.toml` disagree — otherwise a
mistyped tag silently ships the wrong code under the right name.

### Afterwards

Check the published artifact the way a user reaches it, not the way CI does. The
verifier takes the launcher as written, so it can drive the README's own command:

```bash
python scripts/verify_install.py uvx --no-cache benspdf-mcp
```

`--no-cache` forces a resolve against PyPI. Without it a local cache entry can
make a broken publish look fine. Expect around 10 seconds cold, 2 warm.

Then add the server to one real client from the README and ask it something about
a PDF. Until that happens, every config block in the README is an untested claim.

Don't reach for `uv cache clean <package>` to get a cold run — it scans the whole
cache and can take minutes on a large one. `--no-cache` bypasses instead of
clearing, and leaves the cache intact.
