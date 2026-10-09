import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { demoSeries } from "../series/demoSeries";

export interface DemoChapterData {
  chapters: Chapter[];
  assetsByChapter: Record<string, StoryboardAsset[]>;
}

const timestamp = "2026-10-02T08:30:00";

export function getDemoChapterData(seriesId: string): DemoChapterData | null {
  const series = demoSeries.find((candidate) => candidate.id === seriesId);
  if (!series) {
    return null;
  }

  const openingChapterId = series.id + "-chapter-opening";
  const letterChapterId = series.id + "-chapter-letter";
  const openingFrameId = series.id + "-frame-opening";
  const secondFrameId = series.id + "-frame-letter";
  const chapters: Chapter[] = [
    {
      id: openingChapterId,
      series_id: series.id,
      title: "第一章 · 雾起",
      content: [
        {
          text: series.name + "。清晨的雾沿着旧街缓缓散开。",
          original_text: "天色微明，旧街仍被薄雾笼罩。",
          storyboard: [openingFrameId],
          character: [series.id + "-character-linlan"],
          scene: [series.id + "-scene-old-street"],
          prop: [],
          preview: "https://preview.example.invalid/opening",
        },
        {
          text: "门前的信封没有署名，纸角沾着雨水。",
          storyboard: [secondFrameId],
          character: [],
          scene: [],
          prop: [series.id + "-shared-material-id"],
          preview: null,
        },
      ],
      order: 2,
      created_at: timestamp,
      updated_at: timestamp,
      lock: {
        locked: true,
        locked_by_username: "周编剧",
        is_mine: false,
        expires_at: "2026-10-04T10:30:00",
      },
    },
    {
      id: letterChapterId,
      series_id: series.id,
      title: "第二章 · 来信",
      content: [
        {
          text: "她拆开信封，发现里面只有一行熟悉的字。",
          original_text: null,
          storyboard: [series.id + "-frame-letter-second"],
          character: [series.id + "-character-linlan", series.id + "-shared-material-id"],
          scene: [series.id + "-shared-material-id"],
          prop: [series.id + "-shared-material-id", series.id + "-prop-key"],
          preview: "preview-ready",
        },
      ],
      order: 1,
      created_at: timestamp,
      updated_at: timestamp,
      lock: null,
    },
  ];

  const assetsByChapter: Record<string, StoryboardAsset[]> = {
    [openingChapterId]: [
      {
        id: openingFrameId,
        series_id: series.id,
        chapter_id: openingChapterId,
        frame_index: 7,
        name: "街巷远景",
        description: null,
        image_url: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
      {
        id: secondFrameId,
        series_id: series.id,
        chapter_id: openingChapterId,
        frame_index: 3,
        name: "无署名信封",
        description: "信封边缘留有雨痕。",
        image_url: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
    [letterChapterId]: [
      {
        id: series.id + "-frame-letter-second",
        series_id: series.id,
        chapter_id: letterChapterId,
        frame_index: 1,
        name: "来信",
        description: null,
        image_url: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
  };

  return { chapters, assetsByChapter };
}
