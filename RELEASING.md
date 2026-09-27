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
