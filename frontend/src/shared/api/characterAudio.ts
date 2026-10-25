import type { Character } from "./contracts";

export interface CharacterAudioSource {
  readonly kind: "character-audio-source";
}

export interface CharacterAudioUploadReceipt {
  readonly kind: "character-audio-upload-receipt";
}

export interface CharacterAudioDirectory {
  readonly category: "characters";
  readonly items: Character[];
  readonly audioSources: ReadonlyMap<string, CharacterAudioSource>;
}

export interface CharacterAudioTarget {
  readonly audio?: CharacterAudioUploadReceipt | null;
  readonly voice_ref?: string;
}

export interface CharacterAudioRunContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly characterId: string;
  readonly directoryEpoch: number;
  readonly portEpoch: number;
  readonly character: Character;
  isContextCurrent(): boolean;
}

export interface CharacterAudioRun {
  isCurrent(): boolean;
  canSaveAudio(receipt: CharacterAudioUploadReceipt): boolean;
  canReadAudio(source: CharacterAudioSource | CharacterAudioUploadReceipt): boolean;
  uploadAudio(file: File, signal: AbortSignal): Promise<CharacterAudioUploadReceipt>;
  save(target: CharacterAudioTarget, signal: AbortSignal): Promise<Character>;
  readDirectory(signal: AbortSignal): Promise<CharacterAudioDirectory>;
  readAudio(source: CharacterAudioSource | CharacterAudioUploadReceipt, signal: AbortSignal): Promise<Blob>;
  matchesTarget(character: Character, target: CharacterAudioTarget): boolean;
}

export interface CharacterAudioPort {
  beginRun(context: CharacterAudioRunContext): CharacterAudioRun;
}

export type CharacterAudioFailureKind = "not-sent" | "rejected" | "unknown" | "read-error";

export class CharacterAudioError extends Error {
  readonly kind: CharacterAudioFailureKind;
  readonly status: number | null;

  constructor(kind: CharacterAudioFailureKind, message: string, status: number | null = null) {
    super(message);
    this.name = "CharacterAudioError";
    this.kind = kind;
    this.status = status;
  }
}
