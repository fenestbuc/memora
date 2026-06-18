import { describe, it } from 'node:test';
import assert from 'node:assert';
import { buildD1Filters } from './search.js';

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
