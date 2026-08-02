import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
test("dashboard source keeps credentials out of persistent browser storage", async () => {
  const source = await readFile(new URL("../src/main.tsx", import.meta.url), "utf8");
  assert.equal(source.includes("localStorage"), false);
  assert.equal(source.includes("sessionStorage"), false);
  assert.match(source, /Workspace/);
  assert.match(source, /ONE REQUEST/);
  assert.match(source, /product-jobs/);
  assert.match(source, /PLANNING.*MUSIC.*IMAGE.*BLOG.*VIDEO.*YOUTUBE.*NAVER/);
  assert.match(source, /USER_CONFIRM_REQUIRED/);
  assert.match(source, /\/connections/);
  assert.match(source, /Completed audio/);
  assert.match(source, /\/audio/);
  assert.match(source, /\/resume/);
  assert.match(source, /\/retry/);
  assert.match(source, /\/artifacts\/.*\/content/);
  assert.equal(source.includes("internal_ref"), false);
});
test("API client has timeout and bearer injection", async () => {
  const source = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");
  assert.match(source, /AbortController/);
  assert.match(source, /Bearer/);
});
