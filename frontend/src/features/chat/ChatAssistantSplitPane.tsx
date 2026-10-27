import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import {
  DEFAULT_CHAT_MESSAGES_SHARE,
  MAX_CHAT_MESSAGES_SHARE,
  MIN_CHAT_MESSAGES_SHARE,
  readChatAssistantSplitPanePreference,
  writeChatAssistantSplitPanePreference,
} from "./chatAssistantSplitPanePreferences";
import "./chatAssistantSplitPane.css";

interface ChatAssistantSplitPaneProps {
  readonly userId: string;
  readonly apiBaseUrl: string | null | undefined;
  readonly sessionGeneration: number;
  readonly messages: ReactNode;
  readonly input: ReactNode;
}

interface PreferenceViewState {
  readonly owner: string;
  readonly messageShare: number;
  readonly notice: string | null;
}

interface SplitPaneOwner {
  readonly identity: string;
  readonly sessionGeneration: number;
  readonly userId: string;
  readonly apiBaseUrl: string | null | undefined;
}

interface ActivePointerDrag {
  readonly identity: string;
  readonly sessionGeneration: number;
  readonly pointerId: number;
  readonly startShare: number;
  readonly detachListeners: () => void;
}

function identityForPreference(userId: string, apiBaseUrl: string | null | undefined): string {
  return JSON.stringify([userId, apiBaseUrl ?? null]);
}

function restoredViewState(
  owner: string,
  userId: string,
  apiBaseUrl: string | null | undefined,
): PreferenceViewState {
  const preference = readChatAssistantSplitPanePreference({ userId, apiBaseUrl });
  if (preference.status === "unavailable") {
    return {
      owner,
      messageShare: DEFAULT_CHAT_MESSAGES_SHARE,
      notice: preference.reason === "scope"
        ? "账号或 API 地址尚未确定，分隔比例不会保存；当前仍可手动调整。"
        : "浏览器存储不可用，未能恢复分隔比例；当前仍可手动调整。",
    };
  }
  if (preference.malformed) {
    return {
      owner,
      messageShare: DEFAULT_CHAT_MESSAGES_SHARE,
      notice: "上次保存的分隔比例无效，已使用默认比例；当前仍可手动调整。",
    };
  }
  return {
    owner,
    messageShare: preference.messageShare ?? DEFAULT_CHAT_MESSAGES_SHARE,
    notice: null,
  };
}

function writeNotice(result: ReturnType<typeof writeChatAssistantSplitPanePreference>): string | null {
  if (result.status === "saved") return null;
  if (result.status === "invalid") return "分隔比例无效，当前页面仍可手动调整。";
  return result.reason === "scope"
    ? "账号或 API 地址尚未确定，分隔比例未保存；当前页面仍可手动调整。"
    : "浏览器存储不可用，分隔比例未保存；当前页面仍可手动调整。";
}

