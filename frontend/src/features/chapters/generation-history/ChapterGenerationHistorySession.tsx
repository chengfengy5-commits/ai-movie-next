import { ChapterGenerationHistoryDialog } from "./ChapterGenerationHistoryDialog";
import {
  useChapterGenerationHistoryOwner,
  type ChapterGenerationHistoryOwner,
} from "./useChapterGenerationHistoryOwner";

interface ChapterGenerationHistorySessionProps {
  readonly owner: ChapterGenerationHistoryOwner;
  readonly onClose: () => void;
}

export function ChapterGenerationHistorySession({ owner, onClose }: ChapterGenerationHistorySessionProps) {
  const dialog = useChapterGenerationHistoryOwner(owner);
  return <ChapterGenerationHistoryDialog {...dialog} onClose={onClose} />;
}
