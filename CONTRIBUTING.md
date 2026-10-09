# Contributing to Test Data Service

Thanks for your interest in contributing!

## Development setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/testsmith-io/test-data-service.git
   cd test-data-service
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

3. **Install in editable mode with dev extras**
   ```bash
   pip install -e ".[dev]"
   ```

## Running the app

```bash
tds serve                # or: python -m testdataservice serve
# with a tds.yml in the project root, just `tds serve`
```

## Tests, formatting, and linting

```bash
pytest                                   # test suite
black src/ tests/                        # format
flake8 src/ tests/ --max-line-length=100 --extend-ignore=E203,W503
mypy src/testdataservice                 # type check
```

Please add or update tests for any behavior change, and keep `pytest` green.

## Releasing

Releases publish to PyPI automatically when a GitHub Release is published
(see `.github/workflows/publish.yml`). To cut a release:

1. Bump `version` in `pyproject.toml`.
2. Commit and tag (`git tag vX.Y.Z && git push --tags`).
3. Publish a GitHub Release for that tag.

The workflow builds the sdist/wheel, runs `twine check`, and uploads using the
`PYPI_API_TOKEN` repository secret. You can also dry-run to Test PyPI via the
workflow's manual dispatch.
