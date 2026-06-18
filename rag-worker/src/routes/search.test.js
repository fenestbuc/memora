import { describe, it } from 'node:test';
import assert from 'node:assert';
import { buildVectorizeFilter } from './search.js';

describe('search top_k normalization', () => {
  function normalizeTopK(top_k, shouldRerank) {
    const numericTopK = Number.isFinite(top_k) ? Math.max(1, Math.floor(top_k)) : 10;
    return shouldRerank ? Math.min(numericTopK * 3, 50) : numericTopK;
  }

  it('defaults NaN to 10', () => {
    assert.strictEqual(normalizeTopK(NaN, false), 10);
    assert.strictEqual(normalizeTopK('abc', false), 10);
  });
  it('floors decimals', () => {
    assert.strictEqual(normalizeTopK(5.9, false), 5);
  });
  it('caps at minimum 1', () => {
    assert.strictEqual(normalizeTopK(0, false), 1);
    assert.strictEqual(normalizeTopK(-5, false), 1);
  });
  it('caps rerank at 50', () => {
    assert.strictEqual(normalizeTopK(30, true), 50); // 30*3=90, min(90,50)=50
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
