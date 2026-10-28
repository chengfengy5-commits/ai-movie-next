import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { WorkspaceServices } from "../../shared/api/services";
import { browserStorage, type StorageLike } from "../../shared/api/storage";
import {
  continuousChapterJournalMatchesScope,
  continuousChapterOperationJournalKey,
  normalizeApiBaseUrl,
  readContinuousChapterJournal,
  type ContinuousChapterOperationEnvelope,
  type ContinuousChapterOperationScope,
} from "./continuousChapterOperationJournal";

export type ContinuousChapterOperationJournalState = {
  readonly identity: string | null;
  readonly status: "checking" | "empty" | "ready" | "error";
  readonly journal: ContinuousChapterOperationEnvelope | null;
  readonly message: string | null;
};

export interface ContinuousChapterOperationJournalBinding {
  readonly actorId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly sessionGeneration: number;
}

export function continuousChapterJournalScope(
  actorId: string,
  apiBaseUrl: string | null,
  seriesId: string,
  chapterId: string,
): ContinuousChapterOperationScope {
  const canonicalApiBaseUrl = apiBaseUrl === null ? null : normalizeApiBaseUrl(apiBaseUrl);
  if (actorId.trim() === "" || canonicalApiBaseUrl === null || seriesId.trim() === "" || chapterId.trim() === "") {
    throw new Error("当前账号、接口或章节身份不完整，无法安全读取连续编辑原操作。");
  }
  return { actorId, apiBaseUrl: canonicalApiBaseUrl, seriesId, chapterId };
}

export function continuousChapterOperationStorage(services: WorkspaceServices): StorageLike {
  try {
    const configured = services.continuousChapterOperationStorage;
    if (configured !== undefined) return configured;
    return browserStorage();
  } catch {
    throw new Error("本地原操作存储不可用；本章写入保持锁定。");
  }
}

export function continuousChapterOperationJournalIdentity(
  binding: ContinuousChapterOperationJournalBinding,
): string {
  const apiBaseUrl = normalizeApiBaseUrl(binding.apiBaseUrl);
  return [binding.actorId, apiBaseUrl ?? "<invalid-api>", binding.seriesId, binding.chapterId, binding.sessionGeneration].join("\u0000");
}

export function useContinuousChapterOperationJournal(
  binding: ContinuousChapterOperationJournalBinding | null,
  services: WorkspaceServices,
): {
  readonly state: ContinuousChapterOperationJournalState;
  readonly setState: Dispatch<SetStateAction<ContinuousChapterOperationJournalState>>;
  readonly reload: () => void;
} {
  const identity = binding === null ? null : continuousChapterOperationJournalIdentity(binding);
  const [reloadEpoch, setReloadEpoch] = useState(0);
  const [state, setState] = useState<ContinuousChapterOperationJournalState>({
    identity: null,
    status: "empty",
    journal: null,
    message: null,
  });
  const requestGeneration = useRef(0);

  useEffect(() => {
    const requestId = ++requestGeneration.current;
    let active = true;
    if (binding === null || identity === null) {
      setState({ identity: null, status: "empty", journal: null, message: null });
      return () => { active = false; };
    }

    setState({ identity, status: "checking", journal: null, message: null });
    void (async () => {
      try {
        const scope = continuousChapterJournalScope(
          binding.actorId,
          binding.apiBaseUrl,
          binding.seriesId,
          binding.chapterId,
        );
        const storage = continuousChapterOperationStorage(services);
        const key = continuousChapterOperationJournalKey(scope);
        const journal = await readContinuousChapterJournal(storage, key);
        if (journal !== null && !continuousChapterJournalMatchesScope(journal, scope)) {
          throw new Error("本地原操作记录与当前账号、接口或章节不匹配；记录已保留。");
        }
        if (!active || requestId !== requestGeneration.current) return;
        setState({
          identity,
          status: journal === null ? "empty" : "ready",
          journal,
          message: journal === null ? null : "发现本章尚未完成审阅的原操作记录。",
        });
      } catch (cause) {
        if (!active || requestId !== requestGeneration.current) return;
        setState({
          identity,
          status: "error",
          journal: null,
          message: cause instanceof Error ? cause.message : "本地原操作记录无法安全读取；本章写入保持锁定。",
        });
      }
    })();

    return () => { active = false; };
  }, [binding?.actorId, binding?.apiBaseUrl, binding?.seriesId, binding?.chapterId, binding?.sessionGeneration, identity, reloadEpoch, services]);

  const reload = useCallback(() => setReloadEpoch((current) => current + 1), []);
  return { state, setState, reload };
}
