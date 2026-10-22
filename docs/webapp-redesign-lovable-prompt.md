# Web App redesign — prompt for Lovable

The Web App (https://team-smos-snea.lovable.app) is built in Lovable, so it is
redesigned there, not in this repo. Open the project in Lovable, paste the prompt
below into the chat, and review the preview before publishing.

If the redesigned app gets a new URL, set `WEB_APP_URL` in Railway → Variables;
the bot's main-menu "🚀 Team App" button and /register use it (no code change needed).

---

## Prompt (copy everything below this line)

Redesign this app — "Team ស្មោះស្នេហ៍ — Trip Dashboard" — as a modern **Telegram Mini App**.
Keep every existing section and its content: Home (welcome + official Telegram bot promo with
QR), Game Center (Spin and Drink, Blue Gold Rush, Logo Crush 3D with Play buttons), Team
(member count + online indicator), Team Potluck, Safety checklist, Official Team Outfit
(front/back photos), Install Team App instructions, and the footer navigation (Home, Feed,
Team, Profile). Keep all links, games, data and logic working exactly as now — this is a
visual and UX redesign only. Do not delete or rename any data, tables or API calls.

**Telegram integration**
- Load `https://telegram.org/js/telegram-web-app.js`; call `Telegram.WebApp.ready()` and
  `Telegram.WebApp.expand()` on start.
- Greet the user by name from `Telegram.WebApp.initDataUnsafe.user` on Home when available.
- Use `Telegram.WebApp.BackButton` when inside a section/game, and light haptic feedback
  (`HapticFeedback.impactOccurred('light')`) on taps.
- Respect safe areas (`env(safe-area-inset-*)`) so nothing hides behind the notch or the
  bottom navigation.
- Everything must still work in a normal browser (no Telegram object) — the "Install Team
  App" home-screen flow must keep working.

**Dark mode**
- Follow Telegram's theme automatically (`Telegram.WebApp.colorScheme`, `themeParams`, and
  the `themeChanged` event); in a normal browser follow `prefers-color-scheme`.
- Add a small sun/moon toggle in the header that overrides it; remember it in localStorage.
- Keep the team brand: dark mode = deep navy background (#0b1d4a → #0a1433 gradient) with
  raised cards slightly lighter, gold accent (#f5b942) for buttons, badges and highlights.
  Light mode = soft off-white background, white cards, navy text, same gold accent.
  Check text contrast is readable in both.

**Look and feel**
- Mobile-first, max width ~480px, generous spacing, rounded cards (16–20px radius), soft
  shadows, a glass-style sticky header and a floating bottom navigation bar with an
  animated active indicator.
- Khmer-friendly fonts: "Kantumruy Pro" (or "Noto Sans Khmer") for Khmer and "Inter" for
  Latin, line-height ≥ 1.6 so Khmer vowels and subscripts are never clipped.
- Home: hero card with team logo/name, the trip highlight (temple visit + stone-area outing),
  and quick-action tiles for Games, Team, Safety, Outfit.
- Game Center: large game cards with cover art, a glowing gold "Play" button.
- Safety: checklist with icons and tick animations.
- Team: member count with an animated counter and a pulsing green online dot.

**Animation (smooth and light)**
- framer-motion: fade + slight slide-up as sections enter, staggered card grids, gentle
  scale on press (0.97), smooth page transitions between footer tabs.
- Skeleton shimmer while anything loads.
- Respect `prefers-reduced-motion` (turn motion off for those users).
- Keep it fast: compress images, lazy-load below the fold.

**Language**
- Visible text in Khmer; keep English only for brand names, game names and short subtitles.

When done, show me Home, Game Center and Safety in both dark and light mode.
