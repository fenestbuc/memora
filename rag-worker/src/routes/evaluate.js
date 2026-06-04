import { json, MAX_EVAL_TOP_K } from "../constants.js";
import { ValidationError } from "../lib/validate.js";
import { embedTexts, aiRun } from "../lib/embed.js";

export async function handleEvaluate(body, env) {
  try {
    const { top_k = 10, parent_id, use_reranking } = body || {};

    if (top_k > MAX_EVAL_TOP_K) {
      return json({ error: "top_k max is 100", code: "LIMIT_TOO_HIGH" }, 400);
    }

    const result = await env.DB.prepare(`SELECT id, content FROM facts WHERE category = 'eval_golden'`).all();
    const evalRecords = result.results || [];

    if (evalRecords.length === 0) {
      return json({ message: "No eval_golden facts found", mrr: 0, hit_rate: 0, total: 0 });
    }

    let mrrSum = 0;
    let hitCount = 0;

    for (const record of evalRecords) {
      try {
        const data = JSON.parse(record.content);
        const { query, expected_fact_id } = data;

        const embeddings = await embedTexts([query], env);
        const queryVector = JSON.parse(JSON.stringify(embeddings[0] || []));

        const queryOpts = {
          topK: use_reranking ? Math.min(top_k * 3, 50) : top_k,
          returnMetadata: use_reranking ? "all" : undefined
        };
        if (parent_id) queryOpts.filter = { parent_id };

        const matches = await env.VECTORIZE.query(queryVector, queryOpts);

        let matchResults = matches.matches || [];

        if (use_reranking && matchResults.length > 0) {
          const candidateTexts = matchResults.map(m => m.metadata?.text || "");
          const reranked = await aiRun(env, env.RERANKER_MODEL, {
            query: query,
            contexts: candidateTexts.map(t => ({ text: t }))
          });

          matchResults = reranked.response
            .map((r, i) => ({ ...matchResults[i], score: r.score, reranked: true }))
            .sort((a, b) => b.score - a.score);
        }

        const rankIndex = matchResults.findIndex(m => m.id === expected_fact_id);
        if (rankIndex !== -1) {
          hitCount++;
          mrrSum += (1.0 / (rankIndex + 1));
        }
      } catch (e) {
        console.error(`Failed to eval record ${record.id}:`, e.message);
      }
    }

    const total = evalRecords.length;
    return json({ total, mrr: mrrSum / total, hit_rate: hitCount / total });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}
