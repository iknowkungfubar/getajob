# Branch Protection — `main`

> **These settings must be configured manually in the GitHub repository UI.**
> Go to **Settings → Branches → Add branch protection rule** (or edit the existing rule for `main`).

---

## Required Settings

| Setting | Value | Why |
|---------|-------|-----|
| **Branch name pattern** | `main` | Protect the default branch. |
| **Require a pull request before merging** | ✅ On | No direct pushes to `main`. |
| **Require approvals** | `1` | At least one reviewer must approve. (Increase for production.) |
| **Dismiss stale pull request approvals when new commits are pushed** | ✅ On | Prevents out-of-date reviews from counting. |
| **Require review from Code Owners** | ✅ On | If `CODEOWNERS` is defined, those reviewers are required. |
| **Require status checks to pass before merging** | ✅ On | CI must be green before merge is allowed. |
| **Require branches to be up to date before merging** | ✅ On | The PR branch must be based on the latest `main`. Prevents merge skew. |
| **Status checks that must pass** | See below | Every CI job must report success. |

---

## Required Status Checks

Add each of the following as a required check (case-sensitive):

| Check Name | Source Job | Approx. Duration | Notes |
|-----------|-----------|-------------------|-------|
| `Lint (ruff)` | `.github/workflows/ci.yml` → `lint` | ~2 min | Ruff formatting + linting |
| `Type Check (mypy)` | `ci.yml` → `typecheck` | ~5 min | Strict mypy |
| `Tests (pytest)` | `ci.yml` → `test` | ~8 min | Full test suite with coverage |
| `Security Scan` | `ci.yml` → `security` | ~5 min | Secrets, bandit, pip-audit |
| `CodeQL Analysis` | `ci.yml` → `codeql` | ~15 min | CodeQL security-and-quality |

---

## Recommended Additional Settings

| Setting | Recommended | Why |
|---------|-------------|-----|
| **Allow auto-merge** | ✅ On | Lets Dependabot merge automatically once checks pass. |
| **Allow force pushes** | ❌ Off | Prevents history rewriting on `main`. |
| **Allow deletions** | ❌ Off | Prevents branch deletion on `main`. |
| **Include administrators** | ✅ On | Admins also require PRs — prevents bypass. |
| **Lock branch** | ❌ Off | Only enable if `main` should be read-only for everyone. |

---

## Enabling Auto-Merge for Dependabot

After branch protection is configured:

1. Go to **Settings → Actions → General → Allow GitHub Actions to create and approve pull requests** → ✅ On.
2. Go to **Settings → Code security → Dependabot → Dependabot auto-merge**.
3. Enable auto-merge — PRs that pass all required checks will merge automatically.

For Dependabot grouping to work effectively (reducing PR noise), ensure the `group` configuration in `.github/dependabot.yml` is active. Minor/patch updates are grouped into a single PR; major bumps require manual review.

---

## Verification Checklist

After configuring, verify by creating a test PR:

- [ ] PR cannot be merged without CI passing.
- [ ] PR cannot be merged without at least one approval.
- [ ] PR must be rebased/merged with `main` if behind.
- [ ] Dependabot PRs auto-merge when checks pass (and `GETAJOB_SECURITY__ENCRYPTION_KEY` is available in CI secrets if Profile tests require it).

> **Note:** Dependabot PRs run CI from the workflow in the *target branch*, so the encryption key CI secret must be configured in the repository's **Settings → Secrets and variables → Actions** in order for auto-merge to succeed.
