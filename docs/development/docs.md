# Documentation

These docs are mkdocs-material, bilingual, built by GitHub Actions and served from a
Cloudflare Worker.

## Writing

English is the default language and lives in `page.md`. Spanish lives next to it in
`page.es.md`. The `mkdocs-static-i18n` plugin pairs them by suffix and builds `/` and
`/es/`.

Rules that keep the two in sync:

- **Both or neither.** A new page ships with both files. A half-translated site is worse
  than a monolingual one, because readers cannot tell which pages they are missing.
- **Navigation titles are translated in `mkdocs.yml`**, under `nav_translations` — not by
  renaming files.
- **Cross-page links use the default-language path.** Write `../roadmap.md`; the plugin
  resolves it to the Spanish page from a Spanish page.
- **Anchors differ between languages.** A link to `roadmap.md#m1-spine` from an English
  page must be `roadmap.md#m1-espina-dorsal` from the Spanish one, because the anchor is
  generated from the translated heading. Strict builds catch broken ones.

## Local preview

```bash
python3 -m venv .venv
.venv/bin/pip install mkdocs-material mkdocs-static-i18n
.venv/bin/mkdocs serve
```

A language switcher appears in the header. Check both.

## Build and deploy

`.github/workflows/docs.yml` runs on every push to `main` that touches `docs/**` or
`mkdocs.yml`:

1. `mkdocs build --strict` — any broken internal link or missing nav target fails the
   build rather than shipping a dead page.
2. `wrangler deploy` — uploads `site/` as static assets on a Cloudflare Worker.

The strict build is deliberate. A documentation site whose links rot silently is a
documentation site nobody trusts.

### Why a Worker and not Pages

Cloudflare Pages is in maintenance; Workers static assets is the path Cloudflare is
actually developing. For a static site the two are equivalent today, and the Worker leaves
room to add a redirect or a header rule later without migrating.

### Secrets

`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are repository secrets, scoped to
Workers deployment. They are never committed; `.env` is in `.gitignore`.
