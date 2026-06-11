# Find Fortune Route Design System

## Skill Basis

This design direction combines:

- Impeccable: product UI register, craft gates, production-grade interface polish.
- Taste Skill redesign-existing-projects: audit and improve the existing app incrementally.
- Taste Skill minimalist-ui: restrained, professional, high-signal product surfaces.
- imagegen-frontend-web: use only for dashboard reference frames or visual direction boards, constrained to fintech/product UI.
- TypeUI: keep this file as the stable project design context.

## Tone

Precise, professional, calm, analytical. The product should feel like a trustworthy research terminal rather than a promotional website.

## Color Tokens

- Canvas: cool gray, subtle depth, never pure decorative gradients.
- Surface: white or near-white panels.
- Text: dark graphite.
- Muted text: blue-gray, strong enough for dense labels.
- Action blue: primary command and selected states.
- China market red: positive/up.
- China market green: negative/down.
- Amber: warning, guardrail, incomplete readiness.

## Typography

- Prefer native professional UI fonts: SF Pro, PingFang SC, Microsoft YaHei, Segoe UI.
- Use tabular numerals for prices, percentages, and metrics.
- Keep headings compact; avoid oversized hero typography inside dashboards.
- Labels should be short and scannable.

## Layout

- Dashboard first, no landing-page hero.
- Use full-width bands for cross-cutting modules, panels for work areas, and cards only for repeated items.
- Keep border radius at 8px.
- Use dense but breathable grids: 10-16px gaps for panels, 6-10px gaps inside lists.
- Do not nest visual cards inside other decorative cards.

## Components

- Buttons use icons when the action is operational.
- Metrics use compact cells with a label, value, and optional tone.
- Strategy scores use progress bars plus plain text rationale.
- Holding context must show entry price, current price, floating return, and visual buy-line overlays on charts.
- Empty states should be concise and operational.

## Chart Style

- Line charts and K-line charts use restrained strokes, soft grid lines, and readable reference markers.
- Buy/entry markers use action blue to avoid conflicting with red/green market movement semantics.
- Price movement follows China A-share convention: red for up/positive, green for down/negative.

## Quality Gates

- No generic AI visual tropes: purple gradients, floating blobs, overlarge cards, or fake depth.
- Text must not overflow controls or compact panels.
- Every interactive row must have hover and focus-visible states.
- Mobile layouts collapse cleanly without overlapping text.
- Run TypeScript checks after UI changes.
