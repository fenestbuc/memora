import { json, MAX_DOCUMENTS } from "../constants.js";
import { validateArray, ValidationError, truncateId } from "../lib/validate.js";
import { buildMetadata } from "../lib/metadata.js";
import { twoPhaseWrite } from "../lib/vectorize.js";
import { embedTexts } from "../lib/embed.js";

export async function handleIngest(body, env) {
  try {
    const { documents } = body;
    if (!documents || !documents.length) {
      return json({ error: "No documents provided", code: "MISSING_DOCUMENTS" }, 400);
    }
    if (documents.length > MAX_DOCUMENTS) {
      return json({ error: "Max 200 documents per request", code: "BATCH_TOO_LARGE" }, 400);
    }

    const texts = documents.map(d => d.text);
    const embeddings = await embedTexts(texts, env);

    let dbInserted = 0;
    let vectorized = 0;

    for (let i = 0; i < documents.length; i++) {
      const doc = documents[i];
      let vid = doc.id || `doc_${Date.now()}_${i}`;
      const vectorId = truncateId(vid);

      const metadata = buildMetadata(doc);

      const stmt = env.DB.prepare(
        "INSERT OR REPLACE INTO facts (id, vector_id, category, content, owner_id, scope, parent_id, source_session, source_file, importance_score, pending_vector_sync, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))"
      ).bind(
        vid, vectorId, metadata.category, metadata.text,
        metadata.owner_id, metadata.scope, metadata.parent_id,
        doc.metadata?.session || null, doc.metadata?.source || null,
        doc.importance_score ?? 0.5, 1
      );

      const vector = {
        id: vectorId,
        values: embeddings[i],
        metadata,
      };

      const result = await twoPhaseWrite(env, stmt, vector, "ingest", { fact_id: vid, vector_id: vectorId });
      if (result.dbSuccess) dbInserted++;
      if (result.vectorSuccess) vectorized++;
    }

    return json({ success: true, inserted: dbInserted, vectorized });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}
