import { buildSearchIndex } from "../../lib/docs";

// Índice de busca da documentação (gerado no build, baixado só quando o visitante usa a busca).
export function GET() {
  return new Response(JSON.stringify(buildSearchIndex()), { headers: { "Content-Type": "application/json; charset=utf-8" } });
}
