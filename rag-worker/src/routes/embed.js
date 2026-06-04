import { json, MAX_TEXTS_PER_REQUEST } from "../constants.js";
import { validateArray, ValidationError } from "../lib/validate.js";
import { embedTexts } from "../lib/embed.js";

export async function handleEmbed(body, env) {
  try {
    const { text } = body;
    validateArray(text || [body.text], "text");
    const inputs = Array.isArray(text) ? text : [text];
    if (inputs.length > MAX_TEXTS_PER_REQUEST) {
      return json({ error: "Max 100 texts per request", code: "BATCH_TOO_LARGE" }, 400);
    }
    const vectors = await embedTexts(inputs, env);
    return json({ embeddings: vectors, model: env.EMBEDDING_MODEL, count: inputs.length });
  } catch (e) {
    if (e instanceof ValidationError) return json({ error: e.message, code: e.code }, 400);
    throw e;
  }
}
