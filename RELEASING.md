# Releasing

Releases are published to PyPI by `.github/workflows/release.yml` using
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/): GitHub
Actions authenticates to PyPI over OIDC, so no API token is stored anywhere.

## One-time setup

1. **PyPI pending publisher.** Signed in at <https://pypi.org>, go to
   *Your account → Publishing → Add a new pending publisher* and enter:

   | Field | Value |
   |---|---|
   | PyPI project name | `librarian-searxng` |
   | Owner | `kevin-j-smith` |
   | Repository name | `librarian-searxng` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   The project is created on PyPI by the first successful upload.

2. **TestPyPI (optional, for dry runs).** Do the same at
   <https://test.pypi.org> with environment name `testpypi`. TestPyPI is a
   separate site and needs its own account.

3. **GitHub environments.** In the repository's *Settings → Environments*,
   create `pypi` (and `testpypi` if you did step 2). For `pypi`, consider
   adding yourself as a required reviewer so every publish waits for a
   manual approval.

## Each release

1. Update `__version__` in `src/librarian_searxng/__init__.py`. It is the
   single source of the package version.
2. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new version
   heading and update the compare links at the bottom.
3. Check locally:

   ```bash
   uv build
   uvx twine check --strict dist/*
   ```

4. Optional dry run: *Actions → Release → Run workflow* on `main`. This runs
   the tests, builds the package, and publishes to TestPyPI. TestPyPI refuses
   to accept the same version twice.
5. Commit, then tag and push:

   ```bash
   git tag v0.1.0
   git push origin main v0.1.0
   ```

   The workflow runs the tests, checks that the tag matches `__version__`,
   and publishes to PyPI.

A published version can't be replaced. PyPI lets you *yank* a version, but
never upload the same version number again, so fix mistakes by releasing a
new version.
