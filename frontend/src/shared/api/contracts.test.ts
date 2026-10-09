// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  InvalidResponseError,
  parseChapterList,
  parseCharacterList,
  parseLoginResponse,
  parsePropList,
  parseSeries,
  parseSceneList,
  parseStoryboardAssetList,
  type SeriesAssetFields,
} from "./contracts";
import { demoSeries } from "../../features/series/demoSeries";

const authSchemaPath = fileURLToPath(new URL("../../../../legacy-reference/backend/app/schemas/auth.py", import.meta.url));
const seriesSchemaPath = fileURLToPath(new URL("../../../../legacy-reference/backend/app/schemas/series.py", import.meta.url));
const contractsPath = fileURLToPath(new URL("./contracts.ts", import.meta.url));

function pythonModelFields(source: string, modelName: string): string[] {
  const declaration = source.match(new RegExp(`^class ${modelName}(?:\\(([^)]+)\\))?:`, "m"));
  if (declaration === null || declaration.index === undefined) {
    throw new Error(`Pydantic model not found: ${modelName}`);
  }
  const start = declaration.index;
  const nextClass = source.indexOf("\nclass ", start + 1);
  const model = source.slice(start, nextClass < 0 ? undefined : nextClass);
  const ownFields = [...model.matchAll(/^    ([a-z_]+):/gm)].map((match) => match[1]!);
  const parentName = declaration[1]?.trim();
  const inheritedFields = parentName && parentName !== "BaseModel"
    ? pythonModelFields(source, parentName)
    : [];
  return [...new Set([...inheritedFields, ...ownFields])].sort();
}

function typeScriptInterfaceFields(source: string, interfaceName: string): string[] {
  const match = source.match(new RegExp(
    `export interface ${interfaceName}(?: extends ([^{]+))? \\{([\\s\\S]*?)\\n\\}`,
  ));
  if (match === null) {
    throw new Error(`TypeScript interface not found: ${interfaceName}`);
  }
  const parentNames = match[1]?.split(",").map((name) => name.trim()).filter(Boolean) ?? [];
  const ownFields = [...match[2]!.matchAll(/^  ([a-z_]+):/gm)].map((field) => field[1]!);
  return [...new Set([
    ...parentNames.flatMap((name) => typeScriptInterfaceFields(source, name)),
    ...ownFields,
  ])].sort();
}

describe("static source schema compatibility", () => {
  const authSchema = readFileSync(authSchemaPath, "utf8");
  const seriesSchema = readFileSync(seriesSchemaPath, "utf8");
  const contracts = readFileSync(contractsPath, "utf8");

  it("matches user and login input fields from the frozen Pydantic schemas", () => {
    expect(typeScriptInterfaceFields(contracts, "User")).toEqual(pythonModelFields(authSchema, "UserResponse"));
    expect(typeScriptInterfaceFields(contracts, "Credentials")).toEqual(pythonModelFields(authSchema, "UserLogin"));
  });

  it("matches all series response fields and accepts the compatible login envelope", () => {
    expect(typeScriptInterfaceFields(contracts, "Series")).toEqual(pythonModelFields(seriesSchema, "SeriesResponse"));
    expect(parseLoginResponse({
      access_token: "local-static-test-token",
      token_type: "bearer",
      user: {
        id: "user-1",
        username: "作者",
        email: "author@example.invalid",
        created_at: "2026-10-01T00:00:00",
      },
    })).toMatchObject({ token: "local-static-test-token", user: { id: "user-1" } });
  });

  it("matches the frozen chapter and storyboard asset response fields", () => {
    expect(typeScriptInterfaceFields(contracts, "Chapter")).toEqual(pythonModelFields(seriesSchema, "ChapterResponse"));
    expect(typeScriptInterfaceFields(contracts, "StoryboardAsset")).toEqual(
      pythonModelFields(seriesSchema, "StoryboardAssetResponse"),
    );
  });

  it("matches all material response fields including inherited naming fields", () => {
    for (const model of ["Character", "Scene", "Prop"]) {
      expect(typeScriptInterfaceFields(contracts, model)).toEqual(
        pythonModelFields(seriesSchema, model + "Response"),
      );
    }
  });

  it("defaults the optional can_enter field to true when older responses omit it", () => {
    const { can_enter: _canEnter, ...seriesWithoutPermission } = demoSeries[0]!;
    expect(parseSeries(seriesWithoutPermission).can_enter).toBe(true);
  });
});

function validChapter(overrides: Record<string, unknown> = {}) {
  return {
    id: "chapter-1",
    series_id: "series-1",
    title: "第一章",
    content: [{ text: "一段镜头" }],
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
    ...overrides,
  };
}

