import { describe, expect, it } from "vitest";
import { parseCreatorModel } from "./creatorSettings";

function response(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "model-1", category: "video", name: "视频模型", provider: "fixture",
    model: "video-fixture", base_url: null, params: "{}", sort_order: 0,
    is_system: true, user_id: "system", ...overrides,
  };
}

describe("creator model public projection", () => {
  it("retains a safe public cost estimate while discarding model secrets", () => {
    const model = parseCreatorModel(response({ cost_per_call: 7, api_key: "DO_NOT_EXPOSE" }));
    expect(model.cost_per_call).toBe(7);
    expect(JSON.stringify(model)).not.toContain("DO_NOT_EXPOSE");
    expect(parseCreatorModel(response({ cost_per_call: -1 })).cost_per_call).toBeNull();
    expect(parseCreatorModel(response()).cost_per_call).toBeUndefined();
  });
});
