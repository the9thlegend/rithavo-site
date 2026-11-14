# Profile redesign (release R1): design copies, limits and packaging

> **Update (implementation phase).** The Founder approved a dark, gradient-led visual direction (desktop and mobile references) that supersedes
> the flat Bearing restrictions below. The profile pages now use `assets/profile/profile.css` (self-contained; Manrope, DM Mono, Fraunces
> only) plus the derived `bearing-tokens.css` (read by `theme.js` for the browser theme colour). `bearing-components.css` and
> `profile-theme.css` were removed. The sections below record what the earlier phase copied and why; the pinned token hash and the
> excluded declarations are unchanged. Original Bearing files remain untouched.


Maintained with the code. Everything below is generated from, and checked against, the files in this repository.

## 1. What was copied from Bearing, and what was not

**Originals are untouched.** The Bearing files live as untracked files in the `rithavo-site` working tree
(`tokens.css`, `design-lab/lab.css`). They were only read. Their SHA-256 values today:

| Original | SHA-256 |
|---|---|
| `tokens.css` | `23dc1f64a8e970aefefe96f9c101e605643946985c3fcafe526cd57a6bf41078` |
| `design-lab/lab.css` | `accbe4e9be1c010b789d022332c5249ed2d5ea03c876d70210cac386f1119dca` |

Only decisions labelled APPROVED in Bearing's `DESIGN.md` are binding; PROPOSED are recommendations; EXPERIMENTAL concepts
(Field, Ritanya, Theta, the dark ink-panel colour) are excluded and appear nowhere in these copies.

### 1.1 `assets/profile/bearing-tokens.css` (and the identical `app/static/bearing-tokens.css` in the profile service)
A **derived** copy of `tokens.css`: identical except for one omitted block (section 1.2). Pinned SHA-256:
`bcc8df370eb38d99b7564138b1fb7cc16767fe7ffc5681028590d931afaa144c`. A test in each repository re-derives it from the original (when the original is present) and
fails on any difference; the same pinned value is asserted in both repositories.

Copied unchanged, 86 custom properties:

- **Surfaces and rules**: `--c-canvas`, `--c-control-border`, `--c-on-panel`, `--c-on-panel-muted`, `--c-on-panel-soft`, `--c-panel`, `--c-panel-rule`, `--c-rule`, `--c-rule-strong`, `--c-scrim`, `--c-sheet`, `--c-well`
- **Text**: `--c-ink`, `--c-ink-soft`, `--c-muted`
- **Signal accent**: `--c-signal`, `--c-signal-text`
- **Primary action and focus**: `--c-focus`, `--c-primary-bg`, `--c-primary-bg-hover`, `--c-primary-chevron`, `--c-primary-fg`
- **Semantic (status and validation only)**: `--c-error`, `--c-mid`, `--c-mid-text`, `--c-mid-tint`, `--c-strong`, `--c-strong-text`, `--c-strong-tint`, `--c-success`, `--c-warning`, `--c-weak`, `--c-weak-text`, `--c-weak-tint`
- **Brand asset colours (logo only)**: `--brand-g1`, `--brand-g2`, `--brand-g3`
- **Type scale**: `--f-wordmark`, `--fs-body`, `--fs-body-lg`, `--fs-display`, `--fs-h1`, `--fs-h2`, `--fs-h3`, `--fs-label`, `--fs-meta`, `--fs-small`, `--lh-body`, `--lh-display`, `--lh-h1`, `--lh-h2`, `--lh-h3`, `--lh-small`, `--ls-display`, `--ls-h1`, `--ls-h2`, `--ls-h3`, `--ls-label`, `--measure`
- **Space and layout**: `--container`, `--container-app`, `--gutter`, `--note-w`, `--s-1`, `--s-2`, `--s-3`, `--s-4`, `--s-5`, `--s-6`, `--s-7`, `--s-8`, `--s-9`
- **Shape, line, layers, elevation**: `--bw`, `--bw-strong`, `--elev-overlay`, `--radius-container`, `--radius-control`, `--radius-pill`, `--z-dialog`, `--z-menu`, `--z-nav`
- **Motion**: `--dur-1`, `--dur-2`, `--dur-3`, `--dur-4`, `--ease`