function isPaneInteractive(root: HTMLElement | null): boolean {
  if (root === null || !root.isConnected || root.closest("[hidden], [inert], [aria-hidden='true']") !== null) return false;
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

function clampMessageShare(value: number): number {
  return Math.max(MIN_CHAT_MESSAGES_SHARE, Math.min(MAX_CHAT_MESSAGES_SHARE, Math.round(value)));
}

export function ChatAssistantSplitPane({
  userId,
  apiBaseUrl,
  sessionGeneration,
  messages,
  input,
}: ChatAssistantSplitPaneProps) {
  const preferenceOwner = identityForPreference(userId, apiBaseUrl);
  const initialState = useRef<PreferenceViewState | null>(null);
  if (initialState.current === null) initialState.current = restoredViewState(preferenceOwner, userId, apiBaseUrl);

  const [viewState, setViewState] = useState(initialState.current);
  const [isDragging, setIsDragging] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const separatorRef = useRef<HTMLDivElement>(null);
  const pointerDragRef = useRef<ActivePointerDrag | null>(null);
  const restoreGenerationRef = useRef(0);
  const interactionVersionRef = useRef(0);
  const ownerRef = useRef<SplitPaneOwner>({ identity: preferenceOwner, sessionGeneration, userId, apiBaseUrl });
  ownerRef.current = { identity: preferenceOwner, sessionGeneration, userId, apiBaseUrl };

  const currentView = viewState.owner === preferenceOwner
    ? viewState
    : { owner: preferenceOwner, messageShare: DEFAULT_CHAT_MESSAGES_SHARE, notice: null };
  const messageShareRef = useRef(currentView.messageShare);
  messageShareRef.current = currentView.messageShare;

  const updateView = useCallback((owner: string, messageShare: number, notice?: string | null) => {
    messageShareRef.current = messageShare;
    setViewState((current) => {
      const base = current.owner === owner
        ? current
        : { owner, messageShare: DEFAULT_CHAT_MESSAGES_SHARE, notice: null };
      return { ...base, messageShare, ...(notice === undefined ? {} : { notice }) };
    });
  }, []);

  const cancelPointerDrag = useCallback(() => {
    const drag = pointerDragRef.current;
    if (drag === null) return;
    pointerDragRef.current = null;
    drag.detachListeners();
    const separator = separatorRef.current;
    if (separator !== null && typeof separator.releasePointerCapture === "function") {
      try {
        if (typeof separator.hasPointerCapture !== "function" || separator.hasPointerCapture(drag.pointerId)) {
          separator.releasePointerCapture(drag.pointerId);
        }
      } catch {
        // Pointer capture may already have been released by the browser.
      }
    }
    setIsDragging(false);
    const owner = ownerRef.current;
    if (owner.identity === drag.identity) {
      updateView(drag.identity, drag.startShare);
    }
  }, [updateView]);

  useEffect(() => {
    const generation = ++restoreGenerationRef.current;
    const interactionVersion = interactionVersionRef.current;
    const restored = restoredViewState(preferenceOwner, userId, apiBaseUrl);
    if (ownerRef.current.identity === preferenceOwner
      && restoreGenerationRef.current === generation
      && interactionVersionRef.current === interactionVersion) {
      updateView(restored.owner, restored.messageShare, restored.notice);
    }
    return () => {
      if (restoreGenerationRef.current === generation) restoreGenerationRef.current += 1;
    };
  }, [apiBaseUrl, preferenceOwner, updateView, userId]);

  useEffect(() => {
    const handleWindowBlur = () => cancelPointerDrag();
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") cancelPointerDrag();
    };
    window.addEventListener("blur", handleWindowBlur);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("blur", handleWindowBlur);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      cancelPointerDrag();
    };
  }, [cancelPointerDrag, preferenceOwner, sessionGeneration]);

  const persistShare = useCallback((ownerIdentity: string, messageShare: number) => {
    const owner = ownerRef.current;
    if (owner.identity !== ownerIdentity) return;
    interactionVersionRef.current += 1;
    const result = writeChatAssistantSplitPanePreference({ userId: owner.userId, apiBaseUrl: owner.apiBaseUrl }, messageShare);
    updateView(ownerIdentity, messageShare, writeNotice(result));
  }, [updateView]);

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!isPaneInteractive(rootRef.current)) return;
    const currentShare = messageShareRef.current;
    let nextShare: number | null = null;
    if (event.key === "ArrowUp") nextShare = clampMessageShare(currentShare + 5);
    else if (event.key === "ArrowDown") nextShare = clampMessageShare(currentShare - 5);
    else if (event.key === "Home") nextShare = MIN_CHAT_MESSAGES_SHARE;
    else if (event.key === "End") nextShare = MAX_CHAT_MESSAGES_SHARE;
    if (nextShare === null) return;
    event.preventDefault();
    cancelPointerDrag();
    const owner = ownerRef.current;
    persistShare(owner.identity, nextShare);
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isPaneInteractive(rootRef.current) || event.isPrimary === false) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if (pointerDragRef.current !== null) cancelPointerDrag();
    event.preventDefault();

    const owner = ownerRef.current;
    const pointerId = event.pointerId;
    const startShare = messageShareRef.current;
    interactionVersionRef.current += 1;
    const separator = separatorRef.current;
    try {
      separator?.setPointerCapture?.(pointerId);
    } catch {
      // Window listeners below also support browsers without pointer capture.
    }

    let drag: ActivePointerDrag;
    const detachListeners = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerCancel);
    };
    const handlePointerMove = (nativeEvent: PointerEvent) => {
      if (pointerDragRef.current !== drag || nativeEvent.pointerId !== pointerId) return;
      const currentOwner = ownerRef.current;
      if (currentOwner.identity !== owner.identity
        || currentOwner.sessionGeneration !== owner.sessionGeneration
        || !isPaneInteractive(rootRef.current)) {
        cancelPointerDrag();
        return;
      }
      const root = rootRef.current;
      const rootRect = root?.getBoundingClientRect();
      const separatorHeight = separatorRef.current?.getBoundingClientRect().height ?? 0;
      const availableHeight = (rootRect?.height ?? 0) - separatorHeight;
      if (rootRect === undefined || availableHeight <= 0) return;
      const messageShare = clampMessageShare(((nativeEvent.clientY - rootRect.top) / availableHeight) * 100);
      updateView(owner.identity, messageShare);
    };
    const handlePointerUp = (nativeEvent: PointerEvent) => {
      if (pointerDragRef.current !== drag || nativeEvent.pointerId !== pointerId) return;
      const currentOwner = ownerRef.current;
      if (currentOwner.identity !== owner.identity
        || currentOwner.sessionGeneration !== owner.sessionGeneration
        || !isPaneInteractive(rootRef.current)) {
        cancelPointerDrag();
        return;
      }
      pointerDragRef.current = null;
      detachListeners();
      setIsDragging(false);
      const activeSeparator = separatorRef.current;
      if (activeSeparator !== null && typeof activeSeparator.releasePointerCapture === "function") {
        try {
          if (typeof activeSeparator.hasPointerCapture !== "function" || activeSeparator.hasPointerCapture(pointerId)) {
            activeSeparator.releasePointerCapture(pointerId);
          }
        } catch {
          // Pointer capture may already have been released by the browser.
        }
      }
      persistShare(owner.identity, messageShareRef.current);
    };
    const handlePointerCancel = (nativeEvent: PointerEvent) => {
      if (nativeEvent.pointerId === pointerId) cancelPointerDrag();
    };
    drag = {
      identity: owner.identity,
      sessionGeneration: owner.sessionGeneration,
      pointerId,
      startShare,
      detachListeners,
    };
    pointerDragRef.current = drag;
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerCancel);
    setIsDragging(true);
  };

  const handleReset = () => {
    cancelPointerDrag();
    persistShare(ownerRef.current.identity, DEFAULT_CHAT_MESSAGES_SHARE);
  };
  const generatedId = useId().replaceAll(":", "");
  const messagePaneId = `chat-assistant-messages-${generatedId}`;
  const inputPaneId = `chat-assistant-input-${generatedId}`;
  const splitStyle = {
    "--chat-assistant-message-share": currentView.messageShare,
    "--chat-assistant-input-share": 100 - currentView.messageShare,
  } as CSSProperties;

  return (
    <section className="chat-assistant-split-layout" aria-label="聊天记录与输入区域">
      <div className="chat-assistant-split-toolbar">
        {currentView.notice !== null && <p role="status">{currentView.notice}</p>}
        <button className="text-button" onClick={handleReset} type="button">重置分隔比例</button>
      </div>
      <div
        ref={rootRef}
        className="chat-assistant-split-pane"
        data-dragging={isDragging ? "true" : "false"}
        data-message-share={currentView.messageShare}
        style={splitStyle}
        data-testid="chat-assistant-split-pane"
      >
        <div id={messagePaneId} className="chat-assistant-split-messages">{messages}</div>
        <div
          ref={separatorRef}
          aria-controls={`${messagePaneId} ${inputPaneId}`}
          aria-label="调整聊天记录与输入区域比例"
          aria-orientation="horizontal"
          aria-valuemax={MAX_CHAT_MESSAGES_SHARE}
          aria-valuemin={MIN_CHAT_MESSAGES_SHARE}
          aria-valuenow={currentView.messageShare}
          aria-valuetext={`聊天记录 ${currentView.messageShare}%，输入区域 ${100 - currentView.messageShare}%`}
          className="chat-assistant-split-divider"
          onKeyDown={handleKeyDown}
          onLostPointerCapture={cancelPointerDrag}
          onPointerDown={handlePointerDown}
          role="separator"
          tabIndex={0}
        />
        <div id={inputPaneId} className="chat-assistant-split-input">{input}</div>
      </div>
    </section>
  );
}
