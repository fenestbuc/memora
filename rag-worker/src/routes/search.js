import { json } from "../constants.js";
import { validateString, ValidationError, sanitizeScope, sanitizeOwnerId } from "../lib/validate.js";
import { embedTexts, sha256, aiRun } from "../lib/embed.js";

export async function handleSearch(body, env) {
  try {
    const { query, top_k = 10, rerank = true, filter, parent_id, use_reranking, owner_id, scope, include_archived } = body;

    validateString(query, "query", 2000);

    const shouldRerank = use_reranking !== undefined ? use_reranking : rerank;

    const cacheKey = `search:v2:${await sha256(JSON.stringify({ query, top_k, rerank: shouldRerank, filter, parent_id, owner_id, scope }))}`;
    if (env.CACHE) {
      const cached = await env.CACHE.get(cacheKey, "json");
      if (cached) return json({ ...cached, cached: true });
    }

    const embeddings = await embedTexts([query], env);
    const queryVector = JSON.parse(JSON.stringify(embeddings[0] || []));
    if (!queryVector || queryVector.length === 0) {
      return json({ error: "Empty embedding vector", code: "EMPTY_VECTOR" }, 400);
    }

    const numericTopK = Number.isFinite(top_k) ? Math.max(1, Math.floor(top_k)) : 10;
    const searchOpts = {
      topK: shouldRerank ? Math.min(numericTopK * 3, 50) : numericTopK,
      returnMetadata: "all"
    };

    let metadataFilter = filter || {};
    if (parent_id) metadataFilter.parent_id = parent_id;

    const safeScope = sanitizeScope(scope);
    // Do not push owner/scope filters into Vectorize. This index predates
    // metadata indexes for those fields, and Cloudflare returns zero matches
    // when a filter targets an unindexed property. Fetch a wider candidate set
    // and enforce access scope after D1 hydration instead.
    const needsAccessFilter = Boolean(owner_id || safeScope === "company");

    // Only add this metadata filter when explicitly requested. Legacy vectors
    // predate the archived metadata field and disappear from Vectorize queries
    // whenever archived=0 is applied implicitly. D1 hydration still excludes
    // archived rows from the returned result set by default.
    if (include_archived === false) {
      metadataFilter.archived = 0;
    }

    if (Object.keys(metadataFilter).length > 0) {
      searchOpts.filter = metadataFilter;
    }

    if (needsAccessFilter) {
      searchOpts.topK = 50;
    }

    const matches = await env.VECTORIZE.query(queryVector, searchOpts);

    if (!shouldRerank || !matches.matches || matches.matches.length === 0) {
      let finalResults = (matches.matches || []).map(m => ({
        id: m.id,
        score: m.score,
        text: m.metadata?.text || "",
        metadata: m.metadata
      }));

      finalResults = await hydrateWithD1(finalResults, env, include_archived);
      finalResults = filterAccessResults(finalResults, owner_id, safeScope);
      finalResults = finalResults.slice(0, numericTopK);

      const result = { results: finalResults };
      if (env.CACHE && finalResults.length > 0) env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: 300 });
      return json(result);
    }

    const candidateTexts = matches.matches.map(m => m.metadata?.text || "");
    const reranked = await aiRun(env, env.RERANKER_MODEL, {
      query: query,
      contexts: candidateTexts.map(t => ({ text: t }))
    });

    const baseResults = reranked.response
      .map((r, i) => ({
        id: matches.matches[i].id,
        vector_score: matches.matches[i].score,
        rerank_score: r.score,
        text: candidateTexts[i],
        metadata: matches.matches[i].metadata
      }))
      .sort((a, b) => b.rerank_score - a.rerank_score);

    let finalResults = await hydrateWithD1(baseResults, env, include_archived);
    finalResults = filterAccessResults(finalResults, owner_id, safeScope);
    finalResults = finalResults.slice(0, numericTopK);

    const resultData = { results: finalResults };
    if (env.CACHE && finalResults.length > 0) env.CACHE.put(cacheKey, JSON.stringify(resultData), { expirationTtl: 300 });
    return json(resultData);
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

function filterAccessResults(results, ownerId, scope) {
  if (scope === "company") {
    return results.filter(r => (r.scope ?? r.metadata?.scope) === "company");
  }
  if (ownerId) {
    const safeOwner = sanitizeOwnerId(ownerId);
    return results.filter(r => (r.owner_id ?? r.metadata?.owner_id) === safeOwner);
  }
  return results;
}

async function hydrateWithD1(results, env, includeArchived = false) {
  if (!env.DB || results.length === 0) return results;

  const ids = results.map(r => r.id);
  const placeholders = ids.map(() => '?').join(',');
  let sql = `SELECT * FROM facts WHERE vector_id IN (${placeholders})`;
  if (!includeArchived) {
    sql += " AND (archived = 0 OR archived IS NULL)";
  }
  const { results: d1Facts } = await env.DB.prepare(sql).bind(...ids).all();

  results = results.map(r => {
    const fact = d1Facts.find(f => f.vector_id === r.id);
    return fact ? { ...r, ...fact } : r;
  });

  const parentIds = [...new Set(results.map(r => r.parent_id).filter(id => id))];
  if (parentIds.length > 0) {
    const pPlaceholders = parentIds.map(() => '?').join(',');
    const { results: d1Parents } = await env.DB.prepare(`SELECT * FROM facts WHERE id IN (${pPlaceholders})`).bind(...parentIds).all();
    results = results.map(r => {
      if (r.parent_id) {
        r.parent_fact = d1Parents.find(p => p.id === r.parent_id) || null;
      }
      return r;
    });
  }

  return results;
}
