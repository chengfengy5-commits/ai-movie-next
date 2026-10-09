import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  ALLOWED_ENTRIES,
  assertManifestShape,
  MANIFEST_TARGET,
  SOURCE_COMMIT,
  SOURCE_REPOSITORY,
} from "./source-baseline-contract.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifestPath = path.join(projectRoot, MANIFEST_TARGET);
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function frozenSource(entry) {
  return execFileSync(
    "git",
    ["-C", SOURCE_REPOSITORY, "show", `${SOURCE_COMMIT}:${entry.source_path}`],
    { encoding: "buffer" },
  );
}

function frozenBlob(entry) {
  return execFileSync(
    "git",
    ["-C", SOURCE_REPOSITORY, "rev-parse", `${SOURCE_COMMIT}:${entry.source_path}`],
    { encoding: "utf8" },
  ).trim();
}

function targetPath(relativePath) {
  const absolutePath = path.resolve(projectRoot, relativePath);
  const referenceRoot = path.join(projectRoot, "legacy-reference");
  if (!absolutePath.startsWith(`${referenceRoot}${path.sep}`)) {
    throw new Error(`Manifest target is outside the reference directory: ${relativePath}`);
  }
  return absolutePath;
}

function listFiles(directory, parent = "") {
  const files = [];
  for (const entry of readdirSync(path.join(directory, parent), { withFileTypes: true })) {
    const relative = path.join(parent, entry.name);
    if (entry.isSymbolicLink()) {
      throw new Error(`Source reference contains an unexpected symlink: ${relative}`);
    }
    if (entry.isDirectory()) {
      files.push(...listFiles(directory, relative));
    } else if (entry.isFile()) {
      files.push(relative.split(path.sep).join("/"));
    }
  }
  return files;
}

const expectedFiles = [
  MANIFEST_TARGET.replace("legacy-reference/", ""),
  ...ALLOWED_ENTRIES.map((entry) => entry.target_path.replace("legacy-reference/", "")),
].sort();
const actualFiles = listFiles(path.join(projectRoot, "legacy-reference")).sort();
if (JSON.stringify(actualFiles) !== JSON.stringify(expectedFiles)) {
  throw new Error("Reference directory contains missing or unapproved files.");
}

assertManifestShape(manifest);
if (manifest.source_review?.sensitive_pattern_scan?.includes("No matches") !== true) {
  throw new Error("Sensitive-pattern scan result is missing from the manifest.");
}

const findings = [];
const secretPatterns = [
  ["private-key block", /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/],
  ["quoted credential literal", /\b(?:api[_-]?key|access[_-]?token|secret(?:[_-]?key)?|password|passwd)\b\s*[:=]\s*['"][A-Za-z0-9+/=_\-.]{16,}['"]/i],
  ["credential URI", /\b(?:https?|postgres(?:ql)?|redis):\/\/[^/\s:@]+:[^/\s@]+@/i],
  ["provider token", /\b(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{16,})/i],
];

for (const entry of manifest.entries) {
  const source = frozenSource(entry);
  const target = readFileSync(targetPath(entry.target_path));
  const sourceHash = sha256(source);
  const targetHash = sha256(target);

  if (frozenBlob(entry) !== entry.git_blob) {
    throw new Error(`Frozen Git blob mismatch: ${entry.source_path}`);
  }
  if (source.length !== entry.source_bytes || sourceHash !== entry.source_sha256) {
    throw new Error(`Frozen source bytes or SHA-256 mismatch: ${entry.source_path}`);
  }
  if (target.length !== entry.target_bytes || targetHash !== entry.target_sha256) {
    throw new Error(`Reference target bytes or SHA-256 mismatch: ${entry.target_path}`);
  }

  const sourceText = source.toString("utf8");
  for (const [category, pattern] of secretPatterns) {
    if (pattern.test(sourceText)) {
      findings.push({ path: entry.source_path, category });
    }
  }

  if (entry.copied_full_file) {
    if (sourceHash !== targetHash || !source.equals(target)) {
      throw new Error(`Full-file copy differs from its frozen source: ${entry.source_path}`);
    }
  } else {
    const { start, end } = entry.source_line_range;
    const sourceLines = sourceText.match(/[^\n]*\n|[^\n]+$/g) ?? [];
    const excerpt = sourceLines.slice(start - 1, end).join("");
    if (
      Buffer.byteLength(excerpt, "utf8") !== entry.source_excerpt_bytes ||
      sha256(excerpt) !== entry.source_excerpt_sha256 ||
      excerpt !== target.toString("utf8")
    ) {
      throw new Error(`Bounded source excerpt mismatch: ${entry.source_path} lines ${start}-${end}`);
    }
  }
}

if (findings.length > 0) {
  const summary = findings.map(({ path: sourcePath, category }) => `${sourcePath}: ${category}`).join("\n");
  throw new Error(`Sensitive-pattern scan needs review; values were suppressed:\n${summary}`);
}

console.log(
  `Verified ${manifest.entries.length} approved source entries from ${SOURCE_COMMIT}; ${findings.length} sensitive-pattern matches.`,
);
