import { describe, it } from 'node:test';
import assert from 'node:assert';
import { handleSearch, buildD1Filters } from './search.js';

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

describe('buildD1Filters', () => {
  it('maps personal scope with owner_id to owner_id filter', () => {
    assert.deepStrictEqual(buildD1Filters({ scope: 'personal', owner_id: 'TestCrud' }), {
      owner_id: 'TestCrud'
    });
  });

  it('sanitizes owner_id', () => {
    assert.deepStrictEqual(buildD1Filters({ scope: 'personal', owner_id: 'test@user!!' }), {
      owner_id: 'testuser'
    });
  });

  it('defaults missing scope to personal and applies owner_id', () => {
    assert.deepStrictEqual(buildD1Filters({ owner_id: 'Alice' }), {
      owner_id: 'Alice'
    });
  });

  it('maps company scope to scope filter', () => {
    assert.deepStrictEqual(buildD1Filters({ scope: 'company' }), {
      scope: 'company'
    });
  });

  it('ignores owner_id for company scope', () => {
    assert.deepStrictEqual(buildD1Filters({ scope: 'company', owner_id: 'TestCrud' }), {
      scope: 'company'
    });
  });

  it('adds parent_id filter when provided', () => {
    assert.deepStrictEqual(buildD1Filters({ parent_id: 'parent-1' }), {
      parent_id: 'parent-1'
    });
  });

  it('returns empty filters when only personal scope is given without owner_id', () => {
    assert.deepStrictEqual(buildD1Filters({ scope: 'personal' }), {});
  });
});
