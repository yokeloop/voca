const FENCED_CODE_BLOCK = /```[\s\S]*?```/g;
const URL = /\bhttps?:\/\/\S+|\bwww\.\S+/gi;
const URL_TRAILING_PUNCT = /[,.!?;:)\]}"']+$/;
const INLINE_LINK = /!?\[([^\]]+)\]\(([^)]+)\)/g;
const EMOJI = /\p{Extended_Pictographic}/gu;
const EMOJI_MODIFIER = /[\uFE0E\uFE0F\u200D]|[\u{1F3FB}-\u{1F3FF}]/gu;
const MARKDOWN_PUNCT = /[*_`#>]/g;
const LIST_BULLET_LINE = /^[ \t]*(?:[-*+]|\d+\.)[ \t]+/gm;
const DECORATIVE_SYMBOL = /[→•—–…«»""'']/g;
const WHITESPACE_RUN = /\s+/g;

export function sanitizeForTts(text: string): string {
  return text
    .replace(FENCED_CODE_BLOCK, ' Code block ')
    .replace(INLINE_LINK, '$1')
    .replace(URL, (match) => {
      const tail = match.match(URL_TRAILING_PUNCT)?.[0] ?? '';
      return ' Link' + tail + ' ';
    })
    .replace(EMOJI, '')
    .replace(EMOJI_MODIFIER, '')
    .replace(LIST_BULLET_LINE, '')
    .replace(MARKDOWN_PUNCT, '')
    .replace(DECORATIVE_SYMBOL, ' ')
    .replace(WHITESPACE_RUN, ' ')
    .trim();
}
