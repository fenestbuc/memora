import { json, MAX_RERANK_DOCUMENTS } from "../constants.js";
import { validateArray, ValidationError } from "../lib/validate.js";
import { aiRun } from "../lib/embed.js";

export async function handleRerank(body, env) {
  try {
    const { query, documents } = body;
    if (!query || !documents) {
      return json({ error: "Need query and documents", code: "MISSING_PARAMS" }, 400);
    }
    validateArray(documents, "documents");
    if (documents.length > MAX_RERANK_DOCUMENTS) {
      return json({ error: "Max 50 documents for reranking", code: "BATCH_TOO_LARGE" }, 400);
    }
    const contexts = documents.map(d => typeof d === "string" ? { text: d } : d);
    const result = await aiRun(env, env.RERANKER_MODEL, { query, contexts });
    return json({ results: result.response });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}
