# Release candidate: Card handoff removal + Ritanya browser engine

Branch `release/candidate-card-engine`. Status: **Preview candidate only. Not merged to `main`, nothing in Production changed.**
Test results are tied to the exact commit in the evidence report delivered with it; this file is the durable description.

## 1. Ancestry and order

```
bb2a5e3  main (Production)  = PWA release; already contains hotfix a9d6409 (encoded-path exposure fix)
 |
 +-- e8b8bfb  Correct public availability and Card wording (About, Contact)      \  feature/card-handoff-removal
 +-- 3d6a714  Remove the external Card handoff; show accurate on-domain status   /  (fast-forward of main)
 |
 +-- 158add0  Merge feature/ritanya-browser-engine (b3700d9)                     <- browser engine, merged after the Card branch
 |            (b3700d9 descends from main bb2a5e3 via feature/ritanya-integration; it also carries 84cc92a, the
 |             SAME About/Contact wording change as e8b8bfb with a different hash; git merged the identical hunks cleanly)
 +-- a75ffc9  Remove regex look-behind from the engine (older browsers)
 +-- 3e90a0f  Kill switch: bounded 5-minute grace period; unusable engine is never mounted
```

| Question | Answer (verified with `git merge-base` / `--is-ancestor`) |
|---|---|
| Is `main` (`bb2a5e3`) an ancestor of both feature branches? | Yes. Both branched directly from it. |
| Is the Card branch an ancestor of the engine branch? | **No.** They are siblings. Neither is fast-forwardable onto the other. |
| Do they stay fast-forwardable after the first merge? | **No.** After the Card branch is merged, the engine branch is a divergent sibling and needs a merge commit (done here, on the release branch only). The duplicated wording commits (`e8b8bfb` / `84cc92a`) are identical in content. |
| Is the encoded-path fix (`a9d6409`) in the candidate? | Yes, through `main` (`bb2a5e3`). Its tests run in the full suite. |
| Original branches and rollback points | All unchanged: `main` bb2a5e3, `feature/card-handoff-removal` 3d6a714, `feature/ritanya-browser-engine` b3700d9, `feature/ritanya-integration` 9a0845e, `hotfix/encoded-path-exposure` a9d6409. |

Order guaranteed on the release branch: (1) Card handoff removal and wording corrections, (2) browser engine, then its compatibility and
kill-switch fixes, (3) the encoded-path fix is inherited from `main`, (4) full regression verification.

## 2. Card behaviour (what rithavo.com does after this release)

- `POST /card/continue` answers **404** unless `RITHAVO_CARD_HANDOFF_ENABLED` is set. It is not set anywhere and defaults to off.
- No customer-facing page, script, form, redirect or response hands a visitor to another Rithavo host. `tests/test_single_domain.py`
  fails the build if one does.
- `/card/status` (read-only), the Profile and Explore keep working. The Home panel says "The Rithavo Card isn't available on rithavo.com";
  signed-in members who have a Card row also see "You have a Rithavo Card from an earlier step … can't be viewed or shared from here".
  It links to the on-domain Profile and never claims the Card is available.
- Ritanya's Card answer says the same ("isn't available on rithavo.com") and links only to relative on-domain pages.

### Existing Cards (documented separately; nothing was deleted, migrated or had its visibility changed)
- Cards live in the **sibling Profile service** at `app.rithavo.com`, not in this repository. Observed read-only in Production earlier in this
  work: **5 Cards have a page slug (`/c/{slug}`, sign-in required) and 2 are listed in Discover**; none shows a public email or phone.
- After this release those pages keep working on `app.rithavo.com`. rithavo.com no longer links to them, and a member can no longer reach
  Card management from rithavo.com. Whether to disclose, take down or migrate them is a founder decision (see the privacy review).
  Disabling the handoff did not touch any Card data.

## 3. Marketing copy that still describes the Card (NOT changed; a positioning decision)

| Where | Current text | Issue | Suggested precise wording (for approval) |
|---|---|---|---|
| `index.html` hero visual (decorative, `aria-hidden`) | label "Rithavo Card", value "Ready to share" | States a capability the site no longer offers | "Rithavo Profile" / "Your starting point" |
| `index.html` journey step 3 | "Represent: **Rithavo Card**. A professional identity that represents you across the Rithavo network." | Presents the Card as a step in the journey | "Represent: **Rithavo Profile**. Your professional profile, kept in one place." or mark the Card "Coming later" |
| `index.html` journey steps 4 and 5 | "Discover: People + Businesses", "Connect: Professional relationships" | Describe features not offered on rithavo.com | Mark both "Planned", or remove until offered |

## 4. Production changes that would be required later (all need separate approval; none made)
1. Fast-forward `main` to the approved candidate commit (or merge the branch), which deploys Production.
2. To turn the guide on: set `RITHAVO_RITANYA_MODE=browser` in Production and redeploy. No other variable is needed in this mode.
3. Nothing is needed for the Card change (the handoff is off by default).
Rollback: unset `RITHAVO_RITANYA_MODE` (or set `RITHAVO_RITANYA_PAUSED=true`) and redeploy; to undo the code, redeploy `bb2a5e3`.
