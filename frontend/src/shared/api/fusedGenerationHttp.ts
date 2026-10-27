import { ApiError } from "./errors";
import type {
  FusedGenerationInput,
  FusedGenerationJsonRequester,
  FusedGenerationPort,
  FusedGenerationReceipt,
} from "./fusedGeneration";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSafeId(value: unknown): value is string {
  return typeof value === "string" && value.trim() === value && value.length > 0
    && value.length <= 128 && !/[\u0000-\u001f\u007f-\u009f/\\]/u.test(value);
}

function parseReceipt(value: unknown): FusedGenerationReceipt {
  if (!isRecord(value)
    || !Object.prototype.hasOwnProperty.call(value, "task_id")
    || !Object.prototype.hasOwnProperty.call(value, "message_id")
    || !isSafeId(value.task_id)
    || (value.message_id !== null && !isSafeId(value.message_id))) {
    throw new ApiError("invalid-response", "融合生成服务没有返回可核对的任务编号。", 200);
  }
  return Object.freeze({ taskId: value.task_id, messageId: value.message_id });
}

function validateInput(input: FusedGenerationInput): void {
  if (!isSafeId(input.series_id) || !["character", "prop"].includes(input.member_type)
    || !isSafeId(input.model_id) || typeof input.prompt_id !== "string"
    || (input.prompt_system !== null && typeof input.prompt_system !== "string")
    || (input.prompt_user !== null && typeof input.prompt_user !== "string")
    || (input.size !== null && typeof input.size !== "string")
    || input.chapter_id !== null || input.frame_index !== null || input.name !== null
    || !Array.isArray(input.members) || input.members.length === 0) {
    throw new ApiError("invalid-response", "融合生成请求字段无效，未发送请求。", null);
  }
  for (const member of input.members) {
    if (member.kind === "asset") {
      if (!isSafeId(member.id) || typeof member.name !== "string") {
        throw new ApiError("invalid-response", "融合成员身份无效，未发送请求。", null);
      }
    } else if (member.kind === "image") {
      if (typeof member.url !== "string" || member.url.trim() !== member.url || member.url === ""
        || /[\u0000-\u001f\u007f-\u009f]/u.test(member.url) || typeof member.name !== "string") {
        throw new ApiError("invalid-response", "自定义图片地址无效，未发送请求。", null);
      }
      try {
        const url = new URL(member.url);
        if ((url.protocol !== "http:" && url.protocol !== "https:") || url.hostname === ""
          || url.username !== "" || url.password !== "" || url.hash !== "") {
          throw new Error("unsafe image URL");
        }
      } catch {
        throw new ApiError("invalid-response", "自定义图片地址无效，未发送请求。", null);
      }
    } else {
      throw new ApiError("invalid-response", "融合成员类型无效，未发送请求。", null);
    }
  }
}

export function createFusedGenerationHttp(requestJson: FusedGenerationJsonRequester): FusedGenerationPort {
  return {
    async submit(input, signal) {
      validateInput(input);
      const payload: FusedGenerationInput = {
        series_id: input.series_id,
        member_type: input.member_type,
        members: input.members.map((member) => member.kind === "asset"
          ? { kind: "asset", id: member.id, name: member.name }
          : { kind: "image", url: member.url, name: member.name }),
        model_id: input.model_id,
        prompt_id: input.prompt_id,
        prompt_system: input.prompt_system,
        prompt_user: input.prompt_user,
        size: input.size,
        chapter_id: null,
        frame_index: null,
        name: null,
      };
      return parseReceipt(await requestJson(payload, signal));
    },
  };
}
