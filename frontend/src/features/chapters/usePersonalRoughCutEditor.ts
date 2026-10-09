import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { StoryboardAsset } from "../../shared/api/contracts";
import type { ApiError } from "../../shared/api/errors";
import {
  parsePersonalRoughCutSaveResponse,
  type PersonalRoughCutSnapshot,
  type PersonalRoughCutUpdateFrame,
} from "../../shared/api/personalRoughCut";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import {
  createRoughCutDraft,
  decideRoughCutSave,
  moveRoughCutFrame,
  setRoughCutIncluded,
  type RoughCutSourceCheck,
} from "./personal-production/roughCutEdit";

interface EditorWriteTicket {
  readonly snapshot: PersonalRoughCutSnapshot;
  readonly sourceIdentity: AssetSourceIdentity;
  readonly readGeneration: number;
  readonly editorGeneration: number;
  readonly draftGeneration: number;
  readonly writeGeneration: number;
}

interface AssetSourceIdentity {
  readonly assets: StoryboardAsset[] | null;
  readonly token: object | null;
  readonly requestGeneration: number;
  readonly status: "loading" | "ready" | "error";
}

type SaveState =
  | { readonly status: "idle" }
  | {
      readonly status: "pending";
      readonly ticket: EditorWriteTicket;
      readonly submitted: readonly PersonalRoughCutUpdateFrame[];
      readonly message: string;
    }
  | {
      readonly status: "locked";
      readonly ticket: EditorWriteTicket;
      readonly submitted: readonly PersonalRoughCutUpdateFrame[];
      readonly message: string;
    };

interface EditorState {
  readonly snapshot: PersonalRoughCutSnapshot | null;
  readonly readGeneration: number;
  readonly draft: readonly PersonalRoughCutUpdateFrame[];
  readonly draftGeneration: number;
  readonly editing: boolean;
  readonly editorGeneration: number;
  readonly sourceIdentity: AssetSourceIdentity | null;
  readonly saveState: SaveState;
}

interface LatestInputs {
  readonly chapterId: string;
  readonly save: WorkspaceServices["savePersonalRoughCut"] | undefined;
  readonly sourceCheck: RoughCutSourceCheck;
  readonly sourceIdentity: AssetSourceIdentity;
  readonly isScopeCurrent: () => boolean;
  readonly onBeforeSave: () => void;
  readonly onSaved: (snapshot: PersonalRoughCutSnapshot, readGeneration: number) => void;
  readonly onUnauthorized: () => void;
}

export interface PersonalRoughCutEditorController {
  readonly snapshot: PersonalRoughCutSnapshot | null;
  readonly readGeneration: number;
  readonly editorGeneration: number;
  readonly draftGeneration: number;
  readonly draft: readonly PersonalRoughCutUpdateFrame[];
  readonly displayedSnapshot: PersonalRoughCutSnapshot | null;
  readonly editing: boolean;
  readonly isSaving: boolean;
  readonly isLocked: boolean;
  readonly message: string | null;
  readonly canEdit: boolean;
  readonly canSave: boolean;
  readonly acceptRead: (snapshot: PersonalRoughCutSnapshot, readGeneration: number) => void;
  readonly beginRead: (readGeneration: number) => boolean;
  readonly isCurrentRead: (readGeneration: number) => boolean;
  readonly isCurrentNavigationRead: (readGeneration: number) => boolean;
  readonly isCurrentPanelAction: (readGeneration: number, editorGeneration: number, draftGeneration: number) => boolean;
  readonly openEditor: () => void;
  readonly cancelEditor: () => void;
  readonly moveFrame: (index: number, direction: -1 | 1) => void;
  readonly setIncluded: (index: number, included: boolean) => void;
  readonly save: () => void;
}

const INITIAL_STATE: EditorState = {
  snapshot: null,
  readGeneration: -1,
  draft: [],
  draftGeneration: 0,
  editing: false,
  editorGeneration: 0,
  sourceIdentity: null,
  saveState: { status: "idle" },
};

function sameAssetSource(
  left: AssetSourceIdentity,
  right: AssetSourceIdentity,
  requiresAssets: boolean,
): boolean {
  return !requiresAssets || (
    left.assets === right.assets
    && left.token === right.token
    && left.requestGeneration === right.requestGeneration
    && left.status === right.status
  );
}

