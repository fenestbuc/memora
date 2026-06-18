import { describe, it } from 'node:test';
import assert from 'node:assert';
import { handleThink } from './think.js';

const FAKE_VECTOR = new Array(768).fill(0.1);

function fakeEnv(overrides = {}) {
  return {
    AI: {
      run: async (model, payload) => {
        if (model.includes('bge-m3') || payload.text) {
          const texts = Array.isArray(payload.text) ? payload.text : [payload.text];
          return { data: texts.map(() => FAKE_VECTOR) };
        }
        if (payload.query && payload.contexts) {
          // Reranker path in search.js
          return { response: payload.contexts.map((_, i) => ({ score: 1.0 - i * 0.1 })) };
        }
        // LLM path used by /think
        const query = payload.messages?.[1]?.content || '';
        if (query.includes('Retrieved facts:')) {
          return {
            response:
              'Project Falcon builds AI underwriting tools. [projects/2026-01-01]\n\n## Gaps\n- The exact launch date is unclear.\n- Whether PNB partnership is confirmed is uncertain.',
          };
        }
        return { response: 'I could not find any relevant facts.' };
      },
    },
    VECTORIZE: {
      query: async (_vector, opts) => {
        const matches = overrides.matches || [];
        return { matches, searchOpts: opts };
      },
    },
    DB: null,
    CACHE: null,
    DEFAULT_LLM: '@cf/meta/llama-4-scout-17b-16e-instruct',
    EMBEDDING_MODEL: '@cf/baai/bge-m3',
    RERANKER_MODEL: '@cf/baai/bge-reranker-base',
    ...overrides,
  };
}

function makeFact(id, text, metadata = {}) {
  return {
    id,
    text,
    score: 0.95,
    rerank_score: 0.95,
    vector_score: 0.9,
    metadata: {
      category: 'projects',
      created_at: '2026-01-01T00:00:00Z',
      owner_id: 'e2e-test-owner',
      ...metadata,
    },
  };
}

describe('handleThink', () => {
  it('returns a structured answer, sources, and extracted gaps', async () => {
    const matches = [
      makeFact('fact-001', 'Project Falcon builds AI underwriting tools.'),
      makeFact('fact-002', 'Pilot lender is SBI.'),
    ];
    const resp = await handleThink(
      { query: 'What is Project Falcon?', top_k: 5, owner_id: 'e2e-test-owner' },
      fakeEnv({ matches }),
    );
    assert.strictEqual(resp.status, 200);
    const data = await resp.json();
    assert.ok(data.answer, 'answer should be present');
    assert.ok(data.answer.includes('Project Falcon'), 'answer should reference the fact');
    assert.strictEqual(data.sources.length, 2);
    assert.deepStrictEqual(
      data.sources.map((s) => s.id),
      ['fact-001', 'fact-002'],
    );
    assert.deepStrictEqual(data.gaps, [
      'The exact launch date is unclear.',
      'Whether PNB partnership is confirmed is uncertain.',
    ]);
  });

  it('returns honest gaps when no facts match', async () => {
    const resp = await handleThink(
      { query: 'nonsense query abc123', top_k: 5, owner_id: 'e2e-test-owner' },
      fakeEnv(),
    );
    assert.strictEqual(resp.status, 200);
    const data = await resp.json();
    assert.ok(data.answer.includes('could not find'));
    assert.deepStrictEqual(data.sources, []);
    assert.strictEqual(data.gaps.length, 1);
    assert.ok(data.gaps[0].toLowerCase().includes('no facts matched'));
  });

  it('falls back to empty gaps when the model omits the section', async () => {
    const env = fakeEnv({
      matches: [makeFact('fact-003', 'Some fact.')],
      AI: {
        run: async (model, payload) => {
          if (model.includes('bge-m3') || payload.text) {
            const texts = Array.isArray(payload.text) ? payload.text : [payload.text];
            return { data: texts.map(() => FAKE_VECTOR) };
          }
          if (payload.query && payload.contexts) {
            return { response: payload.contexts.map(() => ({ score: 1.0 })) };
          }
          return { response: 'A plain answer without a gaps section.' };
        },
      },
    });
    const resp = await handleThink(
      { query: 'Plain question', top_k: 5, owner_id: 'e2e-test-owner' },
      env,
    );
    const data = await resp.json();
    assert.deepStrictEqual(data.gaps, []);
  });

  it('validates that query is a string', async () => {
    const resp = await handleThink({ query: 123 }, fakeEnv());
    assert.strictEqual(resp.status, 400);
    const data = await resp.json();
    assert.ok(data.error);
  });
});
