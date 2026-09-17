# Shared UI style guide

Use this guide for all pages and new features. The visual direction is inspired by [Simplify](https://simplify.jobs/): light surfaces, dark readable text, teal actions, subtle borders, and generous spacing. Use our own product name and content.

## Source of truth

- [global.css](../static/css/global.css): shared colors, typography, navigation, footer, and focus states.
- [base.html](../templates/partials/base.html): shared Jinja layout and asset loading.
- [Dashboard](../templates/dashboard.html) and [dashboard.css](../static/css/dashboard.css): cards, forms, and status states.

Extend the shared layout on every page. Keep navigation and footer in their shared partials. Put feature-specific CSS beneath a feature wrapper; do not redefine the whole app theme inside a feature stylesheet. When a pattern becomes shared, move it into `global.css` and update its consumers instead of maintaining competing copies.

## Colors

Use the existing CSS variables for the base palette:

| Variable | Value | Use |
| --- | --- | --- |
| `--background` | `#f7f9fb` | Page background |
| `--surface` | `#ffffff` | Cards and navigation |
| `--text-strong` | `#24313a` | Headings and important values |
| `--text-normal` | `#52616e` | Body copy |
| `--text-subtle` | `#63717e` | Supporting labels |
| `--accent` | `#087f9a` | Links, focus, selected states |
| `--border` | `#e0e6eb` | Card outlines and dividers |

The following component colors are currently literal values in the reference stylesheets, not additional global variables:

| Treatment | Colors |
| --- | --- |
| Primary action | `#164653` background, white text; `#0d5b6e` hover |
| Light teal surface | `#e9f8fc` background, `#087891` text |
| Positive status | `#eaf7ee` background, `#287653` text |
| Neutral badge | `#f3f5f7` background, `#697681` text |
| Purple accent | `#f0ecfc` background, `#7055a5` text |
| Blue accent | `#e8f5fc` background, `#2b769e` text |
| Peach accent | `#fff0e6` background, `#a16036` text |
| Error text | `#b52d36` |

Use bright cyan for small decorative accents; use the darker teal for readable text. Status must include a text label, not just a color.

## Typography and spacing

- App font: `'Segoe UI', Arial, sans-serif`; no font download is needed.
- Standard page heading: 28–36px, weight 650–700, modest negative letter spacing.
- Section headings: 20–24px. Card headings: 17–19px.
- Main copy: 14–16px, line height around 1.7. Card supporting copy: 13px. Small labels: 11–12px; reserve 10px for short nonessential badges.
- Use a content width around 1120px with 20–28px horizontal page padding, reducing as needed on mobile.
- Typical section spacing: 28–40px. Card padding: 22–28px desktop, 16–20px mobile. Grid gaps: 18–24px.

## Components

- **Cards:** white surface, 1px neutral border, 12–16px corner radius. Keep shadows faint.
- **Buttons:** dark teal primary action with white text; pale teal secondary action with a teal border. Use 7–8px corners in app forms. Rounded pill buttons are appropriate for landing-page calls to action.
- **Inputs:** white background, subtle gray border, 7px radius, clear visible label. Keep the entered value distinct from a unit such as “credits.”
- **Selection:** teal outline and a check mark or pressed state. Keep selection visible after a control is disabled.
- **Badges and chips:** small, quiet, lightly tinted. Reserve green for positive/open states. Avoid making static labels look like buttons.
- **Results:** show concise explanatory text in a pale teal panel. Errors appear near the relevant control with actionable wording.
- **Navigation:** shared 72px white header, consistent product name, and a pale teal active tab. Only link to working routes.
- **Footer:** reuse the shared footer; do not add placeholder copy or a separate feature-specific brand.

Label unfinished modules “Coming soon” and simulated content “Demo.” Do not display invented account activity, progress, or live results as real data.

## Page pattern

Start a new page with the shared layout:

```html
{% extends 'partials/base.html' %}
{% block title %}Feature · JobPilot{% endblock %}
{% block styles %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/feature.css') }}">
{% endblock %}
{% block content %}
<main class="feature-page">
    <h1>Feature title</h1>
    <article class="feature-card">...</article>
</main>
{% endblock %}
```

Example feature-scoped styles (these are examples, not preinstalled utility classes):

```css
.feature-page {
    max-width: 1120px;
    margin: 36px auto;
    padding: 0 24px;
}
.feature-page .feature-card {
    background: var(--surface);
    color: var(--text-normal);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 24px;
}
```

Use Jinja `url_for` for routes/assets and keep user-provided text escaped. Keep business logic separate from presentation so components can receive real backend data later.

## Responsive behavior and accessibility

- Shared navigation switches to its mobile menu at 1000px; keep the CSS breakpoint and `base.js` resize logic aligned.
- Collapse card grids when content becomes cramped, generally around 650–850px. Choose the breakpoint based on the component, not a fixed device model.
- Verify at 390px and 1440px, and check narrow layouts down to the 320px minimum without horizontal overflow.
- Use real buttons for actions, links for navigation, labels for inputs, and native `details`/`summary` for expandable content where suitable.
- Keep visible keyboard focus and readable contrast. Ensure primary touch controls are comfortably sized, ideally at least 44px tall.
- Announce asynchronous results with a status region and validation failures with an alert. Support reduced motion if adding transitions or animation.

Before handing off UI changes, check the dashboard and login for shared-style regressions, test keyboard/mobile navigation, and inspect loading, empty, disabled, selected, error, and result states that apply to the feature. Update this guide when shared design decisions change.
