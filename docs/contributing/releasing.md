# Releasing

A release publishes two artifacts from one commit: the `django-crm` wheel to PyPI, and the `bottlecrm-frontend` image to GHCR. They carry the same version because "self-hosting 1.4.2" has to name one commit rather than an API version and whatever the UI happened to be.

## The whole process

Bump `version` in `backend/pyproject.toml`, open a PR, merge it.

That is the end of the list. Nothing else is typed, and in particular **you do not create the tag**.

On merge to `master`, `.github/workflows/tag-release.yml` reads the version back out of `backend/pyproject.toml`, creates tag `v<version>` on the commit that carried the bump, publishes a GitHub Release with generated notes, and starts `publish.yml`.

`backend/pyproject.toml` is the only place the version is written. Deriving the tag from it rather than typing it is the point: the two cannot disagree if only one of them is ever authored.

!!! tip "Releasing a version already on master"
    Run **Tag release** from the Actions tab. It re-reads the file and releases whatever it says, so a version that predates this workflow can still be cut without an empty commit.

!!! warning "Merging the bump *is* the release"
    There is no separate "publish" button to hold. If you want to land work now and release it later, keep the version bump out of that PR and open a one-line PR when you are ready.

## What this version does and does not cover

`backend/pyproject.toml` versions **the server and the web UI**: the PyPI wheel and the GHCR frontend image are built from one tag so that "self-hosting 1.4.0" names a single commit.

Two versions in this repo are deliberately not part of it:

- **`mobile/pubspec.yaml`** is the Flutter app's own version, and it ships through the Play Store and the App Store on its own schedule. It is not bumped here and does not track this number. It has drifted behind before, so do not read the app's version as the API's.
- **`frontend/package.json`** is `0.0.1` and always has been. The web UI is not published to npm; it ships as the image tagged from the git tag, so nothing reads that field.

## What runs, in order

`publish.yml` is a chain, and every link can stop the release. Nothing is published until all of them pass, because PyPI refuses a second upload of a version that already exists: a wrong artifact cannot be replaced, only yanked and superseded.

| Job | What it proves |
| --- | --- |
| `test` | The commit being shipped passes the suite, and no model change is missing a migration. |
| `build` | The tag matches the packaged version. The wheel and sdist build, the README renders on PyPI, and the wheel starts Django and resolves its templates **from a clean environment outside the source tree**. |
| `publish` | Uploads to PyPI over OIDC. Tag refs only. |
| `frontend-image` | Builds and pushes the GHCR image. Tag refs only, and gated on `publish`. |

Two of those gates exist because of specific incidents and are worth understanding before you change them.

**The wheel is started from `$RUNNER_TEMP`, not from the checkout.** Inside the source tree every app imports from the working directory, which is how two `INSTALLED_APPS` once shipped missing from the wheel with a fully green suite. Running from a neutral directory is what makes the smoke test able to fail.

**`frontend-image` needs `publish`, not just `build`.** The two artifacts are one release. `v1.3.0` built fine, failed the PyPI OIDC exchange, and still pushed `ghcr.io/django-crm/bottlecrm-frontend:1.3.0` **and `:latest`** with nothing on PyPI to match. `latest` is the damaging half, because it moves.

## Rehearsing without publishing

Run **Publish to PyPI** from the Actions tab against a branch. `publish` and `frontend-image` are both gated on a tag ref, so both skip, and everything before them runs for real: the build, the README render check, and the clean-environment install.

The version-guard step says in its own output that it skipped the tag comparison, because there is no tag to compare against. That is deliberate. A skipped guard and a passing one look identical in the Actions UI, and the point of a rehearsal is knowing which checks it actually ran.

## What the release gate does not cover

- **The PostgreSQL corner.** `publish.yml` runs the SQLite suite. The non-superuser role and the RLS
  isolation pass live in `tests.yml` and are not repeated on the tag, so releasing assumes that job was green on `master`. Check it before you bump the version. See [Testing](testing.md).
- **The frontend suite.** `frontend-checks` likewise runs on the branch, not on the tag.
- **Anything the wheel does not contain.** The smoke test starts Django and loads seven templates. It is a packaging check, not a functional one.

## Version numbers

[PEP 440](https://peps.python.org/pep-0440/), which is what decides whether the GitHub Release isbmarked as a pre-release. `1.4.0rc1` is a pre-release; `1.4.0.post1` is not. The workflow asks `packaging` rather than looking for a substring, because getting this wrong shows a release candidate to everyone watching the repo as though it were final.

Tags created from now on are `v`-prefixed. Tags before `v1.3.0` are not (`1.2`, `1.1`, `0.9.0`), so both spellings still trigger `publish.yml` and `tag-release.yml` checks both before deciding a version is unreleased.

## When something goes wrong

**"No release: a tag for X already exists."** The workflow found the tag and stopped. If that tag is a leftover from an abandoned release, delete both it and its GitHub Release, then re-run **Tag release**. This warning is the reason that branch is loud rather than silently green: without it,
bumping to a version whose tag was left behind publishes nothing and looks like success.

**The tag and the packaged version disagree.** Only reachable via a hand-pushed tag. Delete the tag and the Release, fix `backend/pyproject.toml`, and let `tag-release.yml` do it.

**A bad version reached PyPI.** It cannot be replaced. Yank it on PyPI and release a new patch version. This is the failure every gate above is arranged to prevent.

## Changing the publishing setup

Publishing uses a PyPI Trusted Publisher (OIDC), so there is no API token in this repository. The trust is bound to four things, and PyPI checks all of them:

```
owner:        Django-CRM
repository:   Django-CRM
workflow:     publish.yml     <- the FILENAME is part of the trust
environment:  pypi            <- and so is the environment name
```

Renaming `publish.yml` or the `pypi` environment silently breaks publishing: PyPI rejects the token because the claims no longer match. Change it on PyPI first.

Two more things in that file are load-bearing and easy to "tidy" into a vulnerability:

- **`pypa/gh-action-pypi-publish` is pinned to a commit SHA**, not to `v1`. A moving tag means whatever upstream repoints it to runs inside the job holding the token that can publish as us.
  `tests.yml` can afford tags. That job cannot. Bump the SHA and its comment together.
- **`enable-cache: false` on the `build` job** is not a performance choice. The Actions cache is writable from less-trusted contexts, so a cache entry is an input to the artifact that gets uploaded to PyPI under the project's name. Every other job caches; that one must not. Note that an explicit `true` would be worse than the `auto` default here, not better: setup-uv reads `true` before it tests the event, so it opts out of v10's cache-poisoning protection entirely.

## Bumping the actions

One trap, because it fails at run time rather than at review time: **`astral-sh/setup-uv` has no major or minor tags.** They were removed in v8, so `astral-sh/setup-uv@v10` does not resolve and the step errors. Every reference to it must be a full version (`@v10.0.1`). The `actions/*` and `pnpm/*` actions do publish moving major tags, and are referenced that way.

`pypa/gh-action-pypi-publish` is the exception in the other direction: pin the SHA, never a tag. See above for why.
