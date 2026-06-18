import { describe, it } from 'node:test';
import assert from 'node:assert';
import { buildD1Filters, buildVectorizeFilter } from './search.js';

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

describe('buildVectorizeFilter', () => {
  it('returns undefined for empty conditions', () => {
    assert.strictEqual(buildVectorizeFilter({}), undefined);
  });

  it('wraps simple equality in $eq', () => {
    assert.deepStrictEqual(buildVectorizeFilter({ archived: 0 }), { archived: { $eq: 0 } });
  });

  it('combines multiple conditions with $and', () => {
    assert.deepStrictEqual(
      buildVectorizeFilter({ archived: 0, scope: 'company' }),
      {
        $and: [
          { archived: { $eq: 0 } },
          { scope: { $eq: 'company' } }
        ]
      }
    );
  });

  it('preserves user-supplied operator objects', () => {
    assert.deepStrictEqual(
      buildVectorizeFilter({ score: { $gte: 0.5 } }),
      { score: { $gte: 0.5 } }
    );
  });

  it('mixes operator objects and simple values', () => {
    assert.deepStrictEqual(
      buildVectorizeFilter({ archived: 0, score: { $gte: 0.5 } }),
      {
        $and: [
          { archived: { $eq: 0 } },
          { score: { $gte: 0.5 } }
        ]
      }
    );
  });

  it('drops undefined and null values', () => {
    assert.deepStrictEqual(
      buildVectorizeFilter({ archived: 0, parent_id: null, owner_id: undefined }),
      { archived: { $eq: 0 } }
    );
  });
});
