# ידע רכב — Premium White/Chrome UI Redesign

UI/UX-only redesign. **No** backend fields, API responses, schemas, routes, form
names, JS selectors, or product logic were changed. All 366 tests pass.

## Approach

The site has no base template — each page is self-contained and configures its own
Tailwind CDN instance with a **dark** palette (`dark:#0B0F19`, inverted `slate`
usage, `text-white`). To flip the whole product to a bright white/chrome look without
touching markup/selectors, the redesign:

1. **Inverts the Tailwind color tokens** per page (the `slate` scale, `dark`,
   `dark-lighter`, `primary`, `secondary`). Every existing utility class
   (`bg-dark`, `bg-slate-900`, `text-slate-300`, `border-slate-700`, …) now resolves
   to a light/chrome value automatically — zero class/selector edits.
2. Adds a shared **`static/theme.css`** design system loaded last in every `<head>`:
   - Design tokens (white base, chrome silver, ink/muted text, chrome border, soft
     shadows, success/warning/risk).
   - Reusable classes: `.yr-chrome-card`, `.yr-glass`, `.yr-chip`, `.yr-gauge`,
     `.yr-btn`, `.yr-btn--chrome`, `.yr-risk-note`, `.yr-section-header`,
     `.yr-grid*`, `.yr-form`, `.yr-chrome-text`, `.yr-accent-text`,
     plus navbar/footer (`.yr-header`, `.yr-brand`, `.yr-nav-pill`, `.yr-footer`).
   - Ambient chrome background, Heebo/Rubik fonts, chrome scrollbar,
     keyboard-visible `:focus-visible`, contrast fixes for light accent-text shades.
   - `text-white` → ink on light surfaces, but kept white on colored buttons/badges.
3. Converts hardcoded dark colors inside per-page `<style>` blocks
   (text→ink, surfaces/borders→chrome) which Tailwind inversion can't reach.
4. Rewrites the shared **navbar / footer / login modal** and the inline
   privacy/terms headers to one white-chrome identity with the chrome `ידע רכב` brand.

## Selector / data safety

| Area | Preserved (must not break) | Fallback |
|---|---|---|
| Navbar | IDs `navbar-toggle`/`navbar-close`/`navbar-drawer`, classes `.drawer-overlay`/`.drawer-panel`/`.nav-drawer-link`, all `url_for` endpoints, `__IS_AUTHENTICATED__`, csrf meta | — |
| Login modal | `#login-modal`, `window.showLoginModal/hideLoginModal` | — |
| Recommendations | `#advisor-results`, `#advisor-search-queries`, `#advisor-profile-summary`, `#advisor-highlight-cards`, `#advisor-table-wrapper`, `#advisorResultReadyPanel`, `#advisorOpenResultButton`; response shape `search_performed/search_queries/recommended_cars/history_id`; all `recommended_cars[]` fields | styling only |
| Compare / Reliability / Dashboard | all forms, inputs, IDs, fetch/API calls, render JS | empty-state text untouched |
| Legal (privacy/terms/consent) | all legal text, disclaimers, consent partials | untouched |

## Verification (safe, offline — no paid API / no GEMINI_API_KEY)
- `pytest` → **366 passed** (incl. UI-label, decision-copy, legal-disclaimer tests).
- All 21 templates parse under Jinja2.
- `/`, `/app`, `/recommendations`, `/compare`, `/privacy`, `/terms`, `/coming-soon`
  render `200` with the theme; no residual dark color literals in rendered HTML.
</content>
