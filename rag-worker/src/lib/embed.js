export async function sha256(text) {
  const data = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

export async function aiRun(env, model, input, retries = 2) {
  for (let i = 0; i <= retries; i++) {
    try {
      return await env.AI.run(model, input);
    } catch (e) {
      if (i === retries) throw e;
      await new Promise(r => setTimeout(r, 500 * (i + 1)));
    }
  }
}

export async function embedTexts(texts, env, useCache = true) {
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
