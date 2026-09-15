# CLAUDE.md

## Always test, merge and push

Standing instruction from the maintainer. For every change:

1. **Test** — `python3 scripts/validate-themes.py --check` and
   `./scripts/scan-secrets.sh`. Both must exit 0.
2. **Merge** — merge the work into `main`, resolving conflicts rather than
   leaving a branch dangling.
3. **Push** — push `main`. Don't stop to ask; this file is the permission.

Exceptions that still need a human: tests failing for a reason you cannot
fix (push nothing, say what broke), anything rewriting published history,
and deploy branches with unrelated history (`output`, `gh-pages`) which are
CI-written and must never be merged into `main`.

## The rule that keeps breaking main

`manifest.json` is **generated**. Never hand-edit it. Any change under
`themes/` — including adding a folder or editing one `theme.json` — means
re-running the validator and committing the regenerated manifest in the
same commit:

```bash
./scripts/validate-themes.py     # validates AND rewrites the manifest
git add themes/ manifest.json    # commit both together
```

CI runs `--check`, which fails if the manifest is stale. This has broken
`main` twice: once when `Borb's Lair` was added as a folder, and again when
an `effects` block was added to it. Adding a theme *looks* like a complete
change; it isn't.

Git also does not install hooks on clone. Every fresh clone needs:

```bash
ln -sf ../../scripts/pre-commit .git/hooks/pre-commit   # macOS/Linux
copy scripts\pre-commit .git\hooks\pre-commit           # Windows
```

## Other things worth knowing

- **Folder names are identity.** The client remembers a selection by folder
  name. Renaming or deleting a folder deselects that theme for everyone
  using it. To retire a theme, keep the folder, replace its contents, bump
  `version`.
- **Themes live in `themes/` only.** The validator reads nothing else, so a
  theme folder at the repo root is invisible to it and to the client. 17 such
  duplicates were removed; don't let them come back.
- **Contrast must be measured on the composite.** Every colour role except
  `bg` honours `#RRGGBBAA`, so text sits on `panel*alpha + backdrop*(1-alpha)`
  where `backdrop` is the 98th-percentile bright end of the dimmed image, not
  its average. `dim` is a straight sRGB multiply: `c' = c * (1 - dim)`.
- **Shallow clones lie.** `--depth 1` pins the fetch refspec to `main`, which
  makes pushed branches look unpushed and reports "no merge base". Run
  `git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'`
  and `git fetch --depth=1000` before trusting any ahead/behind count.
