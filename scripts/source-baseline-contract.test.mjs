import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { assertManifestShape } from "./source-baseline-contract.mjs";

const manifestPath = fileURLToPath(new URL("../legacy-reference/source-manifest.json", import.meta.url));
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

test("accepts the approved five-entry source manifest", () => {
  assert.equal(assertManifestShape(manifest), true);
});

test("rejects an empty manifest instead of reporting a zero-entry pass", () => {
  assert.throws(
    () => assertManifestShape({ ...manifest, entries: [] }),
    /exactly 5 approved entries/,
  );
});

test("rejects a broadened series excerpt range", () => {
  const altered = structuredClone(manifest);
  const route = altered.entries.find((entry) => entry.source_path === "backend/app/routes/series.py");
  route.source_line_range.end = 175;
  assert.throws(() => assertManifestShape(altered), /source_line_range mismatch/);
});
