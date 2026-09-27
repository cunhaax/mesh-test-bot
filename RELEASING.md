# Releasing

Releases are grouped, not one per commit: cut one when there's a meaningful batch of
changes to announce, not on every push.

## Version number

[SemVer](https://semver.org): `MAJOR.MINOR.PATCH`.

- **PATCH** — fixes, docs, no behavior change.
- **MINOR** — new features, backward-compatible.
- **MAJOR** — breaking change (removed/renamed config option, a default that changes
  existing scheduled behavior, a change to the on-air message format, a removed flag).

While still `0.x`, a breaking change bumps **MINOR** instead of MAJOR (`0.3.0` →
`0.4.0`) — that's what `0.x` means: no stability promise yet. Move to `1.0.0`
deliberately, when ready to commit to not breaking configs carelessly.

## Cutting one

```sh
git tag v0.2.0
git push origin v0.2.0
```

The tag alone triggers the image workflow, which publishes `ghcr.io/cunhaax/mesh-test-bot:0.2.0`
and `:0.2` (`:latest` stays on `master`, updated by every push there).

Then draft the release notes from the actual commits since the last tag:

```sh
gh release create v0.2.0 --generate-notes
```

Review the generated notes before publishing — trim anything that isn't relevant to
someone running the bot (internal refactors, test-only changes).

## After the first tag

History from that point on is permanent: **no more squashing or force-pushing
`master`.** A rewritten commit after a tag exists breaks that tag's ancestry and makes
its release notes meaningless.

## Pulling a bad release

Deleting a Release and deleting the Docker image it published are two separate
things — neither happens automatically with the other.

```sh
gh release delete v0.2.0 --cleanup-tag   # the GitHub Release + the git tag
```

`--cleanup-tag` matters: without it, the tag still exists, and `git checkout v0.2.0`
still works. The published image (`ghcr.io/cunhaax/mesh-test-bot:0.2.0` and `:0.2`)
is untouched by any of this — delete that version separately, under the repo's
Packages tab (or `gh api -X DELETE /user/packages/container/mesh-test-bot/versions/<id>`,
find `<id>` by listing versions first).

This only stops *new* pulls of that exact version tag. `:latest` and any `:X.Y` a
later push has since overwritten are already safe; anyone who pulled `:0.2.0` before
you deleted it keeps their copy regardless.

For anything more than a minor mistake (wrong notes, an accidental early tag): prefer
publishing a fixed **PATCH** release over silently deleting the bad one, and for an
actual security issue, use GitHub's Security Advisories to document it — that's more
transparent than making a version quietly disappear, especially once the repo is
public.
