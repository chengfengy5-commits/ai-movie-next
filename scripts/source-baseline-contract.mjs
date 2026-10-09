export const SOURCE_COMMIT = "23403806898550a7668a6ee7c0c457315655c39b";
export const SOURCE_REPOSITORY = "/Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com";
export const MANIFEST_TARGET = "legacy-reference/source-manifest.json";

export const ALLOWED_ENTRIES = [
  {
    source_path: "backend/app/schemas/auth.py",
    git_blob: "c23e10dfa3d4bfd60deb2b17bf014c6addb3f5e2",
    source_bytes: 2419,
    source_sha256: "faa48c5f3754d8a491d4310dc28133fa8e586f164af9356f94d1a419a87557f7",
    target_path: "legacy-reference/backend/app/schemas/auth.py",
    target_bytes: 2419,
    target_sha256: "faa48c5f3754d8a491d4310dc28133fa8e586f164af9356f94d1a419a87557f7",
    copied_full_file: true,
  },
  {
    source_path: "backend/app/schemas/series.py",
    git_blob: "9527d41091cecba9065c04a45f0df0c8f2c35309",
    source_bytes: 13748,
    source_sha256: "78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9",
    target_path: "legacy-reference/backend/app/schemas/series.py",
    target_bytes: 13748,
    target_sha256: "78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9",
    copied_full_file: true,
  },
  {
    source_path: "backend/app/routes/auth.py",
    git_blob: "f58aad8ec20eaeae6df6205624821fa4d3d7990b",
    source_bytes: 18221,
    source_sha256: "6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78",
    target_path: "legacy-reference/backend/app/routes/auth.py",
    target_bytes: 18221,
    target_sha256: "6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78",
    copied_full_file: true,
  },
  {
    source_path: "js/auth.js",
    git_blob: "088edb72c579f1f3cf0fe298070e2d79c2e46931",
    source_bytes: 5006,
    source_sha256: "1fcc256e1d6d985ca8d278f639ffe9bf9a243d4c3972d37f6f5f4a82f6b2771a",
    target_path: "legacy-reference/js/auth.js",
    target_bytes: 5006,
    target_sha256: "1fcc256e1d6d985ca8d278f639ffe9bf9a243d4c3972d37f6f5f4a82f6b2771a",
    copied_full_file: true,
  },
  {
    source_path: "backend/app/routes/series.py",
    git_blob: "6cd7f088ed3f6eb3250844cc0334597c7ffcc6ee",
    source_bytes: 115619,
    source_sha256: "db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf",
    source_line_range: { start: 103, end: 174 },
    source_excerpt_bytes: 3226,
    source_excerpt_sha256: "b1eb133ed6005be1f925f345bd67dc410d9871cfc1bbcace2bf3265ad846aa6a",
    target_path: "legacy-reference/backend/app/routes/series.py.lines-0103-0174.txt",
    target_bytes: 3226,
    target_sha256: "b1eb133ed6005be1f925f345bd67dc410d9871cfc1bbcace2bf3265ad846aa6a",
    copied_full_file: false,
  },
];

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function assertManifestShape(manifest) {
  if (!isRecord(manifest)) {
    throw new Error("Source manifest must be a JSON object.");
  }
  if (manifest.source_commit !== SOURCE_COMMIT) {
    throw new Error("Source commit does not match the approved frozen revision.");
  }
  if (manifest.source_repository !== SOURCE_REPOSITORY) {
    throw new Error("Source repository does not match the approved legacy checkout.");
  }
  if (!Array.isArray(manifest.entries) || manifest.entries.length !== ALLOWED_ENTRIES.length) {
    throw new Error(`Manifest must contain exactly ${ALLOWED_ENTRIES.length} approved entries.`);
  }
  if (manifest.source_review?.legacy_worktree_status_unchanged !== true) {
    throw new Error("Legacy checkout status was not verified unchanged during capture.");
  }

  const sourcePaths = manifest.entries.map((entry) => entry?.source_path);
  const targetPaths = manifest.entries.map((entry) => entry?.target_path);
  if (new Set(sourcePaths).size !== ALLOWED_ENTRIES.length) {
    throw new Error("Manifest source paths must be unique.");
  }
  if (new Set(targetPaths).size !== ALLOWED_ENTRIES.length) {
    throw new Error("Manifest target paths must be unique.");
  }

  const actualBySource = new Map(manifest.entries.map((entry) => [entry?.source_path, entry]));
  for (const approved of ALLOWED_ENTRIES) {
    const actual = actualBySource.get(approved.source_path);
    if (!isRecord(actual)) {
      throw new Error(`Approved source is missing from manifest: ${approved.source_path}`);
    }
    for (const [key, expected] of Object.entries(approved)) {
      if (JSON.stringify(actual[key]) !== JSON.stringify(expected)) {
        throw new Error(`Manifest ${key} mismatch for ${approved.source_path}.`);
      }
    }
    if (actual.source_commit !== SOURCE_COMMIT) {
      throw new Error(`Entry source commit mismatch for ${approved.source_path}.`);
    }
  }

  return true;
}
