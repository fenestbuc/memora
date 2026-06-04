export default {
  async fetch(request, env) {
    // Timing-safe auth comparison
    const auth = request.headers.get("Authorization") || "";
    const expected = `Bearer ${env.AUTH_TOKEN}`;
    if (!timingSafeEqual(auth, expected)) {
      return json({ error: "Unauthorized", code: "AUTH_FAILED" }, 401);
    }

    const url = new URL(request.url);
    const path = url.pathname;

    try {
      if (request.method === "POST") {
        const contentLength = parseInt(request.headers.get("Content-Length") || "0");
        if (contentLength > 5 * 1024 * 1024) {
          return json({ error: "Payload too large (max 5MB)", code: "PAYLOAD_TOO_LARGE" }, 413);
        }

        let body;
        try {
          body = await request.json();
        } catch (e) {
          return json({ error: "Invalid JSON body", code: "INVALID_JSON" }, 400);
        }

        switch (path) {
          case "/embed":
            return await handleEmbed(body, env);
          case "/ingest":
            return await handleIngest(body, env);
          case "/search":
            return await handleSearch(body, env);
          case "/rerank":
            return await handleRerank(body, env);
          case "/chat":
            return await handleChat(body, env);
          case "/delete":
            return await handleDelete(body, env);
          case "/memory/import":
            return await handleMemoryImport(body, env);
          case "/memory/add":
            return await handleMemoryAdd(body, env);
          case "/memory/update":
            return await handleMemoryUpdate(body, env);
          case "/memory/delete":
            return await handleMemoryDelete(body, env);
          case "/memory/list":
            return await handleMemoryList(body, env);
          case "/memory/sync":
            return await handleMemorySync(body, env);
          case "/evaluate":
            return await handleEvaluate(body, env);
          case "/migrate":
            return await handleMigrate(body, env);
        }
      }
      if (request.method === "GET") {
        switch (path) {
          case "/health":
            return json({
              status: "ok",
              version: "1.2.0",
              models: {
                embedding: env.EMBEDDING_MODEL,
                reranker: env.RERANKER_MODEL,
                llm: env.DEFAULT_LLM
              }
            });
          case "/memory/stats":
            return await handleMemoryStats(env);
          case "/memory/export":
            return await handleMemoryExport(env, url);
        }
      }
      return json({
        error: "Not found",
        code: "ROUTE_NOT_FOUND",
        routes: [
          "POST /embed", "POST /ingest", "POST /search", "POST /rerank", "POST /chat", "POST /delete",
          "POST /memory/import", "POST /memory/add", "POST /memory/update", "POST /memory/delete", "POST /memory/list",
          "POST /memory/sync",
          "GET /health", "GET /memory/stats", "GET /memory/export"
        ]
      }, 404);
    } catch (e) {
      console.error("Unhandled error:", e);
      return json({
        error: "Internal server error",
        code: "INTERNAL_ERROR",
        message: env.DEBUG_MODE ? e.message : undefined
      }, 500);
    }
  }
};

// ============================================================
// Utilities
// ============================================================

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

async function sha256(text) {
  const data = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

function truncateId(id) {
  if (id.length <= 64) return id;
  const hash = Array.from(id).reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0);
  return id.slice(0, 50) + "_" + Math.abs(hash).toString(36);
}

async function aiRun(env, model, input, retries = 2) {
  for (let i = 0; i <= retries; i++) {
    try {
      return await env.AI.run(model, input);
    } catch (e) {
      if (i === retries) throw e;
      await new Promise(r => setTimeout(r, 500 * (i + 1)));
    }
  }
}

