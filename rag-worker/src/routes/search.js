import { json } from "../constants.js";
import { validateString, ValidationError, sanitizeScope, sanitizeOwnerId } from "../lib/validate.js";
import { embedTexts, sha256, aiRun } from "../lib/embed.js";

export async function handleSearch(body, env) {
  try {
    const { query, top_k = 10, rerank = true, filter, parent_id, use_reranking, owner_id, scope, include_archived } = body;

    validateString(query, "query", 2000);

    const shouldRerank = use_reranking !== undefined ? use_reranking : rerank;

    const cacheKey = `search:${await sha256(JSON.stringify({ query, top_k, rerank: shouldRerank, filter, parent_id, owner_id, scope }))}`;
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

    // Vectorize metadata filtering in V2 only works for explicitly indexed
    // metadata fields.  owner_id, scope and parent_id are not guaranteed to be
    // indexed on every deployment, so we keep them as D1-only filters and only
    // use the numeric archived filter for vector pre-filtering.
    const d1Filters = buildD1Filters({ parent_id, owner_id, scope });

    // Increase the candidate pool when D1 filtering will discard rows,
    // capped at Vectorize's max topK when returnMetadata="all".
    const needsPostFilter = Object.keys(d1Filters).length > 0;
    const candidateTopK = shouldRerank
      ? Math.min(numericTopK * (needsPostFilter ? 10 : 3), 50)
      : Math.min(numericTopK * (needsPostFilter ? 5 : 1), 50);

    const searchOpts = {
      topK: candidateTopK,
      returnMetadata: "all"
    };

    const metadataFilter = filter || {};
    if (!include_archived) {
      metadataFilter.archived = 0;
    }

    if (Object.keys(metadataFilter).length > 0) {
      searchOpts.filter = metadataFilter;
    }

    const matches = await env.VECTORIZE.query(queryVector, searchOpts);

    if (!shouldRerank || !matches.matches || matches.matches.length === 0) {
      let finalResults = (matches.matches || []).slice(0, top_k).map(m => ({
        id: m.id,
        score: m.score,
        text: m.metadata?.text || "",
        metadata: m.metadata
      }));

      finalResults = await hydrateWithD1(finalResults, env, include_archived, d1Filters);

      const result = { results: finalResults.slice(0, numericTopK) };
      if (env.CACHE) env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: 3600 });
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
      .sort((a, b) => b.rerank_score - a.rerank_score)
      .slice(0, top_k);

    const finalResults = await hydrateWithD1(baseResults, env, include_archived, d1Filters);

    const resultData = { results: finalResults.slice(0, numericTopK) };
    if (env.CACHE) env.CACHE.put(cacheKey, JSON.stringify(resultData), { expirationTtl: 3600 });
    return json(resultData);
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

export function buildD1Filters({ parent_id, owner_id, scope }) {
  const d1Filters = {};
  if (parent_id) d1Filters.parent_id = parent_id;
  const safeScope = sanitizeScope(scope);
  if (safeScope === "personal" && owner_id) {
    d1Filters.owner_id = sanitizeOwnerId(owner_id);
  } else if (safeScope === "company") {
    d1Filters.scope = "company";
  }
  return d1Filters;
}

async function hydrateWithD1(results, env, includeArchived = false, d1Filters = {}) {
  if (!env.DB || results.length === 0) return results;

  const ids = results.map(r => r.id);
  const placeholders = ids.map(() => '?').join(',');
  let sql = `SELECT * FROM facts WHERE vector_id IN (${placeholders})`;
  const params = [...ids];

  if (!includeArchived) {
    sql += " AND (archived = 0 OR archived IS NULL)";
  }

  if (d1Filters.owner_id) {
    sql += " AND owner_id = ?";
    params.push(d1Filters.owner_id);
  }
  if (d1Filters.scope) {
    sql += " AND scope = ?";
    params.push(d1Filters.scope);
  }
  if (d1Filters.parent_id) {
    sql += " AND parent_id = ?";
    params.push(d1Filters.parent_id);
  }

  const { results: d1Facts } = await env.DB.prepare(sql).bind(...params).all();

  results = results.map(r => {
    const fact = d1Facts.find(f => f.vector_id === r.id);
    return fact ? { ...r, ...fact } : r;
  });

  // Drop vector-only rows that do not pass D1 filters.
  const filterKeys = Object.keys(d1Filters);
  if (filterKeys.length > 0) {
    results = results.filter(r => filterKeys.every(k => r[k] === d1Filters[k] || (k === "parent_id" && !d1Filters[k] && !r[k])));
  }

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
