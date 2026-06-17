import { handleEmbed } from "./routes/embed.js";
import { handleIngest } from "./routes/ingest.js";
import { handleSearch } from "./routes/search.js";
import { handleRerank } from "./routes/rerank.js";
import { handleChat } from "./routes/chat.js";
import { handleThink } from "./routes/think.js";
import { handleDelete } from "./routes/delete.js";
import {
  handleMemoryAdd,
  handleMemoryImport,
  handleMemoryUpdate,
  handleMemoryDelete,
  handleMemoryList,
  handleMemorySync,
  handleMemoryStats,
  handleMemoryExport,
  handleMigrate
} from "./routes/memory.js";
import { handleEvaluate } from "./routes/evaluate.js";

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
          case "/think":
            return await handleThink(body, env);
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
          "POST /embed", "POST /ingest", "POST /search", "POST /rerank", "POST /chat", "POST /think", "POST /delete",
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