Also copied unchanged: the light theme (default), the dark theme that follows `prefers-color-scheme` unless
`data-theme="light"` or `"dark"` is set, and all comments for the retained values.

### 1.2 Intentionally excluded declarations (and why)
The repository's font guard (`tests/test_self_hosted_fonts.py`) forbids naming any font the repository does not ship, and
this release must not weaken it. Bearing's `tokens.css` names three candidate type families and a data face that are not
shipped. The following 10 declarations and the comment block around them are therefore omitted:

- `--f-display: "Bricolage Grotesque", "Hanken Grotesk", ui-sans-serif, system-ui, sans-serif;`
- `--f-body:    "Hanken Grotesk", ui-sans-serif, system-ui, sans-serif;`
- `--fw-display: 600;`
- `--f-display: "Geist", ui-sans-serif, system-ui, sans-serif;`
- `--f-body:    "Geist", ui-sans-serif, system-ui, sans-serif;`
- `--fw-display: 600;`
- `--f-display: "Manrope", ui-sans-serif, system-ui, sans-serif;`
- `--f-body:    "Manrope", ui-sans-serif, system-ui, sans-serif;`
- `--fw-display: 700;`
- `--f-data:     "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;`

The profile pages instead set `--f-display`, `--f-body`, `--f-data` and `--fw-display` in `profile-theme.css` (decision V2):
Manrope for headings and body, DM Mono for numbers, dates and labels, Fraunces for the wordmark only (`--f-wordmark`, copied
unchanged). All three are already self-hosted in `/assets/fonts`.

### 1.3 `assets/profile/bearing-components.css`
A **verbatim extract** of `design-lab/lab.css`: lines 7 to 436 (reset, layout, typography, surfaces, Heading Chevron,
buttons, links, navigation, forms, status and tags, the Scale, Margin Note, states, dialog) and lines 507 to 513 (the
forced-colours block), behind a provenance header. Omitted: the hero prototype (lines 437 to 454), every `.lab-*` rule that
is Design Lab page chrome (455 to 506) and the print line (514). Pinned SHA-256:
`7a9a2490bcd8b5929215e0159ca66979ecda23a1fdb0b42d7c48a33d80ec570d`. The one `.lab-seg` selector inside a shared focus rule (line 92 of the copy) is harmless and
left as extracted, so the extract stays verbatim.

### 1.4 Files that are ours (not copies)
`assets/profile/profile-theme.css` (font mapping, logo gradient aliases, the single portrait-backdrop token, wordmark and
skip-link rules, `aria-current="location"` mirroring Bearing's rule, 44 px minimums) and `assets/profile/theme.js`
(Light / Dark / System, per device). The brand gradient is allowed only in the logo and in one contained, static, plain
rounded-rectangle portrait backdrop (decisions V1 and V11); a test pins that there is exactly one gradient token.

## 2. Rate limiting: what exists and what it cannot do
Vercel Firewall rate limiting is **unresolved** (availability under the current plan could not be determined and nothing is
configured). Until decided, the site uses an in-memory sliding-window limiter (`app/routes_public_profile.py`):

| Budget | Window | Key | Counts |
|---|---|---|---|
| 120 reads | 60 s | signed-in member | profile and photo reads |
| 20 misses | 300 s | signed-in member | unknown, hidden or malformed slugs (probing) |
| 30 writes | 60 s | signed-in member | follow, unfollow, share, settings |

Limitations, stated plainly:
- **Per process.** On serverless, each warm instance has its own counters, so the effective limit is higher in proportion to
  the number of instances, and counters reset on a cold start.
- **Keyed by session, not by network address.** A member can be throttled; an unauthenticated caller cannot be, because
  anonymous requests are refused with 401 before any counter (they still cost a function invocation).
- A durable limiter would need a new table, which this release does not add (decision D-B).
- Measured locally: 1,000 random slugs through one session produced 20 lookups and 980 refusals.

## 3. Page packaging
The member-profile page shell is a Python module (`app/pages/public_profile_shell.py`), not an HTML file. Python modules are
always bundled with the function, so packaging cannot silently drop the page, and no public static file exists for a
disabled feature. (An earlier design read an HTML file at request time; a deployment inspection could not confirm that
non-Python files are bundled, so the question was removed instead of assumed.)
