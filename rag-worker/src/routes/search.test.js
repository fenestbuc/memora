import { describe, it } from 'node:test';
import assert from 'node:assert';
import { handleSearch } from './search.js';

function fakeEnv(vector = new Array(768).fill(0.1)) {
  return {
    AI: {
      run: async () => ({ data: vector }),
    },
    VECTORIZE: {
      query: async (_vector, opts) => ({ matches: [], searchOpts: opts }),
    },
    EMBEDDING_MODEL: '@cf/baai/bge-m3',
  };
}

describe('handleSearch scope metadata filtering', () => {
  it('filters personal search by scope=personal and owner_id', async () => {
    let capturedOpts = null;
    const env = {
      ...fakeEnv(),
      VECTORIZE: {
        query: async (_vector, opts) => {
          capturedOpts = opts;
          return { matches: [] };
        },
      },
    };

    const resp = await handleSearch(
      { query: 'quarterly plan', owner_id: 'alice', scope: 'personal' },
      env,
    );
    assert.strictEqual(resp.status, 200);
    assert.deepStrictEqual(capturedOpts.filter, {
      scope: 'personal',
      owner_id: 'alice',
      archived: 0,
    });
  });

  it('filters company search by scope=company and owner_id', async () => {
    let capturedOpts = null;
    const env = {
      ...fakeEnv(),
      VECTORIZE: {
        query: async (_vector, opts) => {
          capturedOpts = opts;
          return { matches: [] };
        },
      },
    };

    const resp = await handleSearch(
      { query: 'team budget', owner_id: 'alice', scope: 'company' },
      env,
    );
    assert.strictEqual(resp.status, 200);
    assert.deepStrictEqual(capturedOpts.filter, {
      scope: 'company',
      owner_id: 'alice',
      archived: 0,
    });
  });

  it('defaults missing scope to personal and applies owner_id', async () => {
    let capturedOpts = null;
    const env = {
      ...fakeEnv(),
      VECTORIZE: {
        query: async (_vector, opts) => {
          capturedOpts = opts;
          return { matches: [] };
        },
      },
    };

    await handleSearch({ query: 'notes', owner_id: 'bob' }, env);
    assert.deepStrictEqual(capturedOpts.filter, {
      scope: 'personal',
      owner_id: 'bob',
      archived: 0,
    });
  });
});

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
