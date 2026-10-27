export interface ZhangyugePatStatus {
  configured: boolean;
}

export interface ZhangyugePatServicePort {
  getStatus(signal: AbortSignal): Promise<ZhangyugePatStatus>;
  save(pat: string, signal: AbortSignal): Promise<void>;
}

export type ZhangyugePatHttpCall =
  | { endpoint: "zhangyugePatStatus" }
  | { endpoint: "zhangyugePatSave"; pat: string };

export type ZhangyugePatJsonRequester = (
  call: ZhangyugePatHttpCall,
  signal: AbortSignal,
) => Promise<unknown>;
