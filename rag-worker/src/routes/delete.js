import { json, MAX_DELETE_IDS } from "../constants.js";
import { validateArray, ValidationError } from "../lib/validate.js";

export async function handleDelete(body, env) {
  try {
    const { ids } = body;
    if (!ids || !ids.length) {
      return json({ error: "No ids provided", code: "MISSING_IDS" }, 400);
    }
    validateArray(ids, "ids");
    if (ids.length > MAX_DELETE_IDS) {
      return json({ error: "Max 200 ids per delete", code: "BATCH_TOO_LARGE" }, 400);
    }

    // Resolve vector_ids from original ids
    const placeholders = ids.map(() => "?").join(",");
    const { results: rows } = await env.DB.prepare(`SELECT vector_id FROM facts WHERE id IN (${placeholders})`).bind(...ids).all();
    const vectorIds = rows.map(r => r.vector_id).filter(Boolean);

    await env.VECTORIZE.deleteByIds(vectorIds);
    await env.DB.prepare(`DELETE FROM facts WHERE id IN (${placeholders})`).bind(...ids).run();

    await env.DB.prepare(
      "INSERT INTO sync_log (action, details) VALUES (?, ?)"
    ).bind("delete", JSON.stringify({ ids, vector_ids: vectorIds })).run();

    return json({ success: true, deleted: ids.length });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}
