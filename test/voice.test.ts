import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  deriveVoicePaths,
  fetchCatalog,
  listAvailable,
  __resetCatalogCacheForTests,
} from '../src/voice.js';

const stubCatalog = {
  'ru_RU-irina-medium': { language: { code: 'ru_RU' }, quality: 'medium' },
  'en_US-lessac-high': { language: { code: 'en_US' }, quality: 'high' },
  'en_US-lessac-low': { language: { code: 'en_US' }, quality: 'low' },
  'de_DE-thorsten-medium': { language: { code: 'de_DE' }, quality: 'medium' },
};

describe('deriveVoicePaths', () => {
  it('derives HF URL for ru_RU-irina-medium', () => {
    const p = deriveVoicePaths('ru_RU-irina-medium');
    expect(p.onnxUrl).toBe(
      'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx',
    );
    expect(p.jsonUrl).toBe(`${p.onnxUrl}.json`);
    expect(p.onnxPath.endsWith('ru_RU-irina-medium.onnx')).toBe(true);
    expect(p.jsonPath.endsWith('ru_RU-irina-medium.onnx.json')).toBe(true);
  });

  it('derives HF URL for en_US-lessac-high', () => {
    const p = deriveVoicePaths('en_US-lessac-high');
    expect(p.onnxUrl).toContain('/en/en_US/lessac/high/');
    expect(p.onnxUrl.endsWith('/en_US-lessac-high.onnx')).toBe(true);
  });

  it('rejects malformed voice names', () => {
    expect(() => deriveVoicePaths('bogus')).toThrow(/invalid voice name/);
    expect(() => deriveVoicePaths('two-parts')).toThrow(/invalid voice name/);
  });
});

describe('fetchCatalog', () => {
  beforeEach(() => {
    __resetCatalogCacheForTests();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    __resetCatalogCacheForTests();
  });

  it('fetches and caches catalog', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => stubCatalog,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const first = await fetchCatalog();
    const second = await fetchCatalog();
    expect(first).toBe(second);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('throws on non-OK response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })),
    );
    await expect(fetchCatalog()).rejects.toThrow(/HTTP 503/);
  });
});

describe('listAvailable', () => {
  beforeEach(() => {
    __resetCatalogCacheForTests();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => stubCatalog })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    __resetCatalogCacheForTests();
  });

  it('filters by language prefix', async () => {
    const result = await listAvailable({ languageFilter: 'ru' });
    expect(result.map((v) => v.name)).toEqual(['ru_RU-irina-medium']);
  });

  it('filters by language prefix for en', async () => {
    const result = await listAvailable({ languageFilter: 'en' });
    expect(result.map((v) => v.name).sort()).toEqual([
      'en_US-lessac-high',
      'en_US-lessac-low',
    ]);
  });

  it('returns all with all=true', async () => {
    const result = await listAvailable({ all: true });
    expect(result).toHaveLength(4);
  });

  it('returns all when no filter set', async () => {
    const result = await listAvailable({});
    expect(result).toHaveLength(4);
  });
});
