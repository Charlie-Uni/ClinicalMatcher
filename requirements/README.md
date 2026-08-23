# Dependency policy

The reproducible public environment is deliberately separate from model
training and historical experiments.

## Public runtime and tests

- Supported interpreter: CPython 3.11.x. CI currently pins 3.11.16.
- Lock tool: `uv` 0.12.5.
- Lock: `public-py311.lock`, compiled universally with distribution hashes.
- Inputs: the base dependencies in `pyproject.toml` plus
  `public-build.in`, which mirrors the setuptools build-backend requirement.

Create the environment from a clean clone:

```bash
uv venv --python 3.11
uv pip sync --python .venv/bin/python --require-hashes --strict requirements/public-py311.lock
uv pip install --python .venv/bin/python --no-deps --no-build-isolation --reinstall .
uv run --no-sync python -m unittest discover -s tests -v
```

The final two commands are one verification sequence: reinstall the current
worktree immediately before every local test run. Running the test command
alone after a source change can silently exercise a stale wheel snapshot.

On Windows, use `.venv\\Scripts\\python.exe` instead of
`.venv/bin/python` in the two `uv pip` commands.

The normal-wheel reinstall is intentional. On macOS, a file-provider-managed
folder can propagate the Finder `hidden` flag into `.venv`, causing Python to
skip editable-install `.pth` files. Reinstalling the worktree before tests
avoids that platform-specific failure and prevents a previously installed
wheel from being mistaken for current-source verification. It changes only
the local project install mode; CI and the dependency lock remain unchanged.

After reviewing a public dependency change, regenerate the lock with exactly:

```bash
uv pip compile pyproject.toml requirements/public-build.in --python-version 3.11 --universal --generate-hashes --no-emit-package clinical-matcher --output-file requirements/public-py311.lock
```

CI repeats that command without `--upgrade` and fails if it changes the
committed lock. Dependency upgrades must therefore be intentional and
reviewed.

## Environments intentionally excluded

- `legacy/apixaban/requirements-legacy.txt` documents the historical
  prototype only; it is neither a lock nor a supported environment.
- The first P5 real-training environment is local MLX/MLX-LM and is recorded in
  the reviewed exact-version `requirements-mlx.txt` at the repository root,
  plus its model/conversion/run manifest. Recreate the separate local
  mechanism environment with:

  ```bash
  uv venv --python 3.11.16 artifacts/venvs/mlx-p5
  uv pip sync --python artifacts/venvs/mlx-p5/bin/python requirements-mlx.txt
  uv pip install --python artifacts/venvs/mlx-p5/bin/python --no-deps --no-build-isolation --reinstall .
  ```

  The environment path is ignored through `artifacts/`; the requirements file
  contains no model, credential, or restricted-data reference. The exact
  `setuptools` pin exists only so the separate environment can reinstall the
  current first-party wheel without an unpinned build-isolation download.
  Optional
  MedicalGPT/PEFT/CUDA synthetic compatibility work remains separate and pins
  only the dependencies it actually uses.
- Optional dense and semantic-scan dependencies remain opt-in and are not part
  of the lightweight public CI lock.

Never add restricted data paths, credentials, model weights, or patient-level
artifacts to any dependency or environment file.
