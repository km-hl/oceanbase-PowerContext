import assert from "node:assert/strict";
import test from "node:test";
import { chunkText } from "../src/chunk.js";

test("empty input is skipped", () => {
  assert.deepEqual(chunkText("  \r\n "), []);
});
test("short Chinese and English documents stay intact", () => {
  assert.deepEqual(chunkText("向量搜索与全文搜索。"), ["向量搜索与全文搜索。"]);
});
test("overlap keeps adjacent context and final passage", () => {
  const input = "A".repeat(100) + "B".repeat(100) + "C".repeat(100);
  const pieces = chunkText(input, 120, 20);
  assert.ok(pieces.length >= 3);
  assert.ok(pieces.every((p) => p.length <= 120));
  assert.equal(pieces[0].slice(-20), pieces[1].slice(0, 20));
  assert.ok(pieces.at(-1).endsWith("C".repeat(20)));
});
test("invalid overlap is rejected", () => {
  assert.throws(() => chunkText("abc", 32, 32), RangeError);
});
