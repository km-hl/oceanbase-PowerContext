/**
 * Split text without losing the end of a document.
 * The overlapping window preserves evidence across chunk boundaries.
 */
export function chunkText(text, limit = 380, overlap = 48) {
  if (typeof text !== "string") throw new TypeError("Text is required");
  if (!Number.isInteger(limit) || limit < 32 || !Number.isInteger(overlap) || overlap < 0 || overlap >= limit) {
    throw new RangeError("Invalid chunk settings");
  }
  const source = text.replace(/\r\n?/g, "\n").trim();
  if (!source) return [];
  const chunks = [];
  let offset = 0;
  while (offset < source.length) {
    const upper = Math.min(source.length, offset + limit);
    let end = upper;
    if (upper < source.length) {
      const slice = source.slice(offset, upper);
      const breakAt = Math.max(slice.lastIndexOf("\n\n"), slice.lastIndexOf("。"), slice.lastIndexOf(". "));
      if (breakAt >= Math.floor(limit * 0.55)) end = offset + breakAt + 1;
    }
    const value = source.slice(offset, end).trim();
    if (value) chunks.push(value);
    if (end === source.length) break;
    offset = Math.max(offset + 1, end - overlap);
  }
  return chunks;
}
