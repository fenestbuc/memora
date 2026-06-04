import { sanitizeOwnerId, sanitizeScope } from "./validate.js";

export function buildMetadata(doc, extra = {}) {
  // Start with user metadata, but blocklist keys we will override
  const base = doc.metadata || {};
  const blocked = new Set(["owner_id", "scope", "parent_id", "category", "text", "source", "archived", "importance_score"]);
  const safe = {};
  for (const [k, v] of Object.entries(base)) {
    if (!blocked.has(k)) safe[k] = v;
  }

  return {
    ...safe,
    text: (doc.text || doc.content || "").slice(0, 10000),
    owner_id: sanitizeOwnerId(doc.owner_id || base.owner_id),
    scope: sanitizeScope(doc.scope || base.scope),
    parent_id: doc.parent_id || base.parent_id || null,
    category: doc.metadata?.category || doc.category || "general",
    source: doc.source_file || base.source || "",
    archived: doc.archived ?? base.archived ?? 0,
    importance_score: doc.importance_score ?? base.importance_score ?? 0.5,
    ...extra,
  };
}
