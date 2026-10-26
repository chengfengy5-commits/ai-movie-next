import { useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  CreatorModelConnectionTestInput,
  CreatorSettingsServicePort,
} from "../../shared/api/creatorSettings";

const CONNECTION_TEST_TIMEOUT_MS = 16_000;

interface ModelConnectionTestProps {
  ownerId: string;
  draftGeneration: number;
  service: CreatorSettingsServicePort | undefined;
  input: CreatorModelConnectionTestInput;
  onUnauthorized(): void;
}

type ConnectionResult = {
  kind: "success" | "failure" | "unknown";
  message: string;
};

interface CapturedRequest {
  ownerId: string;
  draftGeneration: number;
  service: CreatorSettingsServicePort | undefined;
  input: CreatorModelConnectionTestInput;
}

function redactApiKey(message: string, apiKey: string): string {
  return apiKey === "" ? message : message.split(apiKey).join("[密钥已隐藏]");
}

function capturedScopeMatches(
  current: CapturedRequest,
  captured: CapturedRequest,
  attempt: number,
  currentAttempt: number,
): boolean {
  return currentAttempt === attempt
    && current.ownerId === captured.ownerId
    && current.draftGeneration === captured.draftGeneration
    && current.service === captured.service
    && current.input.provider === captured.input.provider
    && current.input.api_key === captured.input.api_key
    && current.input.base_url === captured.input.base_url
    && current.input.model === captured.input.model
    && current.input.category === captured.input.category;
}

function describeRequestFailure(error: unknown, didTimeout: boolean, apiKey: string): ConnectionResult {
  if (didTimeout || (error instanceof ApiError && error.kind === "timeout")) {
    return { kind: "unknown", message: "连接测试等待超时，结果未知；没有自动重试。" };
  }
  if (error instanceof ApiError && error.kind === "network") {
    return { kind: "unknown", message: "连接测试网络中断，结果未知；没有自动重试。" };
  }
  if (error instanceof ApiError && error.status === 401) {
    return { kind: "failure", message: "当前登录状态已失效，请重新登录。" };
  }
  const rawMessage = error instanceof Error ? error.message : "连接测试未能确认。";
  const safeMessage = redactApiKey(rawMessage, apiKey);
  return {
    kind: "failure",
    message: safeMessage === "" ? "连接测试未能确认。" : safeMessage,
  };
}

export function ModelConnectionTest({
  ownerId,
  draftGeneration,
  service,
  input,
  onUnauthorized,
}: ModelConnectionTestProps) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ConnectionResult | null>(null);
  const pendingLock = useRef(false);
  const attemptGeneration = useRef(0);
  const activeController = useRef<AbortController | null>(null);
  const currentRequest = useRef<CapturedRequest>({ ownerId, draftGeneration, service, input });
  currentRequest.current = { ownerId, draftGeneration, service, input };

  useLayoutEffect(() => {
    attemptGeneration.current += 1;
    activeController.current?.abort();
    activeController.current = null;
    pendingLock.current = false;
    setPending(false);
    setResult(null);
    return () => {
      attemptGeneration.current += 1;
      activeController.current?.abort();
      activeController.current = null;
    };
  }, [ownerId, draftGeneration, service, input.provider, input.api_key, input.base_url, input.model, input.category]);

  async function testConnection(): Promise<void> {
    if (pendingLock.current) return;
    const captured: CapturedRequest = {
      ownerId,
      draftGeneration,
      service,
      input: { ...input },
    };
    const testModelConnection = captured.service?.testModelConnection;
    if (captured.input.api_key.trim() === "" || testModelConnection === undefined) return;

    pendingLock.current = true;
    const attempt = ++attemptGeneration.current;
    const controller = new AbortController();
    activeController.current = controller;
    setPending(true);
    setResult(null);

    let didTimeout = false;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_resolve, reject) => {
      timeoutId = setTimeout(() => {
        didTimeout = true;
        controller.abort();
        reject(new ApiError("timeout", "连接测试等待超时"));
      }, CONNECTION_TEST_TIMEOUT_MS);
    });
    const isCurrent = () => capturedScopeMatches(
      currentRequest.current,
      captured,
      attempt,
      attemptGeneration.current,
    );

    try {
      const response = await Promise.race([
        testModelConnection(captured.input, controller.signal),
        timeout,
      ]);
      if (!isCurrent() || controller.signal.aborted) return;
      const message = redactApiKey(response.message, captured.input.api_key);
      setResult({
        kind: response.success ? "success" : "failure",
        message: message || (response.success ? "连接成功。" : "连接失败。"),
      });
    } catch (error) {
      if (!isCurrent() || controller.signal.aborted && !didTimeout) return;
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorized();
        return;
      }
      setResult(describeRequestFailure(error, didTimeout, captured.input.api_key));
    } finally {
      if (timeoutId !== undefined) clearTimeout(timeoutId);
      if (isCurrent()) {
        pendingLock.current = false;
        activeController.current = null;
        setPending(false);
      }
    }
  }

  const hasKey = input.api_key.trim() !== "";
  const hasService = typeof service?.testModelConnection === "function";

  return (
    <div className="creator-settings-connection-test creator-settings-field-wide">
      <button
        disabled={pending || !hasKey || !hasService}
        onClick={() => void testConnection()}
        type="button"
      >
        {pending ? "测试中…" : "测试连接"}
      </button>
      <p className="creator-settings-hint">
        测试只使用当前草稿里重新输入的 API 密钥，不会保存模型或发起生成；编辑已保存模型时，请重新输入密钥后再测试。
      </p>
      {!hasService && <p className="creator-settings-hint">连接测试服务尚未接入当前运行配置。</p>}
      {result !== null && (
        <p
          className={result.kind === "success" ? "creator-settings-connection-success" : "creator-settings-connection-error"}
          role={result.kind === "success" ? "status" : "alert"}
        >
          {result.message}
        </p>
      )}
    </div>
  );
}
