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
