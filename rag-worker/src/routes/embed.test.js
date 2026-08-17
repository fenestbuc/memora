import { describe, it } from 'node:test';
import assert from 'node:assert';
import { handleEmbed } from './embed.js';

function makeEnv(outputs) {
  return {
    AI: {
      run: async (_model, input) => ({ data: input.text.map(() => outputs.shift()) })
    },
    EMBEDDING_MODEL: '@cf/baai/bge-m3',
    CACHE: null,
  };
}

describe('handleEmbed', () => {
  it('accepts a single string', async () => {
    const env = makeEnv([[0.1, 0.2, 0.3]]);
    const resp = await handleEmbed({ text: 'hello' }, env);
    const body = await resp.json();
    assert.strictEqual(body.count, 1);
    assert.strictEqual(body.embeddings.length, 1);
  });

  it('accepts an array of strings', async () => {
    const env = makeEnv([[0.1], [0.2]]);
    const resp = await handleEmbed({ text: ['hello', 'world'] }, env);
    const body = await resp.json();
    assert.strictEqual(body.count, 2);
    assert.strictEqual(body.embeddings.length, 2);
  });

  it('also accepts body.text as a single string', async () => {
    const env = makeEnv([[0.9]]);
    const resp = await handleEmbed({ text: 'single' }, env);
    const body = await resp.json();
    assert.strictEqual(body.count, 1);
  });
});
