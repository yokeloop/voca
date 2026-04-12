import { describe, it, expect } from 'vitest';
import { sanitizeForTts } from '../src/sanitizer.js';

describe('sanitizeForTts', () => {
  it('returns empty string for emoji-only input', () => {
    expect(sanitizeForTts('👋🎉🔥')).toBe('');
  });

  it('strips bold, italic and inline-code markdown', () => {
    expect(sanitizeForTts('**bold** _italic_ `code`')).toBe('bold italic code');
  });

  it('replaces fenced code blocks with "Code block"', () => {
    const input = 'before\n```js\nconst x = 1;\nconsole.log(x);\n```\nafter';
    expect(sanitizeForTts(input)).toBe('before Code block after');
  });

  it('replaces URLs with "Link"', () => {
    expect(sanitizeForTts('visit https://example.com now')).toBe('visit Link now');
    expect(sanitizeForTts('see http://a.b/c?x=1')).toBe('see Link');
    expect(sanitizeForTts('see www.example.com please')).toBe('see Link please');
  });

  it('replaces inline markdown links with their text', () => {
    expect(sanitizeForTts('see [the docs](https://example.com) now')).toBe('see the docs now');
  });

  it('collapses decorative symbols to whitespace', () => {
    expect(sanitizeForTts('left → right • dash — wait…')).toBe('left right dash wait');
  });

  it('preserves Cyrillic text while stripping emoji', () => {
    expect(sanitizeForTts('Привет 👋 мир')).toBe('Привет мир');
  });

  it('collapses runs of whitespace and trims', () => {
    expect(sanitizeForTts('a  b\t\tc\n\nd')).toBe('a b c d');
    expect(sanitizeForTts('   hello   ')).toBe('hello');
  });

  it('strips heading, blockquote and list markers', () => {
    expect(sanitizeForTts('# Title\n> quoted\n- item one\n- item two')).toBe(
      'Title quoted item one item two',
    );
    expect(sanitizeForTts('1. first\n2. second')).toBe('first second');
  });

  it('preserves sentence punctuation after URLs', () => {
    expect(sanitizeForTts('visit https://example.com, please')).toBe('visit Link, please');
    expect(sanitizeForTts('see https://example.com.')).toBe('see Link.');
    expect(sanitizeForTts('really? https://ya.ru!')).toBe('really? Link!');
  });

  it('preserves URL query strings while trimming trailing punctuation', () => {
    expect(sanitizeForTts('go http://a.b/c?x=1, now')).toBe('go Link, now');
  });

  it('strips image markdown including the leading exclamation', () => {
    expect(sanitizeForTts('see ![alt text](https://example.com/img.png) here')).toBe(
      'see alt text here',
    );
  });

  it('returns empty string for empty input', () => {
    expect(sanitizeForTts('')).toBe('');
  });

  it('returns empty string for whitespace-only input', () => {
    expect(sanitizeForTts('   \n\t  ')).toBe('');
  });

  it('handles combined emoji, markdown, url and code block', () => {
    const input = 'Привет 👋! **Жми** → https://ya.ru\n```js\nx=1\n```';
    const result = sanitizeForTts(input);
    expect(result).toContain('Привет');
    expect(result).toContain('Жми');
    expect(result).toContain('Link');
    expect(result).toContain('Code block');
    expect(result).not.toMatch(/\p{Extended_Pictographic}/u);
    expect(result).not.toContain('*');
    expect(result).not.toContain('→');
    expect(result).not.toContain('`');
  });
});
