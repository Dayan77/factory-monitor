/**
 * Seção "Documentação": o manual do usuário (src/manual/*.md, cópia de docs/manual — scripts/sync-docs.mjs),
 * importado no build. Cada página vira /docs/<slug>; README.md é a capa (/docs).
 */
import type { MarkdownHeading } from "astro";
import { docSlug, docUrl } from "./doc-links.mjs";
import { buildSearchIndex as buildIndex, shortTitle, type SearchEntry } from "./doc-text";

export type { SearchEntry };

interface DocFrontmatter { title: string; description?: string; ordem?: number; papel?: string }
interface MdModule {
  frontmatter: DocFrontmatter;
  file: string;
  Content: unknown;
  getHeadings: () => MarkdownHeading[];
  rawContent: () => string;
}

export interface Doc {
  slug: string;
  url: string;
  title: string;
  /** Curto, para a navegação lateral. */
  short: string;
  description: string;
  order: number;
  role: string;
  Content: unknown;
  headings: MarkdownHeading[];
  raw: string;
}

const modules = import.meta.glob<MdModule>("../manual/*.md", { eager: true });

export const DOCS: Doc[] = Object.entries(modules)
  .map(([file, m]) => {
    const slug = docSlug(file);
    return {
      slug,
      url: docUrl(slug),
      title: m.frontmatter.title,
      short: shortTitle(m.frontmatter.title),
      description: m.frontmatter.description ?? "",
      order: m.frontmatter.ordem ?? 99,
      role: m.frontmatter.papel ?? "todos",
      Content: m.Content,
      headings: m.getHeadings(),
      raw: m.rawContent(),
    };
  })
  .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title, "pt-BR"));

export function docBySlug(slug: string): Doc | undefined {
  return DOCS.find((d) => d.slug === slug);
}

/** Índice de busca de todas as páginas (endpoint /docs/busca.json). */
export function buildSearchIndex(docs: Doc[] = DOCS): SearchEntry[] {
  return buildIndex(docs);
}
