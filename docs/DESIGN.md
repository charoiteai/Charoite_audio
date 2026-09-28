# Charoite Design System

***English** · [Русский](ru/DESIGN.md) · [中文](zh/DESIGN.md)*

One character across two platforms: the macOS app and the iPhone
companion are built from the same tokens. The source of truth in code
is `Theme.swift` in each target (`app/Sources/CharoiteApp/Theme.swift`,
`app-ios/Sources/CharoiteiOS/Theme.swift`); on macOS, `DesignKit.swift`
and `CharoiteButton.swift` extend it with the surface, warning and button
tokens. This document is the human-readable description and the
agreements behind it — code wins. The Android companion (`app-android/`)
is not on these tokens yet: it runs Material 3 with its own violet pair
(`#9B6DFF` / `#6C4BD8`).

## Palette

| Token | Value | Role |
|---|---|---|
| `accent` | `#6366F1` indigo | action: buttons, links, active states |
| `violet` | `#8B5CF6` charoite | character: gradients, brand accents |
| `brand` | accent→violet gradient | "live" action: recording, first run |
| `sky` | `#0EA5E9` | Claude, the one meeting layer that leaves the machine — distinct from local |
| `ok` | `#059669` | success, green statuses |
| `warning` | system orange | attention without breakage; `overdue` is its special case |
| `danger` | `#DC2626` | deletion only (destructive buttons): overdue warns, deletion destroys |
| `surfaceMemory` | accent at 5 % | lavender: the meeting thread pane, where hints and memory answers land |
| `surfaceCloud` / `borderCloud` | sky at 6 % / 22 % | what leaves the machine: the Claude card, the cloud dossier toggle |

Semantic colors (recording/error red) are system colors, not brand ones.
Backgrounds and text use the platform's system materials (`.bar`,
`.background`, `secondary`): dark mode comes for free and stays honest.

Colors and sizes are never set "in place": views take `Theme.warning`
rather than `.orange`, `Theme.ok` rather than `.green`, and text styles
(`.caption`, `.callout`, `.headline`, `.title2`) rather than
`.system(size:)`. `tests/test_design_discipline.py` fails the run on the
first exception — the August 2026 audit found 21 in-place colors and 13
hard-coded sizes, and a system that lives on paper only drifts.

### Surface means origin

White system surface — now and local. Lavender (`Theme.surfaceMemory`)
— memory: the meeting screen's thread pane, where hints and answers from
the archive and the graph land. On the Memory screen the owner chose the
meeting-library palette (24.08): model replies are white hairline-bordered
cards, and provenance is carried by source chips and the meta line, not by
surface color; the `MemorySurface` container is gone. Sky (`CloudSurface`)
— what leaves the Mac: the Claude card on the meeting screen, the cloud
dossier toggle in Settings. The rule "local is quiet, cloud is visible"
becomes visible rather than declared; color is never the only signal —
every cloud surface also says it in words ("leaves this Mac"). Settings'
"What leaves this Mac" section names every path out in words; the cloud
chat toggle and the version check there are not on the sky surface yet —
a known gap.

## Typography

The platform's system font (SF), no custom typefaces: the app is a tool
that sits next to your work, not a showcase.

- Pane titles and caps labels: `caption2.semibold` in capitals + kerning
  0.8, secondary color (`PaneHeader` from DesignKit on macOS, `Theme.label`
  on iOS).
- Recording timer: monospaced digits, so the line does not jump every
  second; light where the timer is the main thing (`thin` on the iPhone
  record screen, `light` in `RecordCapsule`), `headline` in red next to the
  record button in the meeting header.
- Body text: system sizes; long Russian strings are never squeezed.

## Geometry

- Radii (`Theme.radius` / `Theme.radiusCard`): 8 for fields and small
  elements; 12 for cards and chat bubbles; capsules are pills.
- Padding: 12 by default inside panes, 14–16 at window edges.
- Card: system surface fill; shadows only on "live" elements (the record
  button), never on static cards.

## Components

- **Pane**: icon + caps title + an optional counter (`PaneHeader` from
  DesignKit), secondary color, `.bar` background; a copy button where the
  pane holds a result to take away — the meeting thread, where hints and
  Claude's answers land — not on the raw transcript feed.
- **Layer chips**: the meeting screen's layers (Hints, Claude) are
  `LayerChip`s in a `LayerBar`, not system toggles — an active layer
  is an indigo fill, Claude is a `sky` outline because it is the only
  layer that leaves the machine. The same chip shows the "Graph memory"
  state on the Memory screen. Each chip exposes its On/Off value and
  selected trait to VoiceOver; color is never the only state signal.
- **Record button**: the `brand` gradient and a soft accent-colored
  shadow. The shape follows the place: a capsule (`RecordCapsule`) on
  Today, a 32 pt button of the button scale (radius 7) in the meeting
  header, level with its neighbours; on the Mac, while recording — system
  red, shadow off. On the iPhone it is a circle with the radial twin of the
  gradient (`Theme.record`); while recording the gradient stays and the
  white dot inside turns into a stop square.
- **Empty states** (`EmptyState`, DesignKit): a title, one line on what
  will appear, and the one action that gets there, pinned to the top-left;
  a small icon beside the title, no illustrations.
- **Delivery queue** (iPhone): an entry, not a grey line — the count,
  orange once something has waited over a day; it opens a sheet listing
  everything that has not left (kind, time, size) with Share on each
  recording. Plain language, no alerts.
- **Health** (`HealthRollup`, macOS): recording, processing, Ollama and
  the night roll up into one state for the menu-bar icon and status line.
  Red means data is being lost right now (a failed recording during a
  meeting); everything else is `warning`. A problem stays on screen until
  its owner confirms recovery — an ordinary status line does not clear it.
- **One button scale** (`CharoiteButton`, macOS): seven roles — prominent,
  regular, quiet, link, destructive, destructiveFilled, icon — and three
  sizes (s/m/l, 21/26/32 pt tall). One prominent button per pane; no system
  blue (prominent is `brand`, a link is `accent`); a button with no label
  must carry `.help` and an accessibility label. The button radius follows
  its height rather than the surface radii above — a 21 pt control with a
  12 pt radius is a capsule, not a button. Full rules and the handoff
  package: [BUTTONS_2026-08.md](design/BUTTONS_2026-08.md).
- **Phone companion** (iPhone, August 2026): recording, meetings, the meeting
  card, tasks and the delivery queue — the same tokens and rules as on the
  Mac, minus sky: the companion talks to nothing but iCloud folders. Spec and
  mockup: [MOBILE_2026-08.md](design/MOBILE_2026-08.md); it also covers the
  three macOS screens the revision marked "not started" — Memory, Tasks,
  Meeting library — all three shipped in August. On the phone, the record
  screen and the queue sheet follow the mockup; the meetings feed, the
  meeting card and tasks are still simpler than it (a plain list, a card
  built from the minutes without the four depths, tasks without the summary
  or the "By due date" view).

## UI copy tone

Live language, no bureaucratic filler. A button names the action, a
status states the result, an error says what happened and what to do.
The words "please", "sorry" and "successfully" are not used.

## Principles

1. Local is silent, cloud is visible: anything that leaves the machine
   has its own color (`sky`) and an off switch.
2. Nothing disappears silently: queues and statuses instead of quiet
   failures.
3. No voice biometrics — and the design shows it: there are no "voice
   profile" screens.
4. Dark mode is not an inversion but system materials with the same
   tokens.
