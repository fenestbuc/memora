export function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

// Limits
export const MAX_TEXTS_PER_REQUEST = 100;
export const MAX_DOCUMENTS = 200;
export const MAX_RERANK_DOCUMENTS = 50;
export const MAX_DELETE_IDS = 200;
export const MAX_IMPORT_FACTS = 200;
export const MAX_MEMORY_LIST_LIMIT = 500;
export const MAX_EXPORT_LIMIT = 5000;
export const MAX_MIGRATIONS = 10;
export const MAX_EVAL_TOP_K = 100;

// STRICT allow-list for migrations — no arbitrary column names
export const ALLOWED_COLUMNS = new Set([
  "parent_id", "owner_id", "scope", "importance_score", "decayed_at", "archived", "vector_id", "pending_vector_sync"
]);
