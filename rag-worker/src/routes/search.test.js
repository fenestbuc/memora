import { describe, it } from 'node:test';
import assert from 'node:assert';
import { handleSearch } from './search.js';

function fakeEnv() {
  const vector = new Array(1024).fill(0.02);
  return {
    AI: {
      run: async (model, input) => {
        if (model.includes('rerank')) {
          return { response: (input.contexts || []).map((_, i) => ({ score: 0.1 * (i + 1) })) };
        }
        return { data: [vector] };
      },
    },
    VECTORIZE: {
      query: async () => ({ matches: [] }),
    },
    EMBEDDING_MODEL: '@cf/baai/bge-m3',
    RERANKER_MODEL: '@cf/baai/bge-reranker-base',
    DEFAULT_LLM: '@cf/meta/llama-2-7b-chat-int8',
  };
}

function capturingVectorEnv() {
  const queries = [];
  const env = {
    ...fakeEnv(),
    VECTORIZE: {
      query: async (vector, opts) => {
        queries.push(opts);
        return { matches: [] };
      },
    },
  };
  return { env, queries };
}

describe('handleSearch cache behavior', () => {
  it('uses a versioned cache namespace so stale empty results are invalidated', async () => {
    const gets = [];
    const env = {
      ...fakeEnv(),
      CACHE: {
        get: async (key) => { gets.push(key); return null; },
        put: async () => {},
      },
    };

    await handleSearch({ query: 'cache namespace check' }, env);
    assert.ok(gets[0].startsWith('search:v2:'), `unexpected cache key: ${gets[0]}`);
  });

  it('does not cache empty result sets', async () => {
    const puts = [];
    const env = {
      ...fakeEnv(),
      CACHE: {
        get: async () => null,
        put: async (key, value, opts) => { puts.push({ key, value, opts }); },
      },
    };

    const resp = await handleSearch({ query: 'xyz missing topic' }, env);
    assert.strictEqual(resp.status, 200);
    const body = await resp.json();
    assert.deepStrictEqual(body.results, []);
    assert.strictEqual(puts.filter(p => p.key.startsWith('search:')).length, 0, 'empty search results should not be cached');
  });

  it('caches non-empty result sets', async () => {
    const puts = [];
    const env = {
      ...fakeEnv(),
      VECTORIZE: {
        query: async () => ({
          matches: [
            {
              id: 'fact-1',
              score: 0.9,
              metadata: { text: 'Relevant text', category: 'memory', owner_id: 'anonymous', scope: 'personal', archived: 0 },
            },
          ],
        }),
      },
      DB: {
        prepare: () => ({
          bind: () => ({
            all: async () => ({ results: [] }),
          }),
        }),
      },
      CACHE: {
        get: async () => null,
        put: async (key, value, opts) => { puts.push({ key, value, opts }); },
      },
    };

    const resp = await handleSearch({ query: 'find relevant text', top_k: 1, rerank: false }, env);
    const body = await resp.json();
    assert.strictEqual(body.results.length, 1);
    const searchPuts = puts.filter(p => p.key.startsWith('search:'));
    assert.strictEqual(searchPuts.length, 1, 'non-empty search results should be cached');
    assert.strictEqual(searchPuts[0].opts.expirationTtl, 300);
  });
});

describe('handleSearch metadata filtering', () => {
  it('does not apply an archived filter unless the caller explicitly requests one', async () => {
    const { env, queries } = capturingVectorEnv();
    await handleSearch({ query: 'legacy corpus search' }, env);
    assert.strictEqual(queries[0].filter, undefined);
  });

  it('does not send owner and scope metadata filters to Vectorize', async () => {
    const { env, queries } = capturingVectorEnv();
    await handleSearch(
      { query: 'personal search', owner_id: 'alice', scope: 'personal' },
      env,
    );
    assert.strictEqual(queries[0].filter, undefined);
  });

  it('applies archived=0 only when include_archived is explicitly false', async () => {
    const { env, queries } = capturingVectorEnv();
    await handleSearch({ query: 'active facts', include_archived: false }, env);
    assert.deepStrictEqual(queries[0].filter, { archived: 0 });
  });
});
