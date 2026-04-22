# Contributing

Thanks for your interest in `qr23mf`. This repository uses a trunk-based workflow rooted at `main`; every change lands through a pull request.

## Branch protection rules on `main`

The `main` branch is protected. Direct pushes are rejected by GitHub with `GH006: Protected branch update failed`. The active rules are:

- Pull request required for every change (enforced on admins — no bypass).
- `0` required approvals, so a solo maintainer can merge their own PR.
- Force-pushes and branch deletions disabled.
- Status checks not required at the moment (`task check` is still the local gate).

## Typical change flow

```bash
# 1. Sync local main.
git checkout main
git pull

# 2. Create a feature branch off main.
#    Use a short, descriptive prefix: feat/, fix/, docs/, chore/, refactor/.
git checkout -b feat/short-name

# 3. Make changes, then run the full gate before pushing.
task check          # ruff format-check + lint + mypy strict + tests + 85% coverage

# 4. Commit + push the branch.
git commit -am "feat: short human-readable summary"
git push -u origin feat/short-name

# 5. Open a pull request against main.
gh pr create --fill

# 6. Merge once the PR is ready. Squash is the default idiom here so main
#    keeps a clean, release-oriented history; --delete-branch tidies up.
gh pr merge --squash --delete-branch

# 7. Pull the merged commit back into local main.
git checkout main
git pull
```

## Release mechanics (unchanged)

After the PR that bumps `pyproject.toml` and `uv.lock` merges into `main`:

```bash
git pull
git tag -a v<MAJOR.MINOR.PATCH> -m "v<…> — short summary"
git push origin v<…>
gh release create v<…> --title "v<…> — short summary" --notes-file <notes>.md
```

The `qr23mf gui` **Check for updates** button (v1.10.0+) reads the latest GitHub release tag, so pushing the release is what makes the new version discoverable to end users.

## Commit messages

- One-line summary under ~72 chars, lowercase `type(scope): …` prefix: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`.
- Longer body is welcome; wrap it at ~100 cols to match `ruff`'s line length for source files.
- Use actual Unicode punctuation (em-dashes, ellipses) in commit bodies rather than escape sequences — zsh's `$'…'` interprets `\uXXXX`, bash's `"…"` does not.

## Local gate

Install the dev dependencies once with `uv sync --all-extras`, then:

```bash
task check          # format-check + lint + mypy --strict + pytest + 85% coverage gate
task fmt            # format (ruff format) in place
task test           # pytest only, no coverage
```

All tasks invoke `uv run …` under the hood, so you don't need to activate a venv manually.

## Emergency bypass

If protection ever needs to be disabled for a one-off fix (don't do this lightly):

```bash
gh api --method DELETE repos/demiurge28/3mf-qr-code-generator/branches/main/protection
# … push the fix …
# re-apply protection with the same JSON body used in the original `gh api PUT`.
```

Re-enable immediately after the hotfix lands so the default stays PR-required.
