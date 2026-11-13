# Documentación

Esta documentación es mkdocs-material, bilingüe, construida por GitHub Actions y servida
desde un Worker de Cloudflare.

## Redacción

El inglés es el idioma por defecto y vive en `page.md`. El español vive al lado en
`page.es.md`. El plugin `mkdocs-static-i18n` los empareja por sufijo y construye `/` y
`/es/`.

Reglas que mantienen ambos sincronizados:

- **Los dos o ninguno.** Una página nueva llega con ambos ficheros. Un sitio traducido a
  medias es peor que uno monolingüe, porque el lector no puede saber qué páginas le
  faltan.
- **Los títulos de navegación se traducen en `mkdocs.yml`**, bajo `nav_translations`, no
  renombrando ficheros.
- **Los enlaces entre páginas usan la ruta del idioma por defecto.** Escribe
  `../roadmap.md`; el plugin lo resuelve a la página en español desde una página en
  español.
- **Las anclas difieren entre idiomas.** Un enlace a `roadmap.md#m1-spine` desde una
  página en inglés debe ser `roadmap.md#m1-espina-dorsal` desde la española, porque el
  ancla se genera a partir del encabezado traducido. Las builds estrictas detectan las
  rotas.

## Vista previa local

```bash
python3 -m venv .venv
.venv/bin/pip install mkdocs-material mkdocs-static-i18n
.venv/bin/mkdocs serve
```

Aparece un selector de idioma en la cabecera. Revisa los dos.

## Construcción y despliegue

`.github/workflows/docs.yml` se ejecuta en cada push a `main` que toque `docs/**` o
`mkdocs.yml`:

1. `mkdocs build --strict` — cualquier enlace interno roto o destino de navegación
   inexistente hace fallar la build en lugar de publicar una página muerta.
2. `wrangler deploy` — sube `site/` como activos estáticos en un Worker de Cloudflare.

La build estricta es deliberada. Un sitio de documentación cuyos enlaces se pudren en
silencio es un sitio en el que nadie confía.

### Por qué un Worker y no Pages

Cloudflare Pages está en mantenimiento; los activos estáticos en Workers son el camino que
Cloudflare desarrolla de verdad. Para un sitio estático hoy son equivalentes, y el Worker
deja sitio para añadir una redirección o una regla de cabeceras más adelante sin migrar.

### Secretos

`CLOUDFLARE_API_TOKEN` y `CLOUDFLARE_ACCOUNT_ID` son secretos del repositorio, acotados al
despliegue de Workers. Nunca se versionan; `.env` está en `.gitignore`.
