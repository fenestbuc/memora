import { json } from "../constants.js";
import { validateString, ValidationError } from "../lib/validate.js";
import { aiRun } from "../lib/embed.js";
import { handleSearch } from "./search.js";

export async function handleChat(body, env) {
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