function assetSourceReady(inputs: LatestInputs, requiresAssets: boolean): boolean {
  return !requiresAssets || (
    inputs.sourceIdentity.status === "ready"
    && inputs.sourceIdentity.assets !== null
    && inputs.sourceIdentity.token !== null
    && inputs.sourceCheck.valid
  );
}

function editorSourceIsCurrent(
  current: EditorState,
  inputs: LatestInputs,
  invalidated: boolean,
): boolean {
  if (current.snapshot === null || current.sourceIdentity === null || invalidated) {
    return false;
  }
  return sameAssetSource(
    current.sourceIdentity,
    inputs.sourceIdentity,
    current.snapshot.frames.length > 0,
  );
}

function submittedSnapshot(
  snapshot: PersonalRoughCutSnapshot,
  submitted: readonly PersonalRoughCutUpdateFrame[],
): PersonalRoughCutSnapshot {
  const currentRows = new Map(snapshot.frames.map((frame) => [frame.asset_id, frame]));
  return {
    ...snapshot,
    frames: submitted.map((frame, index) => {
      const current = currentRows.get(frame.asset_id);
      return current === undefined
        ? {
            asset_id: frame.asset_id,
            frame_index: index,
            text: "",
            preview_url: null,
            missing_reason: null,
            included: frame.included,
            pending: false,
          }
        : { ...current, included: frame.included };
    }),
  };
}

