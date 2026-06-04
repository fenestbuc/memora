import { json, MAX_IMPORT_FACTS, MAX_DELETE_IDS, MAX_MEMORY_LIST_LIMIT, MAX_EXPORT_LIMIT, MAX_MIGRATIONS, ALLOWED_COLUMNS } from "../constants.js";
import { validateString, validateArray, ValidationError, sanitizeScope, sanitizeOwnerId, truncateId } from "../lib/validate.js";
import { embedTexts } from "../lib/embed.js";
import { twoPhaseWrite } from "../lib/vectorize.js";

export async function handleMemoryAdd(body, env) {
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

export async function handleMemoryImport(body, env) {
  try {
    const { facts } = body;
    if (!facts || !facts.length) {
      return json({ error: "No facts provided", code: "MISSING_FACTS" }, 400);
    }
    if (facts.length > MAX_IMPORT_FACTS) {
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

export async function handleMemoryUpdate(body, env) {
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

export async function handleMemoryDelete(body, env) {
  try {
    const ids = body.ids || (body.id ? [body.id] : []);
    if (!ids.length) return json({ error: "Need id or ids", code: "MISSING_IDS" }, 400);
    validateArray(ids, "ids");
    if (ids.length > MAX_DELETE_IDS) {
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

export async function handleMemoryList(body, env) {
  try {
    const { category, search, session, owner_id, scope, archived, limit = 50, offset = 0 } = body;

    if (limit > MAX_MEMORY_LIST_LIMIT) {
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

export async function handleMemorySync(body, env) {
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

export async function handleMemoryStats(env) {
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

export async function handleMemoryExport(env, url) {
  try {
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "1000"), MAX_EXPORT_LIMIT);
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

export async function handleMigrate(body, env) {
  try {
    const migrations = body.migrations || [];
    if (!Array.isArray(migrations) || migrations.length === 0) {
      return json({ error: "migrations must be a non-empty array", code: "MISSING_PARAMS" }, 400);
    }
    if (migrations.length > MAX_MIGRATIONS) {
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
