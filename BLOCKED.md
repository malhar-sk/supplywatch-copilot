# BLOCKED

## ~~CI workflow could not be pushed~~ RESOLVED 2026-11-23: token given workflow scope, file moved to `.github/workflows/ci.yml`

- **What:** GitHub rejected the push: `refusing to allow a Personal Access Token to create or update
  workflow '.github/workflows/ci.yml' without 'workflow' scope`. The build host's `gh` token only has
  `repo` scope, and SSH to GitHub isn't set up (`Permission denied (publickey)`).
- **What I tried:** (1) `gh repo create ... --push` (rejected); (2) SSH push (no key); (3) relocating
  the file. Local history was rewritten **before the first push** (the remote was empty, so nothing
  was force-pushed) to keep the workflow at `ci/github-workflows/ci.yml`. The original local history,
  with `.github/workflows/ci.yml`, is kept in the local-only branch `backup/original-with-ci` on the
  build server.
- **The workflow is complete and unchanged.** It only needs to be in the right folder.
- **Next step for a human (one command, from a machine with a normal GitHub login):**
  ```bash
  mkdir -p .github/workflows
  git mv ci/github-workflows/ci.yml .github/workflows/ci.yml && git commit -m "Enable CI" && git push
  ```
  (Or on github.com: Add file → create `.github/workflows/ci.yml` and paste the contents.)
