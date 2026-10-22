// Links entre páginas do manual (escritos para o GitHub: "operador.md#justificar", "../kpis.md") → URLs do site.
// Módulo .mjs puro: usado pelo plugin rehype (astro.config.mjs) e pelos testes.

/** Slug da página no site: README.md → "" (/docs), "operador.md" → "operador". */
export function docSlug(file) {
  const base = String(file).split("/").pop().replace(/\.md$/i, "");
  return base.toLowerCase() === "readme" ? "" : base;
}

/** URL pública de uma página do manual. */
export function docUrl(slug, hash = "") {
  return `/docs${slug ? `/${slug}` : ""}${hash}`;
}

/**
 * Reescreve o href de um link do manual. Devolve:
 *  - string: novo href (link interno do manual);
 *  - null: o link aponta para a documentação técnica do repositório (../kpis.md…), que não é publicada — vira texto;
 *  - undefined: deixa como está (links externos, âncoras, e-mail).
 */
export function rewriteDocHref(href) {
  if (!href || /^(?:[a-z]+:|#|\/\/)/i.test(href)) return undefined;
  const m = /^(\.\/)?([A-Za-z0-9_-]+)\.md(#[^\s]*)?$/.exec(href);
  if (m) return docUrl(docSlug(m[2]), m[3] ?? "");
  if (/^\.\.\//.test(href) && /\.md(#.*)?$/.test(href)) return null;
  return undefined;
}

/**
 * Aplica rewriteDocHref aos links markdown `[texto](href)` de uma página do manual (feito ao copiar docs/manual para
 * src/manual — scripts/sync-docs.mjs). Links para a documentação técnica do repositório viram só o texto.
 */
export function rewriteDocLinks(md) {
  return md.replace(/(!?)\[([^\]]*)\]\(([^)\s]+)\)/g, (all, bang, text, href) => {
    if (bang) return all;
    const next = rewriteDocHref(href);
    if (next === undefined) return all;
    return next === null ? text : `[${text}](${next})`;
  });
}