function validAsset(overrides: Record<string, unknown> = {}) {
  return {
    id: "asset-1",
    series_id: "series-1",
    chapter_id: "chapter-1",
    frame_index: 4,
    name: "原图",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function validCharacter(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "asset-1",
    series_id: "series-1",
    name: "林岚",
    gender: null,
    age: null,
    role: null,
    appearance: null,
    description: null,
    image_url: null,
    audio_url: null,
    voice_ref: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function validScene(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "asset-1",
    series_id: "series-1",
    title: "旧街清晨",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function validProp(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "asset-1",
    series_id: "series-1",
    name: "旧信封",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

interface AssetContractCase {
  name: string;
  parse(value: unknown, expectedSeriesId: string): SeriesAssetFields[];
  valid(overrides?: Record<string, unknown>): Record<string, unknown>;
  requiredNullableFields: string[];
}

const assetContractCases: AssetContractCase[] = [
  {
    name: "character",
    parse: parseCharacterList,
    valid: validCharacter,
    requiredNullableFields: ["gender", "age", "role", "appearance", "description", "image_url", "audio_url", "voice_ref"],
  },
  {
    name: "scene",
    parse: parseSceneList,
    valid: validScene,
    requiredNullableFields: ["description", "image_url"],
  },
  {
    name: "prop",
    parse: parsePropList,
    valid: validProp,
    requiredNullableFields: ["description", "image_url"],
  },
];

describe("material list runtime contracts", () => {
  it.each(assetContractCases)("preserves $name response order and permits empty display names", ({ parse, valid }) => {
    const rows = [
      valid({ id: "asset-b", name: "", title: "" }),
      valid({ id: "asset-a", name: "有名称", title: "有标题" }),
    ];
    expect(parse(rows, "series-1").map((asset) => asset.id)).toEqual(["asset-b", "asset-a"]);
    expect(parse([valid({ aliases: ["", "林岚"], canonical_key: "" })], "series-1")[0])
      .toMatchObject({ aliases: ["", "林岚"], canonical_key: "" });
  });

  it.each(assetContractCases)("accepts null and empty strings for $name required nullable fields", ({ parse, valid, requiredNullableFields }) => {
    expect(parse([valid()], "series-1")[0]).toMatchObject(
      Object.fromEntries(requiredNullableFields.map((field) => [field, null])),
    );
    expect(parse([valid(Object.fromEntries(requiredNullableFields.map((field) => [field, ""])))], "series-1")[0])
      .toMatchObject(Object.fromEntries(requiredNullableFields.map((field) => [field, ""])));
  });

  it.each(assetContractCases.flatMap((contractCase) => contractCase.requiredNullableFields.map((field) => ({
    ...contractCase,
    field,
  }))))("rejects missing or undefined required nullable $name.$field", ({ parse, valid, field }) => {
    const missing = valid();
    delete missing[field];
    expect(() => parse([missing], "series-1")).toThrow(InvalidResponseError);
    expect(() => parse([valid({ [field]: undefined })], "series-1")).toThrow(InvalidResponseError);
  });

  it.each(assetContractCases)("rejects malformed, cross-series, and duplicate $name rows", ({ parse, valid }) => {
    expect(() => parse({}, "series-1")).toThrow(InvalidResponseError);
    expect(() => parse([null], "series-1")).toThrow(InvalidResponseError);
    expect(() => parse([valid({ aliases: '["林岚"]' })], "series-1")).toThrow(InvalidResponseError);
    expect(() => parse([valid({ aliases: ["ok", 3] })], "series-1")).toThrow(InvalidResponseError);
    expect(() => parse([valid({ canonical_key: 4 })], "series-1")).toThrow(InvalidResponseError);
    expect(() => parse([valid({ updated_at: "2026-02-30T00:00:00" })], "series-1"))
      .toThrow(InvalidResponseError);
    expect(() => parse([valid({ series_id: "another-series" })], "series-1"))
      .toThrow(InvalidResponseError);
    expect(() => parse([valid(), valid()], "series-1")).toThrow(InvalidResponseError);
  });
});

describe("chapter and storyboard asset runtime contracts", () => {
  it("preserves valid empty titles, nullable content, lock snapshots, and API order", () => {
    const rows = [
      validChapter({ id: "chapter-z", title: "", content: null, lock: { locked: true, is_mine: false } }),
      validChapter({ id: "chapter-a", order: -1, content: [] }),
    ];
    expect(parseChapterList(rows, "series-1")).toMatchObject(rows);
  });

  it.each([
    { label: "non-array response", value: {} },
    { label: "non-object chapter", value: [null] },
    { label: "empty identity", value: [validChapter({ id: "" })] },
    { label: "wrong series", value: [validChapter({ series_id: "another-series" })] },
    { label: "invalid order", value: [validChapter({ order: 1.5 })] },
    { label: "invalid timestamp", value: [validChapter({ updated_at: "not-a-date" })] },
    { label: "non-ISO timestamp accepted by Date.parse", value: [validChapter({ created_at: "Thu, 01 Jan 1970 00:00:00 GMT" })] },
    { label: "impossible calendar date", value: [validChapter({ updated_at: "2026-02-30T08:00:00" })] },
    { label: "non-object frame", value: [validChapter({ content: ["not an object"] })] },
    { label: "invalid lock", value: [validChapter({ lock: [] })] },
    { label: "duplicate id", value: [validChapter(), validChapter({ title: "重复" })] },
  ])("rejects $label chapter data as an invalid response", ({ value }) => {
    expect(() => parseChapterList(value, "series-1")).toThrow(InvalidResponseError);
  });

  it("accepts nullable asset fields and an empty schema-valid asset name", () => {
    expect(parseStoryboardAssetList([
      validAsset({ name: "", description: "", image_url: null }),
    ])).toMatchObject([{ name: "", description: "", image_url: null }]);
  });

  it.each([
    { value: {} },
    { value: [null] },
    { value: [validAsset({ id: "" })] },
    { value: [validAsset({ frame_index: 1.25 })] },
    { value: [validAsset({ created_at: "invalid" })] },
    { value: [validAsset({ updated_at: "Thu, 01 Jan 1970 00:00:00 GMT" })] },
    { value: [validAsset({ image_url: 17 })] },
  ])("rejects malformed storyboard asset response", ({ value }) => {
    expect(() => parseStoryboardAssetList(value)).toThrow(InvalidResponseError);
  });
});