async function embedTexts(texts, env, useCache = true) {
  if (!useCache || !env.CACHE) {
    const result = await aiRun(env, env.EMBEDDING_MODEL, { text: texts });
    return result.data;
  }

  const embeddings = new Array(texts.length);
  const uncachedIdxs = [];

  for (let i = 0; i < texts.length; i++) {
    const key = `embed:${await sha256(texts[i])}`;
    const cached = await env.CACHE.get(key, "json");
    if (cached) {
      embeddings[i] = cached;
    } else {
      uncachedIdxs.push(i);
    }
  }

  if (uncachedIdxs.length > 0) {
    const uncachedTexts = uncachedIdxs.map(i => texts[i]);
    const result = await aiRun(env, env.EMBEDDING_MODEL, { text: uncachedTexts });
    for (let j = 0; j < uncachedIdxs.length; j++) {
      const idx = uncachedIdxs[j];
      embeddings[idx] = result.data[j];
      const key = `embed:${await sha256(texts[idx])}`;
      env.CACHE.put(key, JSON.stringify(result.data[j]), { expirationTtl: 604800 });
    }
  }

  return embeddings;
}

// ============================================================
// Validation helpers
// ============================================================

function validateString(value, name, maxLength = 10000) {
  if (typeof value !== "string") {
    throw new ValidationError(`${name} must be a string`);
  }
  if (value.length > maxLength) {
    throw new ValidationError(`${name} exceeds maximum length of ${maxLength}`);
  }
  return value;
}

function validateArray(value, name) {
  if (!Array.isArray(value)) {
    throw new ValidationError(`${name} must be an array`);
  }
  return value;
}

function sanitizeScope(scope) {
  if (!scope) return "personal";
  const allowed = ["personal", "company", "global"];
  return allowed.includes(scope) ? scope : "personal";
}

function sanitizeOwnerId(ownerId) {
  if (!ownerId || typeof ownerId !== "string") return "anonymous";
  return ownerId.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64);
}

class ValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "ValidationError";
    this.code = "VALIDATION_ERROR";
  }
}

// ============================================================
// Metadata merge: sanitized values always win over user-supplied metadata
// ============================================================

