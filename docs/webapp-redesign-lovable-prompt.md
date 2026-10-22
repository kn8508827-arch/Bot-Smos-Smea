# Web App redesign — prompt for Lovable

The Web App (https://team-smos-snea.lovable.app) is built in Lovable, so it is
redesigned there, not in this repo. Open the project in Lovable, paste the prompt
below into the chat, and review the preview before publishing.

If the redesigned app gets a new URL, set `WEB_APP_URL` in Railway → Variables;
the bot's menu and /register button use it (no code change needed).

---

## Prompt (copy everything below this line)

Redesign this app as a modern **Telegram Mini App** for "Team ស្មោះស្នេហ៍" (Team Smos Snea),
a Cambodian community. Keep all existing pages, form fields, validation, data storage and
submission logic exactly as they work now — this is a visual and UX redesign only. Do not
delete or rename any database tables, columns or API calls.

**Telegram integration**
- Load `https://telegram.org/js/telegram-web-app.js` and call `Telegram.WebApp.ready()` and
  `Telegram.WebApp.expand()` on start.
- Pre-fill the user's name from `Telegram.WebApp.initDataUnsafe.user` when it exists.
- Use `Telegram.WebApp.MainButton` as the primary "Submit / ចុះឈ្មោះ" button on form pages
  (show a progress spinner while submitting), and `Telegram.WebApp.BackButton` for going back.
- Add light haptic feedback (`HapticFeedback.impactOccurred('light')`) on button taps and
  `notificationOccurred('success')` after a successful submission.
- Respect safe areas (`env(safe-area-inset-*)`) so nothing hides behind the notch.
- Everything must still work when opened in a normal browser (no Telegram object): fall back
  to on-page buttons.

**Dark mode**
- Follow Telegram's theme automatically: read `Telegram.WebApp.colorScheme` and
  `themeParams` (bg_color, text_color, button_color, button_text_color, hint_color,
  secondary_bg_color) and listen for the `themeChanged` event.
- Also add a small sun/moon toggle in the header that overrides it; remember the choice
  in localStorage.
- Dark palette: near-black background (#0f1115), raised cards (#181b22), soft borders,
  one accent color (warm gold #f5b942) for buttons and highlights. Light palette: off-white
  background, white cards, same accent. Check text contrast is readable in both.

**Look and feel**
- Mobile-first, max width ~480px, generous spacing, rounded cards (16px radius), subtle
  shadows, glass-style header that stays at the top.
- Khmer-friendly fonts: "Kantumruy Pro" (or "Noto Sans Khmer") from Google Fonts for Khmer,
  "Inter" for Latin, with line-height ≥ 1.6 so Khmer vowels and subscripts are not clipped.
- Home screen: a hero card with the team logo/name and a short Khmer welcome line, then a
  grid of large icon cards for the main sections, and a highlighted "ចុះឈ្មោះ" call to action.
- Form: floating labels, clear inline error messages in Khmer, a progress/step indicator if
  the form has several parts, and a friendly success screen with a check-mark animation.

**Animation (keep it smooth and light)**
- Use framer-motion: fade + slight slide-up when pages and cards appear, small stagger
  for grid items, gentle scale on press (0.97), animated page transitions.
- Skeleton shimmer placeholders while data loads.
- Respect `prefers-reduced-motion`: turn animations off for users who ask for less motion.
- Keep it fast: no heavy images, lazy-load anything below the fold.

**Language**
- All visible text in Khmer (keep English only for brand names and technical words).

When done, show me the home screen and the form in both dark and light mode.
