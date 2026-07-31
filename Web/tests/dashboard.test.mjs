import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
test("dashboard source keeps credentials out of persistent browser storage", async () => {
  const source = await readFile(new URL("../src/main.tsx", import.meta.url), "utf8");
  assert.equal(source.includes("localStorage"), false);
  assert.equal(source.includes("sessionStorage"), false);
  assert.match(source, /Workspace/);
  assert.match(source, /Artifacts/);
  assert.match(source, /platformAdmin \? \[\.\.\.baseNav, "Admin"\] : baseNav/);
  assert.match(source, /"\/admin\/me"/);
});
test("API client has timeout and bearer injection", async () => {
  const source = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");
  assert.match(source, /AbortController/);
  assert.match(source, /Bearer/);
});
