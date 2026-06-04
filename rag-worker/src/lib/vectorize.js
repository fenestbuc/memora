import { embedTexts } from "./embed.js";

export async function twoPhaseWrite(env, dbStmt, vectorData, syncLogAction, syncLogDetails) {
  // Phase 1: Write to D1 with pending flag
  await dbStmt.run();

  let vectorSuccess = false;
  try {
    if (Array.isArray(vectorData)) {
      await env.VECTORIZE.upsert(vectorData);
    } else {
      await env.VECTORIZE.upsert([vectorData]);
    }
    vectorSuccess = true;
  } catch (e) {
    console.error("Vectorize write failed:", e.message);
  }

  if (vectorSuccess) {
    // Phase 3: Clear pending flag by fact id (primary key)
    try {
      const factId = syncLogDetails.fact_id;
      if (factId) {
        await env.DB.prepare("UPDATE facts SET pending_vector_sync = 0 WHERE id = ?")
          .bind(factId).run();
      }
    } catch (e) {
      console.error("Failed to clear pending_vector_sync:", e.message);
    }
  }

  // Log the action regardless of vector success
  try {
    await env.DB.prepare(
      "INSERT INTO sync_log (action, fact_id, details) VALUES (?, ?, ?)"
    ).bind(syncLogAction, syncLogDetails.fact_id, JSON.stringify({ ...syncLogDetails, vector_sync: vectorSuccess })).run();
  } catch (e) {
    console.error("Sync log write failed:", e.message);
  }

  return { dbSuccess: true, vectorSuccess };
}