export function usePersonalRoughCutEditor(
  inputs: LatestInputs,
): PersonalRoughCutEditorController {
  const [state, setState] = useState(INITIAL_STATE);
  const stateRef = useRef(state);
  const latestInputs = useRef(inputs);
  latestInputs.current = inputs;
  const mounted = useRef(true);
  const writeGeneration = useRef(0);
  const saveController = useRef<AbortController | null>(null);
  const invalidEditorSource = useRef(false);
  const currentInputs = latestInputs.current;
  const currentEditor = stateRef.current;
  if (
    currentEditor.snapshot !== null
    && currentEditor.snapshot.frames.length > 0
    && currentEditor.sourceIdentity !== null
    && !sameAssetSource(currentEditor.sourceIdentity, currentInputs.sourceIdentity, true)
  ) {
    invalidEditorSource.current = true;
  }

  const publish = useCallback((next: EditorState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  useLayoutEffect(() => {
    const current = stateRef.current;
    if (
      current.saveState.status !== "pending"
      || current.snapshot === null
      || current.snapshot.frames.length === 0
      || (
        sameAssetSource(current.saveState.ticket.sourceIdentity, latestInputs.current.sourceIdentity, true)
        && assetSourceReady(latestInputs.current, true)
      )
    ) {
      return;
    }

    writeGeneration.current += 1;
    invalidEditorSource.current = true;
    const { ticket, submitted } = current.saveState;
    const controller = saveController.current;
    saveController.current = null;
    publish({
      ...current,
      saveState: {
        status: "locked",
        ticket,
        submitted,
        message: "素材目录已变化，保存结果暂时无法核对。",
      },
    });
    controller?.abort();
  }, [inputs.sourceIdentity.assets, inputs.sourceIdentity.token, inputs.sourceIdentity.requestGeneration, inputs.sourceIdentity.status, publish]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      writeGeneration.current += 1;
      saveController.current?.abort();
      saveController.current = null;
    };
  }, []);

  const isCurrentRead = useCallback((readGeneration: number) => (
    mounted.current
    && stateRef.current.readGeneration === readGeneration
    && latestInputs.current.isScopeCurrent()
  ), []);

  const isCurrentNavigationRead = useCallback((readGeneration: number) => (
    mounted.current
    && stateRef.current.readGeneration === readGeneration
    && stateRef.current.saveState.status === "idle"
    && latestInputs.current.isScopeCurrent()
  ), []);

  const isCurrentPanelAction = useCallback((
    readGeneration: number,
    editorGeneration: number,
    draftGeneration: number,
  ) => (
    mounted.current
    && stateRef.current.readGeneration === readGeneration
    && stateRef.current.editorGeneration === editorGeneration
    && stateRef.current.draftGeneration === draftGeneration
    && latestInputs.current.isScopeCurrent()
  ), []);

  const beginRead = useCallback((readGeneration: number): boolean => {
    const current = stateRef.current;
    if (
      !mounted.current
      || readGeneration <= current.readGeneration
      || current.saveState.status === "pending"
      || !latestInputs.current.isScopeCurrent()
    ) {
      return false;
    }

    writeGeneration.current += 1;
    const previousController = saveController.current;
    saveController.current = null;
    publish({
      ...current,
      readGeneration,
      editing: false,
      editorGeneration: current.editorGeneration + 1,
      draftGeneration: current.draftGeneration + 1,
    });
    previousController?.abort();
    return true;
  }, [publish]);

  const acceptRead = useCallback((snapshot: PersonalRoughCutSnapshot, readGeneration: number) => {
    if (!mounted.current || !latestInputs.current.isScopeCurrent()) {
      return;
    }
    const current = stateRef.current;
    if (readGeneration !== current.readGeneration) {
      return;
    }

    writeGeneration.current += 1;
    invalidEditorSource.current = false;
    const previousController = saveController.current;
    saveController.current = null;
    publish({
      snapshot,
      readGeneration,
      draft: createRoughCutDraft(snapshot),
      draftGeneration: current.draftGeneration + 1,
      editing: false,
      editorGeneration: current.editorGeneration + 1,
      sourceIdentity: null,
      saveState: { status: "idle" },
    });
    previousController?.abort();
  }, [publish]);

  const openEditor = useCallback((
    expectedSnapshot: PersonalRoughCutSnapshot | null = stateRef.current.snapshot,
    expectedReadGeneration = stateRef.current.readGeneration,
    expectedEditorGeneration = stateRef.current.editorGeneration,
    expectedSourceIdentity: AssetSourceIdentity = latestInputs.current.sourceIdentity,
  ) => {
    const current = stateRef.current;
    const currentInputs = latestInputs.current;
    if (
      !mounted.current
      || current.snapshot === null
      || current.snapshot !== expectedSnapshot
      || current.readGeneration !== expectedReadGeneration
      || current.editorGeneration !== expectedEditorGeneration
      || current.saveState.status !== "idle"
      || currentInputs.save === undefined
      || (current.snapshot.frames.length > 0 && invalidEditorSource.current)
      || !currentInputs.isScopeCurrent()
      || current.readGeneration < 0
      || !sameAssetSource(expectedSourceIdentity, currentInputs.sourceIdentity, current.snapshot.frames.length > 0)
    ) {
      return;
    }
    publish({
      ...current,
      editing: true,
      editorGeneration: current.editorGeneration + 1,
      sourceIdentity: currentInputs.sourceIdentity,
    });
  }, [publish]);

  const cancelEditor = useCallback((
    expectedSnapshot: PersonalRoughCutSnapshot | null = stateRef.current.snapshot,
    expectedReadGeneration = stateRef.current.readGeneration,
    expectedEditorGeneration = stateRef.current.editorGeneration,
    expectedDraftGeneration = stateRef.current.draftGeneration,
  ) => {
    const current = stateRef.current;
    if (
      !mounted.current
      || current.snapshot !== expectedSnapshot
      || current.readGeneration !== expectedReadGeneration
      || current.editorGeneration !== expectedEditorGeneration
      || current.draftGeneration !== expectedDraftGeneration
      || current.saveState.status !== "idle"
      || !latestInputs.current.isScopeCurrent()
      || !editorSourceIsCurrent(current, latestInputs.current, invalidEditorSource.current)
    ) {
      return;
    }
    publish({ ...current, editing: false, editorGeneration: current.editorGeneration + 1 });
  }, [publish]);

  const moveFrame = useCallback((
    index: number,
    direction: -1 | 1,
    expectedSnapshot: PersonalRoughCutSnapshot | null = stateRef.current.snapshot,
    expectedReadGeneration = stateRef.current.readGeneration,
    expectedEditorGeneration = stateRef.current.editorGeneration,
    expectedDraftGeneration = stateRef.current.draftGeneration,
  ) => {
    const current = stateRef.current;
    if (
      !mounted.current
      || !current.editing
      || current.snapshot !== expectedSnapshot
      || current.readGeneration !== expectedReadGeneration
      || current.editorGeneration !== expectedEditorGeneration
      || current.draftGeneration !== expectedDraftGeneration
      || current.saveState.status !== "idle"
      || !latestInputs.current.isScopeCurrent()
      || !editorSourceIsCurrent(current, latestInputs.current, invalidEditorSource.current)
    ) {
      return;
    }
    publish({
      ...current,
      draft: moveRoughCutFrame(current.draft, index, direction),
      draftGeneration: current.draftGeneration + 1,
    });
  }, [publish]);

  const setIncluded = useCallback((
    index: number,
    included: boolean,
    expectedSnapshot: PersonalRoughCutSnapshot | null = stateRef.current.snapshot,
    expectedReadGeneration = stateRef.current.readGeneration,
    expectedEditorGeneration = stateRef.current.editorGeneration,
    expectedDraftGeneration = stateRef.current.draftGeneration,
  ) => {
    const current = stateRef.current;
    if (
      !mounted.current
      || !current.editing
      || current.snapshot !== expectedSnapshot
      || current.readGeneration !== expectedReadGeneration
      || current.editorGeneration !== expectedEditorGeneration
      || current.draftGeneration !== expectedDraftGeneration
      || current.saveState.status !== "idle"
      || !latestInputs.current.isScopeCurrent()
      || !editorSourceIsCurrent(current, latestInputs.current, invalidEditorSource.current)
    ) {
      return;
    }
    publish({
      ...current,
      draft: setRoughCutIncluded(current.draft, index, included),
      draftGeneration: current.draftGeneration + 1,
    });
  }, [publish]);

  const save = useCallback((
    expectedSnapshot: PersonalRoughCutSnapshot | null = stateRef.current.snapshot,
    expectedReadGeneration = stateRef.current.readGeneration,
    expectedEditorGeneration = stateRef.current.editorGeneration,
    expectedDraftGeneration = stateRef.current.draftGeneration,
  ) => {
    const current = stateRef.current;
    const currentInputs = latestInputs.current;
    if (
      !mounted.current
      || !current.editing
      || current.snapshot === null
      || current.snapshot !== expectedSnapshot
      || current.readGeneration !== expectedReadGeneration
      || current.editorGeneration !== expectedEditorGeneration
      || current.draftGeneration !== expectedDraftGeneration
      || current.saveState.status !== "idle"
      || currentInputs.save === undefined
      || !currentInputs.isScopeCurrent()
      || !editorSourceIsCurrent(current, currentInputs, invalidEditorSource.current)
      || !assetSourceReady(currentInputs, current.snapshot?.frames.length !== 0)
    ) {
      return;
    }

    const decision = decideRoughCutSave(current.snapshot, current.draft, currentInputs.sourceCheck);
    if (decision.kind === "blocked") {
      return;
    }
    if (decision.kind === "unchanged") {
      publish({ ...current, editing: false, editorGeneration: current.editorGeneration + 1 });
      return;
    }

    const nextWriteGeneration = writeGeneration.current + 1;
    writeGeneration.current = nextWriteGeneration;
    const controller = new AbortController();
    const ticket: EditorWriteTicket = {
      snapshot: current.snapshot,
      sourceIdentity: current.sourceIdentity ?? currentInputs.sourceIdentity,
      readGeneration: current.readGeneration,
      editorGeneration: current.editorGeneration,
      draftGeneration: current.draftGeneration,
      writeGeneration: nextWriteGeneration,
    };
    const pendingState: EditorState = {
      ...current,
      editing: false,
      editorGeneration: current.editorGeneration + 1,
      saveState: {
        status: "pending",
        ticket,
        submitted: decision.update.frames,
        message: "正在保存粗剪编排…",
      },
    };

    // Publish the synchronous write gate before abort listeners or parent callbacks run.
    const previousController = saveController.current;
    saveController.current = controller;
    publish(pendingState);

    const isCurrent = () => mounted.current
      && !controller.signal.aborted
      && writeGeneration.current === ticket.writeGeneration
      && stateRef.current.saveState.status === "pending"
      && stateRef.current.saveState.ticket === ticket
      && stateRef.current.snapshot === ticket.snapshot
      && stateRef.current.readGeneration === ticket.readGeneration
      && stateRef.current.sourceIdentity !== null
      && sameAssetSource(ticket.sourceIdentity, latestInputs.current.sourceIdentity, ticket.snapshot.frames.length > 0)
      && assetSourceReady(latestInputs.current, ticket.snapshot.frames.length > 0)
      && latestInputs.current.chapterId === currentInputs.chapterId
      && latestInputs.current.save === currentInputs.save
      && latestInputs.current.isScopeCurrent();

    previousController?.abort();
    if (!isCurrent()) {
      return;
    }

    latestInputs.current.onBeforeSave();
    if (!isCurrent()) {
      controller.abort();
      if (saveController.current === controller) {
        saveController.current = null;
      }
      return;
    }

    const saveRequest = currentInputs.save;
    if (saveRequest === undefined) {
      return;
    }
    void saveRequest(currentInputs.chapterId, decision.update, controller.signal).then((value) => {
      if (!isCurrent()) {
        return;
      }
      let canonical: PersonalRoughCutSnapshot;
      try {
        canonical = parsePersonalRoughCutSaveResponse(value, currentInputs.chapterId, decision.update);
      } catch (error) {
        const normalized = normalizeApiError(error);
        publish({
          ...stateRef.current,
          saveState: {
            status: "locked",
            ticket,
            submitted: decision.update.frames,
            message: normalized.message || "保存结果无法核对，请重新读取后再继续。",
          },
        });
        return;
      }

      const next: EditorState = {
        ...stateRef.current,
        snapshot: canonical,
        readGeneration: ticket.readGeneration,
        draft: createRoughCutDraft(canonical),
        draftGeneration: stateRef.current.draftGeneration + 1,
        editing: false,
        editorGeneration: stateRef.current.editorGeneration + 1,
        saveState: { status: "idle" },
      };
      publish(next);
      latestInputs.current.onSaved(canonical, ticket.readGeneration);
    }).catch((cause: unknown) => {
      if (!isCurrent()) {
        return;
      }
      const error: ApiError = normalizeApiError(cause);
      publish({
        ...stateRef.current,
        saveState: {
          status: "locked",
          ticket,
          submitted: decision.update.frames,
          message: error.message || "保存结果不确定，请重新读取后再继续。",
        },
      });
      if (error.status === 401) {
        latestInputs.current.onUnauthorized();
      }
    });
  }, [publish]);

  const current = state;
  const snapshot = current.snapshot;
  const locked = current.saveState.status !== "idle";
  const displayedSnapshot = snapshot === null
    ? null
    : current.saveState.status === "idle"
      ? snapshot
      : submittedSnapshot(snapshot, current.saveState.submitted);
  const decision = snapshot === null
    ? { kind: "blocked" as const, reason: "粗剪草稿尚未读取。" }
    : decideRoughCutSave(snapshot, current.draft, latestInputs.current.sourceCheck);
  const sourceCurrent = editorSourceIsCurrent(current, latestInputs.current, invalidEditorSource.current);
  const sourceUsable = snapshot === null
    ? false
    : assetSourceReady(latestInputs.current, snapshot.frames.length > 0);
  const canEdit = snapshot !== null
    && current.saveState.status === "idle"
    && latestInputs.current.save !== undefined
    && (snapshot.frames.length === 0 || !invalidEditorSource.current)
    && latestInputs.current.isScopeCurrent();
  const canSave = canEdit && sourceCurrent && sourceUsable && current.editing && decision.kind === "save";
  const message = current.saveState.status === "idle"
    ? snapshot !== null && snapshot.frames.length > 0 && invalidEditorSource.current
      ? "素材目录已变化，请重新读取后再编辑完整粗剪。"
      : decision.kind === "blocked" && current.editing ? decision.reason : null
    : current.saveState.message;
  const renderedSourceIdentity = latestInputs.current.sourceIdentity;

  return {
    snapshot,
    readGeneration: current.readGeneration,
    editorGeneration: current.editorGeneration,
    draftGeneration: current.draftGeneration,
    draft: current.draft,
    displayedSnapshot,
    editing: current.editing && sourceCurrent,
    isSaving: current.saveState.status === "pending",
    isLocked: locked,
    message,
    canEdit,
    canSave,
    acceptRead,
    beginRead,
    isCurrentRead,
    isCurrentNavigationRead,
    isCurrentPanelAction,
    openEditor: () => openEditor(
      current.snapshot,
      current.readGeneration,
      current.editorGeneration,
      renderedSourceIdentity,
    ),
    cancelEditor: () => cancelEditor(
      current.snapshot,
      current.readGeneration,
      current.editorGeneration,
      current.draftGeneration,
    ),
    moveFrame: (index, direction) => moveFrame(
      index,
      direction,
      current.snapshot,
      current.readGeneration,
      current.editorGeneration,
      current.draftGeneration,
    ),
    setIncluded: (index, included) => setIncluded(
      index,
      included,
      current.snapshot,
      current.readGeneration,
      current.editorGeneration,
      current.draftGeneration,
    ),
    save: () => save(
      current.snapshot,
      current.readGeneration,
      current.editorGeneration,
      current.draftGeneration,
    ),
  };
}
