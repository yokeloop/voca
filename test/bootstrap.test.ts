import { describe, it, expect } from 'vitest';
import { languageOf, computeWindowSize } from '../src/bootstrap.js';

describe('languageOf', () => {
  it('extracts language prefix from voice name', () => {
    expect(languageOf('en_US-lessac-medium')).toBe('en');
    expect(languageOf('ru_RU-irina-medium')).toBe('ru');
    expect(languageOf('de_DE-thorsten-medium')).toBe('de');
  });

  it('lowercases the prefix', () => {
    expect(languageOf('EN_US-foo')).toBe('en');
  });

  it('falls back to the full string when no underscore', () => {
    expect(languageOf('weirdname')).toBe('weirdname');
  });
});

describe('computeWindowSize', () => {
  it('caps window by options count', () => {
    expect(computeWindowSize(3, 30)).toBe(3);
  });

  it('caps window by usable rows (rows - 4)', () => {
    expect(computeWindowSize(100, 20)).toBe(16);
  });

  it('never drops below 1 even on tiny terminals', () => {
    expect(computeWindowSize(10, 3)).toBe(1);
    expect(computeWindowSize(10, 4)).toBe(1);
  });

  it('handles undefined rows via default', () => {
    expect(computeWindowSize(100, undefined)).toBe(16);
  });
});
