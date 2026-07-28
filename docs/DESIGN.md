# Charoite Design System

***English** · [Русский](DESIGN.ru.md) · [中文](DESIGN.zh.md)*

One character across two platforms: the macOS app and the iPhone
companion are built from the same tokens. The source of truth in code
is `Theme.swift` in each target (`app/Sources/CharoiteApp/Theme.swift`,
`app-ios/Sources/CharoiteiOS/Theme.swift`); this document is the
human-readable description and the agreements behind it — code wins.

## Palette

| Token | Value | Role |
|---|---|---|
| `accent` | `#6366F1` indigo | action: buttons, links, active states |
| `violet` | `#8B5CF6` charoite | character: gradients, brand accents |
| `brand` | accent→violet gradient | "live" action: recording, first run |
| `sky` | `#0EA5E9` | the Claude cloud pane — distinct from local |
| `ok` | `#059669` | success, green statuses |

Semantic colors (recording/error red) are system colors, not brand ones.
Backgrounds and text use the platform's system materials (`.bar`,
`.background`, `secondary`): dark mode comes for free and stays honest.

## Typography

The platform's system font (SF), no custom typefaces: the app is a tool
that sits next to your work, not a showcase.

- Pane titles: `caption.weight(.semibold)`, secondary color
  (`paneTitle` in SuflerView, macOS).
- Caps labels: `caption2.semibold` + kerning 0.8 (`Theme.label`, iOS).
- Recording timer: light weight (`thin`/`weight(200)`), monospaced digits.
- Body text: system sizes; long Russian strings are never squeezed.

## Geometry

- Radii (`Theme.radius` / `Theme.radiusCard`): 8 for fields and small
  elements; 12 for cards and chat bubbles; capsules are pills.
- Padding: 12 by default inside panes, 14–16 at window edges.
- Card: system surface fill; shadows only on "live" elements (the record
  button), never on static cards.

## Components

- **Pane**: icon + caps title (`paneTitle`), secondary color, `.bar`
  background; a copy button only where content is one-shot (Hint,
  Claude), not on feeds.
- **Record button**: circle/capsule with the `brand` gradient and a soft
  accent-colored shadow; while recording — system red, shadow off.
- **Empty states**: an icon plus one line saying what will appear and
  when; no illustrations.
- **Delivery/queue statuses**: a single line at the bottom of the
  screen, in plain language; no modal dialogs.

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
