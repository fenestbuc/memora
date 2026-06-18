import { json } from "../constants.js";
import { validateString, ValidationError, sanitizeScope } from "../lib/validate.js";
import { aiRun } from "../lib/embed.js";
import { handleSearch } from "./search.js";

const THINK_SYSTEM_PROMPT = `You are a careful research assistant. Answer the user's question using ONLY the retrieved facts below.

Rules:
- Cite every substantive claim with a marker like [category/date] using the metadata provided for each fact.
- Do not invent facts. If the context does not contain enough information, say so explicitly.
- Prefer concise, direct answers over exhaustive lists.
- At the end, output a short "## Gaps" section that lists what information is missing or uncertain and should be researched next.
- List the facts you used as a numbered bibliography with IDs and short descriptions.`;

export async function handleThink(body, env) {
  try {
    const { query, top_k = 10, model, scope, owner_id } = body;
    validateString(query, "query", 2000);

    const safeScope = sanitizeScope(scope);
    const searchResp = await handleSearch(
      { query, top_k, rerank: true, scope: safeScope, owner_id },
      env,
    );
    const searchData = await searchResp.json();
    const results = searchData.results || [];

    if (results.length === 0) {
      return json({
        answer: "I could not find any relevant facts for this question.",
        gaps: ["No facts matched the query. Try adding context or rephrasing the question."],
        sources: [],
        model: model || env.DEFAULT_LLM,
      });
    }

    const context = results
      .map((r, i) => {
        const meta = r.metadata || {};
        const cat = meta.category || "memory";
        const date = meta.created_at ? meta.created_at.slice(0, 10) : "";
        const owner = meta.owner_id || "";
        const header = `[${i + 1}] category=${cat} date=${date} owner=${owner} id=${r.id}`;
        return `${header}\n${r.text || ""}`;
      })
      .join("\n\n");

    const messages = [
      { role: "system", content: THINK_SYSTEM_PROMPT },
      {
        role: "user",
        content: `Retrieved facts:\n${context}\n\nQuestion: ${query}\n\nPlease answer the question and include a "## Gaps" section at the end.`,
      },
    ];

    const llm = model || env.DEFAULT_LLM;
    const response = await aiRun(env, llm, { messages });

    const answer = response.response || "";
    const structuredGaps = extractGaps(answer);

    return json({
      answer,
      model: llm,
      sources: results.map((r) => ({
        id: r.id,
        category: r.metadata?.category || r.category || "memory",
        date: (r.metadata?.created_at || r.created_at || "").slice(0, 10),
        score: r.rerank_score || r.vector_score || r.score,
        text_preview: (r.text || "").slice(0, 120),
      })),
      gaps: structuredGaps,
    });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}

/**
 * Extract a structured gaps list from the LLM answer text.
 *
 * The system prompt asks the model to end with a "## Gaps" section.  We parse
 * the text after that heading into non-empty bullet lines.  If the section is
 * missing or empty, we return an empty array so callers can still rely on the
 * shape of the response.
 */
function extractGaps(answer) {
  if (!answer) return [];
  const marker = /##\s*Gaps/i;
  const idx = answer.search(marker);
  if (idx === -1) return [];

  const section = answer.slice(idx + answer.slice(idx).match(marker)[0].length);
  return section
    .split(/\n/)
    .map((line) => line.replace(/^\s*[-*•]|^\s*\d+[.)]\s*/, "").trim())
    .filter((line) => line.length > 0 && !line.match(/^#{1,6}\s/));
}
