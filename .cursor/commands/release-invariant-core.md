# Cut an `invariant-core` release

Follow the full normative checklist in @AGENTS.md (section **Release process**). Use this command when the user wants to ship a new semver (for example `0.4.0`) or adjust the documented workflow.

## What to do

1. Run **`uv run pytest tests/`** and fix failures before changing versions.
2. Set **`[project].version`** in `pyproject.toml` to the **stable** release (no `.dev` suffix).
3. Run **`uv lock --refresh`** and confirm `uv.lock` shows the same version under `[[package]]` / `name = "invariant-core"`.
4. Commit with title **`chore: release vX.Y.Z`** and a commit body summarizing user-visible changes since the last tag.
5. Create git tag **`vX.Y.Z`** on that commit (lightweight is fine; match existing tags).
6. **Post-release commit:** bump `pyproject.toml` to the next dev line (e.g. `0.5.0.dev0` after `0.4.0`), run **`uv lock --refresh`** again, commit with **`chore: bump to development release …`**.
7. **Build PyPI artifacts from the tag**, not from `main` with a dev version:

   ```bash
   git checkout "vX.Y.Z"
   rm -f dist/invariant_core-*
   uv build
   git checkout -
   ```

8. **Do not** `git push` or publish to PyPI unless the user explicitly asks and can authenticate.

## Notes

- Package distribution name is **`invariant-core`**; wheels use **`invariant_core-…`** with underscores.
- If the user only asked to refresh the lockfile after a manual version edit, **`uv lock --refresh`** alone is enough.