function buildMetadata(doc, extra = {}) {
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

// ============================================================
// Two-phase commit: D1 write with pending flag, then Vectorize, then clear flag
// ============================================================

async function twoPhaseWrite(env, dbStmt, vectorData, syncLogAction, syncLogDetails) {
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

// ============================================================
// RAG Routes
// ============================================================

async function handleEmbed(body, env) {
  try {
    const { text } = body;
    validateArray(text || [body.text], "text");
    const inputs = Array.isArray(text) ? text : [text];
    if (inputs.length > 100) {
      return json({ error: "Max 100 texts per request", code: "BATCH_TOO_LARGE" }, 400);
    }
    const vectors = await embedTexts(inputs, env);
    return json({ embeddings: vectors, model: env.EMBEDDING_MODEL, count: inputs.length });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

async function handleIngest(body, env) {
  try {
    const { documents } = body;
    if (!documents || !documents.length) {
      return json({ error: "No documents provided", code: "MISSING_DOCUMENTS" }, 400);
    }
    if (documents.length > 200) {
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

async function handleSearch(body, env) {
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

    const searchOpts = {
      topK: shouldRerank ? Math.min(top_k * 3, 50) : top_k,
      returnMetadata: "all"
    };

    let metadataFilter = filter || {};
    if (parent_id) metadataFilter.parent_id = parent_id;

    const safeScope = sanitizeScope(scope);
    if (safeScope === "personal" && owner_id) {
      metadataFilter.owner_id = sanitizeOwnerId(owner_id);
    } else if (safeScope === "company") {
      metadataFilter.scope = "company";
    }

    // Exclude archived facts from vector search unless explicitly requested
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

      finalResults = await hydrateWithD1(finalResults, env, include_archived);

      const result = { results: finalResults };
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

    const finalResults = await hydrateWithD1(baseResults, env, include_archived);

    const resultData = { results: finalResults };
    if (env.CACHE) env.CACHE.put(cacheKey, JSON.stringify(resultData), { expirationTtl: 3600 });
    return json(resultData);
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
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

async function handleRerank(body, env) {
  try {
    const { query, documents } = body;
    if (!query || !documents) {
      return json({ error: "Need query and documents", code: "MISSING_PARAMS" }, 400);
    }
    validateArray(documents, "documents");
    if (documents.length > 50) {
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

async function handleChat(body, env) {
  try {
    const { query, top_k = 5, system, model, rerank = true } = body;
    validateString(query, "query", 2000);

    const searchResp = await handleSearch({ query, top_k, rerank }, env);
    const searchData = await searchResp.json();

    const context = (searchData.results || [])
      .map((r, i) => `[${i + 1}] ${r.text}`)
      .join("\n\n");

    const systemPrompt = system || "You are Hermes, an AI assistant. Answer the question using the provided context. Be direct and concise. If the context doesn't contain enough information, say so.";

    const messages = [
      { role: "system", content: systemPrompt },
      { role: "user", content: `Context:\n${context}\n\nQuestion: ${query}` }
    ];

    const llm = model || env.DEFAULT_LLM;
    const response = await aiRun(env, llm, { messages });

    return json({
      answer: response.response,
      model: llm,
      sources: (searchData.results || []).map(r => ({ id: r.id, score: r.rerank_score || r.vector_score || r.score }))
    });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

async function handleDelete(body, env) {
  try {
    const { ids } = body;
    if (!ids || !ids.length) {
      return json({ error: "No ids provided", code: "MISSING_IDS" }, 400);
    }
    validateArray(ids, "ids");
    if (ids.length > 200) {
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

async function handleMemoryAdd(body, env) {
  try {
    const { category, content, source_session, source_file, parent_id, importance_score } = body;
    if (!category || !content) {
      return json({ error: "Need category and content", code: "MISSING_REQUIRED" }, 400);
    }
    validateString(content, "content", 10000);

    const id = body.id || `${category}::manual::${Date.now()}`;
    const vectorId = truncateId(id);
    const ownerId = sanitizeOwnerId(body.owner_id);
    const scope = sanitizeScope(body.scope);
    const importance = typeof importance_score === "number" ? Math.max(0, Math.min(1, importance_score)) : 0.5;

    const stmt = env.DB.prepare(
      "INSERT OR REPLACE INTO facts (id, vector_id, category, content, owner_id, scope, parent_id, source_session, source_file, importance_score, pending_vector_sync, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))"
    ).bind(id, vectorId, category, content.slice(0, 10000), ownerId, scope, parent_id || null, source_session || null, source_file || null, importance, 1);

    const vector = {
      id: vectorId,
      values: (await embedTexts([content], env))[0],
      metadata: {
        text: content.slice(0, 10000),
        category,
        owner_id: ownerId,
        scope,
        parent_id: parent_id || null,
        source: source_file || "",
        archived: 0,
        importance_score: importance,
      }
    };

    const result = await twoPhaseWrite(env, stmt, vector, "add", { fact_id: id, category });

    return json({ success: true, id, vector_sync: result.vectorSuccess });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

async function handleMemoryImport(body, env) {
  try {
    const { facts } = body;
    if (!facts || !facts.length) {
      return json({ error: "No facts provided", code: "MISSING_FACTS" }, 400);
    }
    if (facts.length > 200) {
      return json({ error: "Max 200 facts per import", code: "BATCH_TOO_LARGE" }, 400);
    }

    let inserted = 0;
    let vectorized = 0;

    for (let i = 0; i < facts.length; i++) {
      const f = facts[i];
      const vectorId = truncateId(f.id);
      const importance = typeof f.importance_score === "number" ? Math.max(0, Math.min(1, f.importance_score)) : 0.5;

      const stmt = env.DB.prepare(
        "INSERT OR REPLACE INTO facts (id, vector_id, category, content, owner_id, scope, parent_id, source_session, source_file, importance_score, pending_vector_sync, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))"
      ).bind(
        f.id, vectorId, f.category, f.content.slice(0, 10000),
        sanitizeOwnerId(f.owner_id), sanitizeScope(f.scope),
        f.parent_id || null, f.source_session || null, f.source_file || null,
        importance, 1
      );

      const vector = {
        id: vectorId,
        values: (await embedTexts([f.content], env, false))[0],
        metadata: {
          text: f.content.slice(0, 10000),
          category: f.category,
          owner_id: sanitizeOwnerId(f.owner_id),
          scope: sanitizeScope(f.scope),
          source: f.source_file || "",
          archived: 0,
          importance_score: importance,
        }
      };

      const result = await twoPhaseWrite(env, stmt, vector, "import", { fact_id: f.id });
      if (result.dbSuccess) inserted++;
      if (result.vectorSuccess) vectorized++;
    }

    return json({ success: true, inserted, vectorized });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

async function handleMemoryUpdate(body, env) {
  try {
    const { id } = body;
    if (!id) return json({ error: "Need id", code: "MISSING_ID" }, 400);

    const existing = await env.DB.prepare("SELECT * FROM facts WHERE id = ?").bind(id).first();
    if (!existing) return json({ error: "Fact not found", code: "NOT_FOUND" }, 404);

    const content = body.content || existing.content;
    const category = body.category || existing.category;
    const owner_id = body.owner_id !== undefined ? sanitizeOwnerId(body.owner_id) : existing.owner_id;
    const scope = body.scope !== undefined ? sanitizeScope(body.scope) : existing.scope;
    const parent_id = body.parent_id !== undefined ? body.parent_id : existing.parent_id;
    const source_session = body.source_session !== undefined ? body.source_session : existing.source_session;
    const source_file = body.source_file !== undefined ? body.source_file : existing.source_file;
    const importance = body.importance_score !== undefined ? Math.max(0, Math.min(1, body.importance_score)) : existing.importance_score;

    if (body.content) validateString(content, "content", 10000);

    await env.DB.prepare(
      "UPDATE facts SET content = ?, category = ?, owner_id = ?, scope = ?, parent_id = ?, source_session = ?, source_file = ?, importance_score = ?, updated_at = datetime('now') WHERE id = ?"
    ).bind(content, category, owner_id, scope, parent_id, source_session, source_file, importance, id).run();

    const vectorId = existing.vector_id || truncateId(id);
    let vectorSuccess = false;
    if (body.content) {
      try {
        const embeddings = await embedTexts([content], env);
        await env.VECTORIZE.upsert([{
          id: vectorId,
          values: embeddings[0],
          metadata: {
            text: content.slice(0, 10000),
            category,
            owner_id,
            scope,
            parent_id: parent_id || null,
            source: source_file || "",
            archived: existing.archived ?? 0,
            importance_score: importance,
          }
        }]);
        vectorSuccess = true;
      } catch (e) {
        console.error("Vectorize update failed for %s: %s", id, e.message);
        // Mark for retry by sync job
        await env.DB.prepare(
          "UPDATE facts SET pending_vector_sync = 1 WHERE id = ?"
        ).bind(id).run();
      }
    } else {
      vectorSuccess = true; // metadata-only update, no vector change needed
    }

    await env.DB.prepare(
      "INSERT INTO sync_log (action, fact_id, details) VALUES (?, ?, ?)"
    ).bind("update", id, JSON.stringify({ changed: Object.keys(body).filter(k => k !== "id"), vector_sync: vectorSuccess })).run();

    return json({ success: true, id, vector_sync: vectorSuccess });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

async function handleMemoryDelete(body, env) {
  try {
    const ids = body.ids || (body.id ? [body.id] : []);
    if (!ids.length) return json({ error: "Need id or ids", code: "MISSING_IDS" }, 400);
    validateArray(ids, "ids");
    if (ids.length > 200) {
      return json({ error: "Max 200 ids per delete", code: "BATCH_TOO_LARGE" }, 400);
    }

    const placeholders = ids.map(() => "?").join(",");
    const { results: rows } = await env.DB.prepare(`SELECT id, vector_id FROM facts WHERE id IN (${placeholders})`).bind(...ids).all();
    const vectorIds = rows.map(r => r.vector_id).filter(Boolean);

    // Delete from Vectorize first; if it fails, keep D1 rows for retry
    try {
      await env.VECTORIZE.deleteByIds(vectorIds);
    } catch (e) {
      console.error("Vectorize delete failed:", e.message);
      // Mark pending so sync can clean up later
      for (const row of rows) {
        try {
          await env.DB.prepare("UPDATE facts SET pending_vector_sync = 1 WHERE id = ?").bind(row.id).run();
        } catch (_) { /* ignore */ }
      }
      return json({ error: "Vectorize delete failed", code: "VECTOR_DELETE_FAILED", partial: true }, 502);
    }

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

async function handleMemoryList(body, env) {
  try {
    const { category, search, session, owner_id, scope, archived, limit = 50, offset = 0 } = body;

    if (limit > 500) {
      return json({ error: "Max limit is 500", code: "LIMIT_TOO_HIGH" }, 400);
    }

    let sql = "SELECT * FROM facts WHERE 1=1";
    const params = [];

    if (category) { sql += " AND category = ?"; params.push(category); }
    if (session) { sql += " AND source_session LIKE ?"; params.push(`%${session}%`); }
    if (search) { sql += " AND content LIKE ?"; params.push(`%${search}%`); }
    if (owner_id) { sql += " AND owner_id = ?"; params.push(sanitizeOwnerId(owner_id)); }
    if (scope) { sql += " AND scope = ?"; params.push(sanitizeScope(scope)); }
    if (archived !== undefined) { sql += " AND archived = ?"; params.push(archived ? 1 : 0); }
    else { sql += " AND (archived = 0 OR archived IS NULL)"; }

    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?";
    params.push(limit, offset);

    const result = await env.DB.prepare(sql).bind(...params).all();

    let countSql = "SELECT COUNT(*) as total FROM facts WHERE 1=1";
    const countParams = [];
    if (category) { countSql += " AND category = ?"; countParams.push(category); }
    if (session) { countSql += " AND source_session LIKE ?"; countParams.push(`%${session}%`); }
    if (search) { countSql += " AND content LIKE ?"; countParams.push(`%${search}%`); }
    if (owner_id) { countSql += " AND owner_id = ?"; countParams.push(sanitizeOwnerId(owner_id)); }
    if (scope) { countSql += " AND scope = ?"; countParams.push(sanitizeScope(scope)); }
    if (archived !== undefined) { countSql += " AND archived = ?"; countParams.push(archived ? 1 : 0); }
    else { countSql += " AND (archived = 0 OR archived IS NULL)"; }

    const countResult = await env.DB.prepare(countSql).bind(...countParams).first();

    return json({
      facts: result.results,
      total: countResult?.total || 0,
      limit,
      offset
    });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

async function handleMemorySync(body, env) {
  try {
    const { batch_size = 50 } = body || {};
    const { results: pending } = await env.DB.prepare(
      "SELECT id, vector_id, category, content, owner_id, scope, parent_id, source_session, source_file FROM facts WHERE pending_vector_sync = 1 LIMIT ?"
    ).bind(batch_size).all();

    if (!pending || !pending.length) {
      return json({ success: true, synced: 0, message: "No pending vectors to sync" });
    }

    const texts = pending.map(f => f.content);
    const embeddings = await embedTexts(texts, env, false);

    const vectors = pending.map((f, i) => ({
      id: f.vector_id,
      values: embeddings[i],
      metadata: {
        text: f.content.slice(0, 10000),
        category: f.category,
        owner_id: f.owner_id,
        scope: f.scope,
        parent_id: f.parent_id || null,
        source: f.source_file || ""
      }
    }));

    await env.VECTORIZE.upsert(vectors);

    // Clear pending flag by primary id (safer than vector_id in case of collisions)
    const ids = pending.map(f => f.id);
    const placeholders = ids.map(() => "?").join(",");
    await env.DB.prepare(`UPDATE facts SET pending_vector_sync = 0 WHERE id IN (${placeholders})`).bind(...ids).run();

    return json({ success: true, synced: pending.length });
  } catch (e) {
    console.error("Memory sync failed:", e);
    return json({ error: e.message, code: "SYNC_FAILED" }, 500);
  }
}

async function handleMemoryStats(env) {
  try {
    if (env.CACHE) {
      const cached = await env.CACHE.get("stats:facts", "json");
      if (cached) return json({ ...cached, cached: true });
    }

    const result = await env.DB.prepare(
      "SELECT category, COUNT(*) as count FROM facts GROUP BY category ORDER BY count DESC"
    ).all();

    const total = await env.DB.prepare("SELECT COUNT(*) as total FROM facts WHERE archived = 0 OR archived IS NULL").first();
    const archivedTotal = await env.DB.prepare("SELECT COUNT(*) as total FROM facts WHERE archived = 1").first();

    const scopeStats = await env.DB.prepare(
      "SELECT scope, COUNT(*) as count FROM facts GROUP BY scope"
    ).all();

    const pendingSync = await env.DB.prepare(
      "SELECT COUNT(*) as total FROM facts WHERE pending_vector_sync = 1"
    ).first();

    const stats = {
      total: total?.total || 0,
      archived: archivedTotal?.total || 0,
      pending_vector_sync: pendingSync?.total || 0,
      by_category: result.results,
      by_scope: scopeStats.results || []
    };

    if (env.CACHE) env.CACHE.put("stats:facts", JSON.stringify(stats), { expirationTtl: 300 });
    return json(stats);
  } catch (e) {
    throw e;
  }
}

async function handleMemoryExport(env, url) {
  try {
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "1000"), 5000);
    const offset = parseInt(url.searchParams.get("offset") || "0");

    const result = await env.DB.prepare(
      "SELECT * FROM facts ORDER BY category, id LIMIT ? OFFSET ?"
    ).bind(limit, offset).all();

    const countResult = await env.DB.prepare("SELECT COUNT(*) as total FROM facts").first();

    return json({
      facts: result.results,
      count: result.results.length,
      total: countResult?.total || 0,
      limit,
      offset,
      has_more: (offset + result.results.length) < (countResult?.total || 0)
    });
  } catch (e) {
    throw e;
  }
}

async function handleMigrate(body, env) {
  // STRICT allow-list for migrations — no arbitrary column names
  const ALLOWED_COLUMNS = new Set([
    "parent_id", "owner_id", "scope", "importance_score", "decayed_at", "archived", "vector_id", "pending_vector_sync"
  ]);

  try {
    const migrations = body.migrations || [];
    if (!Array.isArray(migrations) || migrations.length === 0) {
      return json({ error: "migrations must be a non-empty array", code: "MISSING_PARAMS" }, 400);
    }
    if (migrations.length > 10) {
      return json({ error: "Max 10 migrations per request", code: "BATCH_TOO_LARGE" }, 400);
    }

    const results = [];

    for (const col of migrations) {
      if (typeof col !== "string" || !ALLOWED_COLUMNS.has(col)) {
        results.push({ column: col, status: "rejected", reason: "Not in allow-list" });
        continue;
      }

      try {
        await env.DB.prepare(`ALTER TABLE facts ADD COLUMN ${col} TEXT`).run();
        results.push({ column: col, status: "added" });
      } catch (e) {
        if (e.message && e.message.includes("duplicate column")) {
          results.push({ column: col, status: "already_exists" });
        } else {
          results.push({ column: col, status: "error", message: e.message });
        }
      }
    }

    // Create indexes for known columns
    const knownIndexes = [
      ["idx_facts_owner", "owner_id"],
      ["idx_facts_scope", "scope"],
      ["idx_facts_importance", "importance_score"],
      ["idx_facts_archived", "archived"],
      ["idx_facts_vector", "vector_id"],
      ["idx_facts_pending_sync", "pending_vector_sync"],
    ];

    for (const [idxName, colName] of knownIndexes) {
      try {
        await env.DB.prepare(`CREATE INDEX IF NOT EXISTS ${idxName} ON facts(${colName})`).run();
      } catch (e) {
        results.push({ index: idxName, status: "error", message: e.message });
      }
    }

    return json({ success: true, migrations: results });
  } catch (e) {
    return json({ error: e.message, code: "MIGRATION_ERROR" }, 500);
  }
}

// ============================================================================
// Evaluation Endpoint
// ============================================================================
async function handleEvaluate(body, env) {
  try {
    const { top_k = 10, parent_id, use_reranking } = body || {};

    if (top_k > 100) {
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
