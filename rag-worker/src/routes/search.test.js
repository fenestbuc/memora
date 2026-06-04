import { describe, it } from 'node:test';
import assert from 'node:assert';

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
