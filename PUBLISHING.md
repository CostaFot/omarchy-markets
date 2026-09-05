# Publishing to the Omarchy plugin marketplace

Written 2026-09-04. Status: **listed since 2026-09-04** (#4546,
`approved-and-verified` by HANCORE-linux, snapshot pinned at `5f0a69a` =
v1.0.1). Listing: https://plugins.omarchy.org/plugin.html?id=costafot.markets
(`omarchyplugins.com` redirects there). This mirrors what was done for
`costafot.clippy`, `costafot.autoduck` and `costafot.yeet`; Clippy's
`~/Work/omarchy-inappropriate-clippy/PUBLISHING.md` has the long version.

## The flow (https://plugins.omarchy.org/publish.html)

Marketplace repo: https://github.com/omacom/omarchy-plugin-marketplace
(docs there: `SUBMISSION.md`, `SECURITY.md`, `VERIFICATION.md`).

1. Repo prep: root `manifest.json`, README with install **and remove**
   commands, `LICENSE`, root `preview.png` (16:9; the marketplace makes the
   card and detail images from it). Public repo, pushed, a GitHub release
   per version (the listing links `releases/latest`).
2. `omarchy plugin validate ~/Work/omarchy-markets` on the real checkout,
   not the symlink in `~/.config/omarchy/plugins`.
3. First submission: the "Submit a plugin" issue form, title
   `[Plugin]: <name>`. Later releases: the "Verify plugin" form
   (`issues/new?template=verify-plugin.yml`, action "Verify and publish a
   newer upstream commit") with the full 40-character target SHA.
4. Two bots comment on the issue: "Marketplace validation" (structure and
   Quattro compatibility at the exact commit) and "Automated security
   baseline" (`passed`, `review-required` or `needs-fixes`). Editing the
   issue body re-runs them; a comment or a push triggers nothing.
5. A maintainer reviews by hand and labels `approved-and-verified`. The
   publication bot runs separately, in batches: it labels `listed`,
   comments the URL and closes the issue.

## The branch rule

**`main` is the marketplace.** The listing pins a commit, but the badge
follows HEAD of the default branch: the catalog re-validates every new HEAD
on its own, and any HEAD newer than the verified commit shows as
"Update unverified" until the next Verify issue is approved, docs-only
commits included. While a Verify issue is open, a push breaks the approval
outright (`update-upstream-changed`: HEAD must still be the target commit).

So `main` sits on the last release tag and moves only at release time.
Everything between releases is committed on `next`, which can be pushed
freely. A release is one sitting: merge `next` into `main`, bump
`manifest.json` and the CHANGELOG, tag, push `main`, `gh release create`,
then file the Verify issue at that HEAD and record its number below. If a
fix lands while the Verify issue is still unreviewed, edit its Target
commit rather than pushing after approval.

## What the maintainer reads

The 1.0.1 review (HANCORE-linux, 2026-09-04) checked, and the next one
will check again: the helper is started as `/bin/sh` + `/usr/bin/python3`
with positional arguments, never a PATH-resolved interpreter or shell
interpolation; `http.py` refuses redirects, caps bodies, bounds sockets,
total time and retries, honours `Retry-After` and redacts key parameters
from debug output; provider URLs are fixed HTTPS with encoded components;
keys travel in headers, never argv or URLs; every QML `Text` is
`Text.PlainText`. The one remark was the missing whole-process deadline
around the helper (a hardening concern, not a blocker); `next` has it
since 2026-09-05 (the helper's alarm plus the store's SIGTERM/SIGKILL),
so the next Verify issue can point at it. Disclose network use in the maintainer notes rather than let
the baseline find it: Yahoo (unofficial), CoinGecko, Frankfurter, all
keyless, and the Settings page's writes to the plugin's own shell.json
entry.

## Submission log

- 2026-09-03: #4546 filed at v1.0.0 (`2cd78ca`), category Widgets, tags
  bar + quickshell, suggested "finance". Validation ✅, baseline ✅. The
  same night the maintainer blocked #4216 (another finance widget) on
  `/usr/bin/env python3`, so v1.0.1 (`5f0a69a`) switched the helper launch
  and shebang to `/usr/bin/python3`; the issue body was edited to the new
  SHA, both bots green again at that commit.
- 2026-09-04 18:48 UTC: `approved-and-verified` at `5f0a69a`, clean
  review, the deadline remark above. 20:22 UTC: published by the bot,
  `listed`, closed. The catalog entry carries
  `verificationCoverage: snapshot-verified` at that commit.
