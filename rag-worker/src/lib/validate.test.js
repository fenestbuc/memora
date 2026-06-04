import { describe, it } from 'node:test';
import assert from 'node:assert';
import { sanitizeScope, sanitizeOwnerId, truncateId, validateString, validateArray, ValidationError } from './validate.js';

describe('sanitizeScope', () => {
  it('defaults missing to personal', () => {
    assert.strictEqual(sanitizeScope(), 'personal');
    assert.strictEqual(sanitizeScope(null), 'personal');
    assert.strictEqual(sanitizeScope(''), 'personal');
  });
  it('allows valid values', () => {
    assert.strictEqual(sanitizeScope('personal'), 'personal');
    assert.strictEqual(sanitizeScope('company'), 'company');
    assert.strictEqual(sanitizeScope('global'), 'global');
  });
  it('rejects invalid values', () => {
    assert.strictEqual(sanitizeScope('admin'), 'personal');
    assert.strictEqual(sanitizeScope("'; DROP TABLE--"), 'personal');
  });
});

describe('sanitizeOwnerId', () => {
  it('defaults missing to anonymous', () => {
    assert.strictEqual(sanitizeOwnerId(), 'anonymous');
    assert.strictEqual(sanitizeOwnerId(null), 'anonymous');
  });
  it('strips special characters', () => {
    assert.strictEqual(sanitizeOwnerId("alice@example.com"), 'aliceexamplecom');
    assert.strictEqual(sanitizeOwnerId("'; DROP TABLE--"), 'DROPTABLE--');
  });
  it('truncates to 64 chars', () => {
    const long = 'a'.repeat(100);
    assert.strictEqual(sanitizeOwnerId(long).length, 64);
  });
});

describe('truncateId', () => {
  it('leaves short ids alone', () => {
    assert.strictEqual(truncateId('short'), 'short');
  });
  it('truncates long ids deterministically', () => {
    const id = 'a'.repeat(100);
    const t1 = truncateId(id);
    const t2 = truncateId(id);
    assert.strictEqual(t1, t2);
    assert.ok(t1.length <= 64);
  });
});

describe('validateString', () => {
  it('accepts valid strings', () => {
    assert.strictEqual(validateString('hello', 'field', 10), 'hello');
  });
  it('rejects non-strings', () => {
    assert.throws(() => validateString(123, 'field'), ValidationError);
  });
  it('rejects too long', () => {
    assert.throws(() => validateString('a'.repeat(11), 'field', 10), ValidationError);
  });
});

describe('validateArray', () => {
  it('accepts arrays', () => {
    assert.deepStrictEqual(validateArray([1,2], 'field'), [1,2]);
  });
  it('rejects non-arrays', () => {
    assert.throws(() => validateArray('not-array', 'field'), ValidationError);
  });
});
