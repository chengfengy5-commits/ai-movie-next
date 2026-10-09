import { createHash } from "node:crypto";
import { createServer } from "node:http";

const host = "127.0.0.1";
const port = Number(process.env.FIXTURE_PORT ?? "4175");
const allowedOrigins = new Set([
  "http://localhost:5173",
  "http://127.0.0.1:5173",
  "http://localhost:5174",
  "http://127.0.0.1:5174",
]);
const validStatuses = new Set([401, 403, 404, 422, 500]);
const demoUsername = "demo";
const demoPassword = "demo123";
const demoToken = "fixture-demo-token";

const user = {
  id: "demo-user",
  username: "演示创作者",
  email: "demo@example.invalid",
  is_superuser: false,
  membership_type: "demo",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00",
};

const secondUser = {
  ...user,
  id: "demo-user-02",
  username: "第二演示创作者",
  email: "demo-two@example.invalid",
};

const fixtureAccounts = [
  { username: demoUsername, password: demoPassword, token: demoToken, user },
  {
    username: "demo-two",
    password: "demo-two123",
    token: "fixture-demo-token-02",
    user: secondUser,
  },
];

const teamsByUser = new Map([
  [user.id, [
    {
      id: "fixture-team",
      name: "本地协作组",
      owner_id: user.id,
      created_at: "2026-09-30T08:00:00Z",
      member_count: 12,
      my_role: "owner",
    },
    {
      id: "fixture-empty-team",
      name: "暂无剧集团队",
      owner_id: "writer-02",
      created_at: "2026-09-29T10:30:00",
      member_count: 0,
      my_role: "member",
    },
    {
      id: "all",
      name: "同名团队",
      owner_id: "writer-03",
      created_at: "2026-09-28T08:00:00+08:00",
      member_count: 2,
      my_role: "observer",
    },
    {
      id: "fixture-team-alt",
      name: "同名团队",
      owner_id: "writer-04",
      created_at: "2026-09-27T08:00:00Z",
      member_count: 1,
      my_role: "legacy_reviewer",
    },
    {
      id: "__proto__",
      name: "",
      owner_id: "writer-05",
      created_at: "2026-09-26T08:00:00Z",
      member_count: 0,
      my_role: "future-role",
    },
  ]],
  [secondUser.id, [
    {
      id: "fixture-team-02",
      name: "第二账号协作组",
      owner_id: secondUser.id,
      created_at: "2026-09-25T08:00:00Z",
      member_count: 3,
      my_role: "owner",
    },
    {
      id: "fixture-team-02-empty",
      name: "暂无剧集团队",
      owner_id: "writer-06",
      created_at: "2026-09-24T08:00:00Z",
      member_count: 0,
      my_role: "member",
    },
  ]],
]);

const series = Array.from({ length: 24 }, (_, index) => {
  const number = index + 1;
  const owned = number <= 12;
  const shared = !owned || number % 3 === 0;
  const claimedByDemo = shared && number % 4 === 0;
  const claimedByOther = shared && number % 5 === 0 && !claimedByDemo;

  return {
    id: `fixture-series-${String(number).padStart(2, "0")}`,
    user_id: owned ? user.id : "writer-02",
    name: `隔离样例剧集 ${String(number).padStart(2, "0")}`,
    description: number % 2 === 0 ? "用于本地接口验收的样例内容。" : null,
    image_url: null,
    style_prompt_id: null,
    style_prompt: null,
    style_prompt_name: null,
    style_prompt_owner_name: null,
    team_id: shared ? "fixture-team" : null,
    team_name: shared ? "本地协作组" : null,
    owner_name: owned ? user.username : "林编剧",
    claimed_by: claimedByDemo ? user.id : claimedByOther ? "writer-03" : null,
    claimed_by_username: claimedByDemo ? user.username : claimedByOther ? "周编剧" : null,
    claimed_by_avatar_url: null,
    can_enter: !claimedByOther,
    created_at: `2026-09-${String(30 - (index % 20)).padStart(2, "0")}T08:00:00`,
    updated_at: `2026-10-0${1 + (index % 3)}T10:30:00`,
  };
});

function lockSnapshot({ locked, username = null, isMine = false }) {
  if (!locked) {
    return null;
  }
  return {
    locked: true,
    locked_by_username: username,
    is_mine: isMine,
    expires_at: "2026-10-04T12:30:00",
  };
}

function createChapter(seriesId, number, details) {
  return {
    id: `${seriesId}-chapter-${String(number).padStart(2, "0")}`,
    series_id: seriesId,
    title: details.title,
    content: details.content,
    order: details.order,
    created_at: `2026-10-0${number}T08:00:00`,
    updated_at: `2026-10-0${number}T09:00:00`,
    lock: details.lock,
  };
}

function createFrame(seriesId, assetId, text, originalText) {
  return {
    text,
    original_text: originalText,
    storyboard: [assetId],
    character: [`${seriesId}-shared-asset`],
    scene: [`${seriesId}-shared-asset`],
    prop: [],
    preview: "https://preview.example.invalid/already-generated.mp4",
  };
}

function createAsset(seriesId, chapterId, id, frameIndex, name) {
  return {
    id,
    series_id: seriesId,
    chapter_id: chapterId,
    frame_index: frameIndex,
    name,
    description: `本地合成原图 ${name}`,
    image_url: `/media/${id}.png`,
    created_at: "2026-10-01T08:00:00",
    updated_at: "2026-10-01T09:00:00",
  };
}

const chaptersBySeries = new Map();
const assetsBySeriesAndChapter = new Map();
for (const item of series) {
  const firstChapterId = `${item.id}-chapter-02`;
  const secondChapterId = `${item.id}-chapter-01`;
  const thirdChapterId = `${item.id}-chapter-03`;
  const fourthChapterId = `${item.id}-chapter-04`;
  const firstAssetA = `${item.id}-chapter-02-shot-a`;
  const firstAssetB = `${item.id}-chapter-02-shot-b`;
  const secondAssetA = `${item.id}-chapter-01-shot-a`;
  const editableLock = lockSnapshot({ locked: true, username: user.username, isMine: true });
  const otherLock = lockSnapshot({ locked: true, username: "周编剧", isMine: false });

  const chapters = [
    createChapter(item.id, 2, {
      title: "第二章：雨夜证词",
      order: 2,
      lock: editableLock,
      // The frame order intentionally differs from asset frame_index order.
      content: [
        createFrame(item.id, firstAssetB, "门外的脚步停在雨声里。", "脚步声在雨中停下。"),
        createFrame(item.id, firstAssetA, "林岚抬头，看见走廊尽头亮起一盏灯。", "走廊尽头的灯亮了。"),
      ],
    }),
    createChapter(item.id, 1, {
      title: "第一章：失踪的底稿",
      order: 1,
      lock: otherLock,
      content: [
        createFrame(item.id, secondAssetA, "桌上只剩下一页被撕开的底稿。", "桌上有一页残稿。"),
      ],
    }),
    createChapter(item.id, 3, {
      title: "第三章：尚未拆分",
      order: 3,
      lock: null,
      content: null,
    }),
    createChapter(item.id, 4, {
      title: "第四章：空白页",
      order: 4,
      lock: null,
      content: [],
    }),
  ];

  const firstAssets = [
    createAsset(item.id, firstChapterId, firstAssetA, 1, "走廊灯光"),
    createAsset(item.id, firstChapterId, firstAssetB, 2, "雨夜门口"),
  ];
  const secondAssets = [
    createAsset(item.id, secondChapterId, secondAssetA, 1, "残缺底稿"),
  ];
  if (process.env.FIXTURE_PERSONAL_NOTES_MODE === "double-empty") {
    const secondChapter = chapters.find((chapter) => chapter.id === secondChapterId);
    const firstFrame = secondChapter?.content?.[0];
    const firstAsset = secondAssets[0];
    if (firstFrame) {
      firstFrame.preview = null;
    }
    if (firstAsset) {
      firstAsset.image_url = null;
    }
  }
  chaptersBySeries.set(item.id, chapters);
  assetsBySeriesAndChapter.set(`${item.id}:${firstChapterId}`, firstAssets);
  assetsBySeriesAndChapter.set(`${item.id}:${secondChapterId}`, secondAssets);
}

function collectionImage(type, id) {
  return `/media/${type}-${id}.png`;
}

function createAssetCollections(seriesId) {
  const sharedId = `${seriesId}-shared-asset`;
  const emptyCharacterId = `${seriesId}-z-empty-character`;
  const emptySceneId = `${seriesId}-z-empty-scene`;
  const emptyPropId = `${seriesId}-z-empty-prop`;

  const characters = [
    {
      id: emptyCharacterId,
      series_id: seriesId,
      name: "",
      gender: null,
      age: null,
      role: null,
      appearance: null,
      description: null,
      image_url: null,
      audio_url: null,
      voice_ref: null,
      aliases: null,
      canonical_key: null,
      created_at: "2026-10-03T10:00:00",
      updated_at: "2026-10-03T10:00:00",
    },
    {
      id: sharedId,
      series_id: seriesId,
      name: "沈照",
      gender: "女",
      age: "28岁",
      role: "调查记者",
      appearance: "利落短发，常穿深色风衣。",
      description: "追查一封旧信来源的记者。",
      image_url: collectionImage("characters", sharedId),
      audio_url: "https://audio.example.invalid/fixture-character.mp3",
      voice_ref: "fixture-voice-reference",
      aliases: ["阿照", "照姐"],
      canonical_key: "shen-zhao",
      created_at: "2026-10-01T08:00:00",
      updated_at: "2026-10-02T08:00:00",
    },
    {
      id: `${seriesId}-character-02`,
      series_id: seriesId,
      name: "林序",
      gender: "男",
      age: null,
      role: "旧书店店主",
      appearance: null,
      description: "熟悉港区旧档案。",
      image_url: null,
      audio_url: null,
      voice_ref: null,
      created_at: "2026-10-02T09:00:00",
      updated_at: "2026-10-03T09:00:00",
    },
  ];
  const scenes = [
    {
      id: emptySceneId,
      series_id: seriesId,
      title: "",
      description: null,
      image_url: null,
      aliases: [],
      canonical_key: null,
      created_at: "2026-10-03T10:00:00",
      updated_at: "2026-10-03T10:00:00",
    },
    {
      id: sharedId,
      series_id: seriesId,
      title: "盐仓码头",
      description: "旧仓库旁的潮湿码头。",
      image_url: collectionImage("scenes", sharedId),
      aliases: ["老盐仓外", "仓库码头"],
      canonical_key: "盐仓|夜",
      created_at: "2026-10-01T08:00:00",
      updated_at: "2026-10-02T08:00:00",
    },
    {
      id: `${seriesId}-scene-02`,
      series_id: seriesId,
      title: "潮汐巷",
      description: null,
      image_url: null,
      created_at: "2026-10-02T09:00:00",
      updated_at: "2026-10-03T09:00:00",
    },
  ];
  const props = [
    {
      id: emptyPropId,
      series_id: seriesId,
      name: "",
      description: null,
      image_url: null,
      aliases: null,
      canonical_key: null,
      created_at: "2026-10-03T10:00:00",
      updated_at: "2026-10-03T10:00:00",
    },
    {
      id: sharedId,
      series_id: seriesId,
      name: "旧铜钥匙",
      description: "齿口磨损，柄上刻有一个数字。",
      image_url: collectionImage("props", sharedId),
      aliases: ["黄铜钥匙"],
      canonical_key: "old-brass-key",
      created_at: "2026-10-01T08:00:00",
      updated_at: "2026-10-02T08:00:00",
    },
    {
      id: `${seriesId}-prop-02`,
      series_id: seriesId,
      name: "蓝布行李箱",
      description: null,
      image_url: null,
      created_at: "2026-10-02T09:00:00",
      updated_at: "2026-10-03T09:00:00",
    },
  ];

  return { characters, scenes, props };
}

const assetCollectionsBySeries = new Map(
  series.map((item) => [item.id, createAssetCollections(item.id)]),
);

function sha256(value) {
  if (typeof value !== "string" || value.length === 0) {
    return null;
  }
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function createWireFrameNotes(entries) {
  return Object.fromEntries(entries);
}

function exactOwnKeys(value, expected) {
  const actual = Reflect.ownKeys(value);
  return actual.length === expected.length
    && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
    && actual.every((key) => typeof key === "string" && expected.includes(key));
}

function parsePersonalRoughCutUpdate(value) {
  if (
    typeof value !== "object"
    || value === null
    || Array.isArray(value)
    || !exactOwnKeys(value, ["expected_revision", "frames"])
    || !Number.isSafeInteger(value.expected_revision)
    || value.expected_revision < 0
    || value.expected_revision === Number.MAX_SAFE_INTEGER
    || !Array.isArray(value.frames)
    || value.frames.length > 500
    || Reflect.ownKeys(value.frames).length !== value.frames.length + 1
  ) {
    return null;
  }

  const seenAssetIds = new Set();
  const frames = [];
  for (let index = 0; index < value.frames.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value.frames, String(index))) {
      return null;
    }
    const frame = value.frames[index];
    if (
      typeof frame !== "object"
      || frame === null
      || Array.isArray(frame)
      || !exactOwnKeys(frame, ["asset_id", "included"])
      || typeof frame.asset_id !== "string"
      || Array.from(frame.asset_id).length < 1
      || Array.from(frame.asset_id).length > 36
      || typeof frame.included !== "boolean"
      || seenAssetIds.has(frame.asset_id)
    ) {
      return null;
    }
    seenAssetIds.add(frame.asset_id);
    frames.push({ asset_id: frame.asset_id, included: frame.included });
  }

  return { expected_revision: value.expected_revision, frames };
}

function parsePersonalNoteUpdate(value) {
  if (
    typeof value !== "object"
    || value === null
    || Array.isArray(value)
    || !exactOwnKeys(value, ["expected_revision", "frames"])
  ) {
    return null;
  }
  if (
    !Number.isSafeInteger(value.expected_revision)
    || value.expected_revision < 0
    || !Array.isArray(value.frames)
    || value.frames.length !== 1
    || Reflect.ownKeys(value.frames).length !== 2
    || !Object.prototype.hasOwnProperty.call(value.frames, "0")
  ) {
    return null;
  }

  const frame = value.frames[0];
  const statuses = new Set(["unmarked", "needs_revision", "approved"]);
  if (
    typeof frame !== "object"
    || frame === null
    || Array.isArray(frame)
    || !exactOwnKeys(frame, [
      "storyboard_asset_id",
      "expected_media_revision",
      "status",
      "note",
    ])
    || typeof frame.storyboard_asset_id !== "string"
    || frame.storyboard_asset_id.length === 0
    || Array.from(frame.storyboard_asset_id).length > 36
    || !Number.isSafeInteger(frame.expected_media_revision)
    || frame.expected_media_revision < 1
    || !statuses.has(frame.status)
    || typeof frame.note !== "string"
    || Array.from(frame.note).length > 2_000
  ) {
    return null;
  }

  return {
    expected_revision: value.expected_revision,
    frames: [{
      storyboard_asset_id: frame.storyboard_asset_id,
      expected_media_revision: frame.expected_media_revision,
      status: frame.status,
      note: frame.note,
    }],
  };
}

function parsePersonalResumeUpdate(value) {
  if (
    typeof value !== "object"
    || value === null
    || Array.isArray(value)
    || !exactOwnKeys(value, ["expected_revision", "resume_frame_id"])
  ) {
    return null;
  }
  if (
    !Number.isSafeInteger(value.expected_revision)
    || value.expected_revision < 0
    || value.expected_revision === Number.MAX_SAFE_INTEGER
    || (value.resume_frame_id !== null
      && (
        typeof value.resume_frame_id !== "string"
        || value.resume_frame_id.trim() === ""
        || Array.from(value.resume_frame_id).length > 36
      ))
  ) {
    return null;
  }

  return {
    expected_revision: value.expected_revision,
    resume_frame_id: value.resume_frame_id,
  };
}

function hasValidResumeTarget(chapter, selectedSeries, snapshot, resumeFrameId) {
  if (resumeFrameId === null) {
    return true;
  }
  if (
    chapter.series_id !== selectedSeries.id
    || snapshot.chapter_id !== chapter.id
    || !Array.isArray(chapter.content)
    || !Array.isArray(snapshot.frames)
  ) {
    return false;
  }

  const matchingContent = [];
  for (let index = 0; index < chapter.content.length; index += 1) {
    const references = chapter.content[index]?.storyboard;
    if (Array.isArray(references) && references[0] === resumeFrameId) {
      matchingContent.push(index);
    }
  }
  const assets = assetsBySeriesAndChapter.get(`${selectedSeries.id}:${chapter.id}`) ?? [];
  const matchingAssets = assets.filter((asset) => asset.id === resumeFrameId);
  const matchingFrames = snapshot.frames.filter((frame) => (
    frame.storyboard_asset_id === resumeFrameId
  ));
  if (matchingContent.length !== 1 || matchingAssets.length !== 1 || matchingFrames.length !== 1) {
    return false;
  }

  const contentIndex = matchingContent[0];
  const asset = matchingAssets[0];
  const frame = matchingFrames[0];
  return Number.isInteger(contentIndex)
    && asset?.series_id === selectedSeries.id
    && asset.chapter_id === chapter.id
    && frame?.frame_index === contentIndex
    && frame.source_valid === true;
}

function captureNotesFrames(chapter, revisions) {
  if (!Array.isArray(chapter.content)) {
    return [];
  }
  const assets = assetsBySeriesAndChapter.get(`${chapter.series_id}:${chapter.id}`) ?? [];
  return chapter.content.map((content, frameIndex) => {
    const assetId = Array.isArray(content.storyboard) && typeof content.storyboard[0] === "string"
      ? content.storyboard[0]
      : null;
    const asset = assetId === null ? undefined : assets.find((candidate) => candidate.id === assetId);
    const imageUrl = typeof asset?.image_url === "string" ? asset.image_url : null;
    const previewUrl = typeof content.preview === "string" ? content.preview : null;
    return {
      frame_index: frameIndex,
      storyboard_asset_id: assetId,
      media_revision: revisions[frameIndex] ?? null,
      source_valid: asset !== undefined,
      asset_image_digest: sha256(imageUrl),
      preview_digest: sha256(previewUrl),
      invalid_reason: asset === undefined ? "当前章节没有唯一匹配的原图资产。" : null,
    };
  });
}

function createPersonalNotesSnapshot(chapter, account) {
  if (chapter.id.endsWith("-chapter-04")) {
    return {
      chapter_id: chapter.id,
      revision: 0,
      media_state: "empty",
      frames: [],
      frame_notes: {},
      resume_frame_id: null,
    };
  }

  if (chapter.id.endsWith("-chapter-03")) {
    return {
      chapter_id: chapter.id,
      revision: account.user.id === secondUser.id ? 8 : 2,
      media_state: "unreadable",
      frames: [],
      frame_notes: createWireFrameNotes([
        [`${chapter.id}-retired-shot`, {
          status: "needs_revision",
          note: account.user.id === secondUser.id
            ? "第二账号的旧分镜记录仅作历史参考。"
            : "旧分镜记录仅作历史参考。",
          approved_media_revision: null,
          needs_reconfirmation: false,
        }],
      ]),
      resume_frame_id: `${chapter.id}-retired-shot`,
    };
  }

  const isSecondUser = account.user.id === secondUser.id;
  const isChapterOne = chapter.id.endsWith("-chapter-01");
  const isDoubleEmptyExample = isChapterOne && process.env.FIXTURE_PERSONAL_NOTES_MODE === "double-empty";
  const frameRevisions = isChapterOne ? [5] : [3, 4];
  const frames = captureNotesFrames(chapter, frameRevisions);
  const firstFrameId = frames[0]?.storyboard_asset_id ?? null;
  const secondFrameId = frames[1]?.storyboard_asset_id ?? null;

  const noteEntries = isDoubleEmptyExample
    ? [[firstFrameId, {
      status: "approved",
      note: isSecondUser
        ? "第二账号的双空旧认可需要重新确认。"
        : "原图与预览都为空时，旧认可需要重新确认。",
      approved_media_revision: 5,
      needs_reconfirmation: true,
    }]]
    : isChapterOne
      ? [[firstFrameId, {
        status: "approved",
        note: isSecondUser
          ? "第二账号的第一章旧认可已过期。"
          : "第一章旧认可已过期，需要重新确认。",
        approved_media_revision: 4,
        needs_reconfirmation: true,
      }]]
    : isSecondUser
      ? [
        [firstFrameId, {
          status: "needs_revision",
          note: "仅属于第二演示账号的镜头备注。",
          approved_media_revision: null,
          needs_reconfirmation: false,
        }],
        [secondFrameId, {
          status: "approved",
          note: "第二账号自己的已核对记录。",
          approved_media_revision: 4,
          needs_reconfirmation: false,
        }],
      ]
      : [
        [firstFrameId, {
          status: "approved",
          note: "已核对该镜头的构图与对白衔接。",
          approved_media_revision: 3,
          needs_reconfirmation: false,
        }],
        [secondFrameId, {
          status: "approved",
          note: "素材更新后仍保留备注，但旧认可已过期。",
          approved_media_revision: 2,
          needs_reconfirmation: false,
        }],
        [`${chapter.id}-orphan-shot`, {
          status: "needs_revision",
          note: "<em>这条旧记录不再对应当前分镜。</em>",
          approved_media_revision: null,
          needs_reconfirmation: false,
        }],
        ["__proto__", {
          status: "unmarked",
          note: "安全保留为独立的字典键。",
          approved_media_revision: null,
          needs_reconfirmation: false,
        }],
      ];

  const revision = isSecondUser
    ? (isChapterOne ? 9 : 11)
    : (isChapterOne ? 4 : 7);

  return {
    chapter_id: chapter.id,
    revision,
    media_state: "ready",
    frames,
    frame_notes: createWireFrameNotes(noteEntries.filter(([id]) => typeof id === "string")),
    resume_frame_id: isSecondUser ? secondFrameId : firstFrameId,
  };
}

const personalNotesByChapterAndUser = new Map();
const personalRoughCutsByChapterAndUser = new Map();
for (const item of series) {
  for (const chapter of chaptersBySeries.get(item.id) ?? []) {
    for (const account of fixtureAccounts) {
      personalNotesByChapterAndUser.set(
        `${account.user.id}:${chapter.id}`,
        createPersonalNotesSnapshot(chapter, account),
      );
    }
  }
}

if (process.env.FIXTURE_PERSONAL_NOTES_SAVE_INITIAL === "unsaved") {
  const initialChapterId = "fixture-series-01-chapter-02";
  const initialKey = `${user.id}:${initialChapterId}`;
  const seededSnapshot = personalNotesByChapterAndUser.get(initialKey);
  if (seededSnapshot?.media_state === "ready" && seededSnapshot.frames.length > 0) {
    const unsavedSnapshot = JSON.parse(JSON.stringify(seededSnapshot));
    unsavedSnapshot.revision = 0;
    unsavedSnapshot.frame_notes = {};
    unsavedSnapshot.resume_frame_id = null;
    personalNotesByChapterAndUser.set(initialKey, unsavedSnapshot);
  }
}

const taskResponseOrder = [7, 3, 10, 1, 9, 2, 8, 4, 6, 5, 18, 12, 20, 11, 17, 13, 19, 14, 16, 15, 21, 22, 23];
const taskTypes = [
  "chat",
  "image",
  "image-single",
  "batch-image",
  "video",
  "video-single",
  "extract",
  "storyboard",
  "optimize-frame",
  "batch-optimize",
  "ai-review",
  "fused",
];

function createTaskRecord(account, taskNumber, responseIndex) {
  const taskId = `${account.user.id}-task-${String(taskNumber).padStart(2, "0")}`;
  const createdMinute = 59 - responseIndex;
  const record = {
    id: taskId,
    type: taskTypes[responseIndex % taskTypes.length],
    message_id: `${taskId}-message`,
    status: "queued",
    result: null,
    request_data: null,
    credit_cost: taskNumber,
    progress: taskNumber % 10,
    progress_message: `本地fixture任务 ${taskNumber} 的读取时说明。`,
    created_at: `2026-10-05T11:${String(createdMinute).padStart(2, "0")}:00`,
    updated_at: null,
    asset_type: taskNumber % 2 === 0 ? "image" : null,
    asset_id: taskNumber % 2 === 0 ? `${taskId}-asset` : null,
    asset_name: taskNumber % 2 === 0 ? `任务关联素材 ${taskNumber}` : null,
    chapter_title: null,
    chapter_id: null,
    frame_index: null,
    frame_count: null,
    frame_text: null,
  };

  if (taskNumber === 7) {
    record.status = "completed";
    record.result = "";
    record.progress = 100;
    record.progress_message = "任务已完成，但旧列表没有结果正文。";
    record.credit_cost = -2;
  } else if (taskNumber === 3) {
    record.status = "legacy_waiting_for_review";
    record.progress = -4;
    record.credit_cost = -9;
    record.progress_message = "未知状态保持原文，不推断为失败。";
  } else if (taskNumber === 10) {
    record.status = "";
    record.progress = 101;
    record.credit_cost = -1;
    record.progress_message = "<strong>历史HTML</strong> https://example.invalid/task-note?source=fixture 中文🙂";
    record.request_data = '{"prompt":"截断的请求片段🙂';
  } else if (taskNumber === 1) {
    record.status = "failed";
    record.result = "<b>失败记录文本</b> https://example.invalid/result 中文🙂";
    record.credit_cost = 0;
    record.request_data = '{"prompt":"截断的旧request_data';
  } else if (taskNumber === 9) {
    record.status = "processing";
    record.progress = -7;
    record.credit_cost = 4;
    record.updated_at = "2026-10-05T03:59:00Z";
  } else if (taskNumber === 2) {
    record.status = "completed";
    record.result = null;
    record.progress = 100;
    record.updated_at = "2026-10-05T12:00:00+08:00";
    record.created_at = null;
  }

  if (taskNumber === 5) {
    record.type = "batch-optimize";
    record.request_data = JSON.stringify({ chapter_id: true, frame_count: ["unexpected", 9] });
    record.chapter_id = true;
    record.frame_count = ["unexpected", 9];
  } else if (taskNumber === 12) {
    record.type = "batch-optimize";
    record.request_data = JSON.stringify({
      chapter_id: { legacy_chapter: "chapter-twelve" },
      frame_count: 4,
      prompt_id: { prompt: "unreached" },
    });
    record.chapter_id = { legacy_chapter: "chapter-twelve" };
  } else if (taskNumber === 18) {
    record.type = "ai-review";
    record.request_data = JSON.stringify({
      chapter_id: 42,
      frame_index: 0,
      prompt_id: { prompt: "ai-review-dynamic-value" },
    });
    record.chapter_id = 42;
    record.frame_index = Number(0) + 1;
    record.prompt_id = { prompt: "ai-review-dynamic-value" };
  } else if (taskNumber === 21) {
    record.type = "fused";
  }

  return record;
}

const tasksByUser = new Map(
  fixtureAccounts.map((account) => [
    account.user.id,
    taskResponseOrder.map((taskNumber, index) => createTaskRecord(account, taskNumber, index)),
  ]),
);

function setCors(request, response) {
  const origin = request.headers.origin;
  if (typeof origin === "string" && allowedOrigins.has(origin)) {
    response.setHeader("Access-Control-Allow-Origin", origin);
    response.setHeader("Vary", "Origin");
    response.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS");
    response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
    response.setHeader("Access-Control-Max-Age", "300");
  }
}

function safeRequestTarget(request) {
  const url = new URL(request.url ?? "/", "http://127.0.0.1");
  if (url.pathname === "/api/chat/tasks/list") {
    const pages = url.searchParams.getAll("page");
    const pageSizes = url.searchParams.getAll("page_size");
    if (
      Array.from(url.searchParams.keys()).length === 2
      && pages.length === 1
      && /^[1-9]\d*$/.test(pages[0])
      && Number.isSafeInteger(Number(pages[0]))
      && pageSizes.length === 1
      && pageSizes[0] === "10"
    ) {
      return `${url.pathname}?page=${pages[0]}&page_size=10`;
    }
    return url.pathname;
  }
  if (/^\/api\/chapters\/[^/]+\/personal-production-notes$/.test(url.pathname)) {
    return url.pathname;
  }
  if (/^\/api\/series\/[^/]+\/storyboard-assets$/.test(url.pathname)) {
    const chapterId = url.searchParams.get("chapter_id");
    if (chapterId !== null) {
      return `${url.pathname}?chapter_id=${encodeURIComponent(chapterId)}`;
    }
  }
  return url.pathname;
}

function logRequest(method, pathname, status) {
  process.stdout.write(`${JSON.stringify({ method, pathname, status })}\n`);
}

function sendJson(request, response, status, payload) {
  setCors(request, response);
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  logRequest(request.method ?? "UNKNOWN", safeRequestTarget(request), status);
  response.end(JSON.stringify(payload));
}

function statusOverride(name) {
  const value = Number(process.env[name] ?? "0");
  return validStatuses.has(value) ? value : null;
}

function detailFor(endpoint) {
  if (endpoint === "series" && process.env.FIXTURE_SERIES_DETAIL === "membership") {
    return "当前演示账号的会员资格暂不可用。";
  }
  if (endpoint === "series" && process.env.FIXTURE_SERIES_DETAIL === "forbidden") {
    return "当前演示账号没有访问此列表的权限。";
  }
  if (endpoint === "chapters") {
    return "当前演示账号无法读取该剧集的章节。";
  }
  if (endpoint === "assets") {
    return "当前演示账号无法读取该章节的原图。";
  }
  if (endpoint === "personal_notes") {
    return "当前账号无法读取该章节的个人制作记录。";
  }
  if (endpoint === "personal_notes_save") {
    return "当前账号无法保存该章节的个人制作记录。";
  }
  if (endpoint === "tasks") {
    return "当前账号无法读取本人的任务列表。";
  }
  if (endpoint === "teams") {
    return "当前账号无法读取团队目录。";
  }
  if (endpoint === "rough_cut" && process.env.FIXTURE_ROUGH_CUT_FORBIDDEN === "membership") {
    return "当前账号的会员资格暂不可用。";
  }
  if (endpoint === "rough_cut" && process.env.FIXTURE_ROUGH_CUT_FORBIDDEN === "ordinary") {
    return "当前账号没有权限读取该章节的个人粗剪草稿。";
  }
  if (endpoint === "rough_cut_save") {
    return "当前账号无法保存该章节的个人粗剪草稿。";
  }
  if (endpoint === "rough_cut") {
    return "当前账号无法读取该章节的个人粗剪草稿。";
  }
  return "请求暂时无法处理，请稍后重试。";
}

function sendControlledError(request, response, endpoint, status) {
  sendJson(request, response, status, { detail: detailFor(endpoint) });
}

function fixtureMode(endpoint) {
  const prefix = `FIXTURE_${endpoint.toUpperCase()}`;
  const mode = process.env[`${prefix}_MODE`];
  const status = process.env[`${prefix}_STATUS`];
  return mode ?? (status === "timeout" || status === "body-timeout" ? status : null);
}

function waitForDelay(endpoint) {
  const key = `FIXTURE_${endpoint.toUpperCase()}_DELAY_MS`;
  const delay = Math.min(30_000, Math.max(0, Number(process.env[key] ?? "0") || 0));
  if (fixtureMode(endpoint) === "timeout") {
    return new Promise((resolve) => setTimeout(resolve, Math.max(delay, 20_000)));
  }
  return delay > 0 ? new Promise((resolve) => setTimeout(resolve, delay)) : Promise.resolve();
}

function authenticatedUser(request) {
  const authorization = request.headers.authorization;
  if (typeof authorization !== "string" || !authorization.startsWith("Bearer ")) {
    return null;
  }
  const token = authorization.slice("Bearer ".length);
  return fixtureAccounts.find((account) => account.token === token)?.user ?? null;
}

function hasValidBearer(request) {
  return authenticatedUser(request) !== null;
}

async function readJsonBody(request, maxBytes = 16_384) {
  const chunks = [];
  let length = 0;
  for await (const chunk of request) {
    length += chunk.length;
    if (length > maxBytes) {
      throw new Error("Request body too large.");
    }
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sendInvalidJson(request, response) {
  setCors(request, response);
  response.statusCode = 200;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  logRequest(request.method ?? "UNKNOWN", safeRequestTarget(request), 200);
  response.end("{ invalid json");
}

function sendBodyTimeout(request, response) {
  setCors(request, response);
  response.statusCode = 200;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  logRequest(request.method ?? "UNKNOWN", safeRequestTarget(request), 200);
  response.write("[");
  response.flushHeaders();
  return new Promise((resolve) => setTimeout(resolve, 20_000));
}

function decodeSeriesId(pathname, suffix) {
  const match = pathname.match(new RegExp(`^/api/series/([^/]+)/${suffix}$`));
  if (!match?.[1]) {
    return null;
  }
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

function decodePersonalNotesChapterId(pathname) {
  const match = pathname.match(/^\/api\/chapters\/([^/]+)\/personal-production-notes$/);
  if (!match?.[1]) {
    return null;
  }
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

function decodePersonalRoughCutChapterId(pathname) {
  const match = pathname.match(/^\/api\/chapters\/([^/]+)\/rough-cut$/);
  if (!match?.[1]) {
    return null;
  }
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

function seriesForChapter(chapterId) {
  return series.find((item) => (
    chaptersBySeries.get(item.id)?.some((chapter) => chapter.id === chapterId) ?? false
  ));
}

const roughCutVideoUrlPattern = /\.(mp4|webm|mov|avi|mkv|m4v)(?:\?|$)/i;
const roughCutLegacyIdReason = "缺少唯一有效的稳定分镜 ID，暂不可编排";
const roughCutMissingVideoReason = "当前分镜没有可用视频";

function roughCutSourceFrames(chapter) {
  if (!Array.isArray(chapter.content)) {
    return [];
  }
  const assets = assetsBySeriesAndChapter.get(`${chapter.series_id}:${chapter.id}`) ?? [];
  const assetsById = new Map(assets.map((asset) => [asset.id, asset]));
  const candidateIds = chapter.content.map((content) => {
    const references = Array.isArray(content.storyboard) ? content.storyboard : null;
    const candidate = references?.[0];
    return typeof candidate === "string" && candidate.length > 0 ? candidate : null;
  });
  const candidateCounts = new Map();
  for (const candidateId of candidateIds) {
    if (candidateId !== null) {
      candidateCounts.set(candidateId, (candidateCounts.get(candidateId) ?? 0) + 1);
    }
  }

  return chapter.content.map((content, frameIndex) => {
    const candidateId = candidateIds[frameIndex];
    const asset = candidateId === null ? undefined : assetsById.get(candidateId);
    const assetId = asset !== undefined && candidateCounts.get(candidateId) === 1 ? candidateId : "";
    const rawPreview = content.preview;
    const previewUrl = typeof rawPreview === "string" && roughCutVideoUrlPattern.test(rawPreview)
      ? rawPreview
      : null;
    const rawText = content.text || content.original_text || "";
    const reasons = [];
    if (assetId === "") {
      reasons.push(roughCutLegacyIdReason);
    }
    if (previewUrl === null) {
      reasons.push(roughCutMissingVideoReason);
    }

    return {
      asset_id: assetId,
      frame_index: frameIndex,
      text: typeof rawText === "string" ? rawText : String(rawText),
      preview_url: previewUrl,
      missing_reason: reasons.length > 0 ? reasons.join("；") : null,
      included: assetId !== "",
      pending: false,
    };
  });
}

function createLegacyRoughCutFrame(frameIndex, text = "历史草稿分镜") {
  return {
    asset_id: "",
    frame_index: frameIndex,
    text,
    preview_url: null,
    missing_reason: `${roughCutLegacyIdReason}；${roughCutMissingVideoReason}`,
    included: false,
    pending: false,
  };
}

function validateRoughCutSource(chapter, selectedSeries) {
  if (chapter.series_id !== selectedSeries.id) {
    return null;
  }
  const sourceContent = chapter.content === null
    ? []
    : Array.isArray(chapter.content)
      ? chapter.content
      : null;
  if (sourceContent === null) {
    return null;
  }

  const assets = assetsBySeriesAndChapter.get(selectedSeries.id + ":" + chapter.id) ?? [];
  const rawIds = [];
  const seenIds = new Set();
  for (const content of sourceContent) {
    const references = Array.isArray(content?.storyboard) ? content.storyboard : null;
    const assetId = references?.[0];
    if (
      typeof assetId !== "string"
      || Array.from(assetId).length < 1
      || Array.from(assetId).length > 36
      || seenIds.has(assetId)
    ) {
      return null;
    }
    const matchingAssets = assets.filter((asset) => asset.id === assetId);
    if (
      matchingAssets.length !== 1
      || matchingAssets[0]?.series_id !== selectedSeries.id
      || matchingAssets[0]?.chapter_id !== chapter.id
    ) {
      return null;
    }
    seenIds.add(assetId);
    rawIds.push(assetId);
  }

  const projectedFrames = roughCutSourceFrames(chapter);
  if (
    projectedFrames.length !== rawIds.length
    || projectedFrames.some((frame, index) => frame.asset_id !== rawIds[index])
  ) {
    return null;
  }
  return new Map(projectedFrames.map((frame) => [frame.asset_id, frame]));
}

function roughCutOwnerKey(userId, chapterId) {
  return JSON.stringify([userId, chapterId]);
}

function createSavedRoughCutSnapshot(chapter, savedState) {
  const sourceById = validateRoughCutSource(chapter, { id: chapter.series_id });
  if (sourceById === null) {
    return null;
  }

  const frames = savedState.frames.map((savedFrame) => {
    const sourceFrame = sourceById.get(savedFrame.asset_id);
    if (sourceFrame === undefined) {
      return null;
    }
    return {
      ...sourceFrame,
      included: savedFrame.included,
      pending: false,
    };
  });
  if (frames.some((frame) => frame === null)) {
    return null;
  }
  return {
    chapter_id: chapter.id,
    revision: savedState.revision,
    saved: true,
    frames,
    removed_asset_ids: [],
  };
}

function createPersonalRoughCutSnapshot(chapter, account, mode) {
  const sourceFrames = roughCutSourceFrames(chapter);
  if (mode === "unsaved" || mode === "unsaved-no-video") {
    const frames = sourceFrames.map((frame) => ({ ...frame }));
    if (mode === "unsaved-no-video") {
      const frameWithoutVideo = frames.find((frame) => frame.asset_id !== "");
      if (frameWithoutVideo !== undefined) {
        frameWithoutVideo.preview_url = null;
        frameWithoutVideo.missing_reason = roughCutMissingVideoReason;
      }
    }
    return {
      chapter_id: chapter.id,
      revision: 0,
      saved: false,
      frames,
      removed_asset_ids: [],
    };
  }
  if (mode === "empty") {
    return {
      chapter_id: chapter.id,
      revision: 0,
      saved: false,
      frames: [],
      removed_asset_ids: [],
    };
  }
  if (mode === "removed") {
    return {
      chapter_id: chapter.id,
      revision: 5,
      saved: true,
      frames: sourceFrames.map((frame) => ({
        ...frame,
        included: false,
        pending: frame.asset_id !== "",
      })),
      removed_asset_ids: [
        `${account.user.id}-retired-rough-cut-item`,
        `${account.user.id}-retired-rough-cut-item`,
      ],
    };
  }
  if (mode === "legacy") {
    const frames = [...sourceFrames];
    for (let frameIndex = frames.length; frameIndex < 501; frameIndex += 1) {
      frames.push(createLegacyRoughCutFrame(frameIndex, `历史草稿分镜 ${frameIndex + 1}`));
    }
    return {
      chapter_id: chapter.id,
      revision: 9,
      saved: true,
      frames,
      removed_asset_ids: [
        `${account.user.id}-legacy-removed-item`,
        `${account.user.id}-legacy-removed-item`,
      ],
    };
  }
  if (mode === "pending") {
    const frames = sourceFrames.map((frame) => ({ ...frame, included: false, pending: false }));
    const frameToSchedule = frames.findIndex((frame) => frame.asset_id !== "");
    if (frameToSchedule !== -1) {
      frames[frameToSchedule] = { ...frames[frameToSchedule], included: false, pending: true };
    }
    return {
      chapter_id: chapter.id,
      revision: 7,
      saved: true,
      frames,
      removed_asset_ids: [],
    };
  }

  const isSecondUser = account.user.id === secondUser.id;
  if (isSecondUser) {
    const frames = sourceFrames.map((frame, index) => ({
      ...frame,
      included: frame.asset_id !== "" && index === 0,
      pending: frame.asset_id !== "" && index === 1,
    }));
    return {
      chapter_id: chapter.id,
      revision: 11,
      saved: true,
      frames,
      removed_asset_ids: [`${account.user.id}-retired-rough-cut-item`],
    };
  }

  return {
    chapter_id: chapter.id,
    revision: 6,
    saved: true,
    frames: sourceFrames.slice().reverse().map((frame, index) => ({
      ...frame,
      included: frame.asset_id !== "" && index === 1,
      pending: false,
    })),
    removed_asset_ids: [
      `${account.user.id}-retired-rough-cut-item`,
      `${account.user.id}-retired-rough-cut-item`,
    ],
  };
}

async function handleLogin(request, response) {
  await waitForDelay("login");
  const forcedStatus = statusOverride("FIXTURE_LOGIN_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "login", forcedStatus);
    return;
  }
  if (fixtureMode("login") === "timeout") {
    return;
  }

  let body;
  try {
    body = await readJsonBody(request);
  } catch {
    sendJson(request, response, 400, { detail: "登录请求格式无效。" });
    return;
  }
  const account = typeof body === "object" && body !== null
    ? fixtureAccounts.find((candidate) => (
      candidate.username === body.username && candidate.password === body.password
    ))
    : undefined;
  if (!account) {
    sendJson(request, response, 401, { detail: "账号或密码不正确。" });
    return;
  }
  sendJson(request, response, 200, {
    access_token: account.token,
    token_type: "bearer",
    user: account.user,
  });
}

async function handleMe(request, response) {
  await waitForDelay("me");
  const forcedStatus = statusOverride("FIXTURE_ME_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "me", forcedStatus);
    return;
  }
  if (fixtureMode("me") === "timeout") {
    return;
  }
  const currentUser = authenticatedUser(request);
  if (currentUser === null) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  sendJson(request, response, 200, currentUser);
}

async function handleSeries(request, response) {
  await waitForDelay("series");
  const forcedStatus = statusOverride("FIXTURE_SERIES_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "series", forcedStatus);
    return;
  }
  if (fixtureMode("series") === "timeout") {
    return;
  }
  if (!hasValidBearer(request)) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (fixtureMode("series") === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (fixtureMode("series") === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }
  sendJson(
    request,
    response,
    200,
    process.env.FIXTURE_SERIES_EMPTY === "true" ? [] : series,
  );
}

async function handleChapters(request, response, pathname) {
  await waitForDelay("chapters");
  const forcedStatus = statusOverride("FIXTURE_CHAPTERS_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "chapters", forcedStatus);
    return;
  }
  if (fixtureMode("chapters") === "timeout") {
    return;
  }
  if (!hasValidBearer(request)) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (fixtureMode("chapters") === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (fixtureMode("chapters") === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }

  const seriesId = decodeSeriesId(pathname, "chapters");
  const selectedSeries = series.find((item) => item.id === seriesId);
  if (!selectedSeries || !chaptersBySeries.has(seriesId)) {
    sendJson(request, response, 404, { detail: "剧集不存在。" });
    return;
  }
  if (!selectedSeries.can_enter) {
    sendJson(request, response, 403, { detail: "该剧集当前由其他创作者负责。" });
    return;
  }
  if (process.env.FIXTURE_CHAPTERS_MODE === "invalid-structure") {
    sendJson(request, response, 200, [{ id: "missing-required-chapter-fields" }]);
    return;
  }
  sendJson(
    request,
    response,
    200,
    process.env.FIXTURE_CHAPTERS_EMPTY === "true" ? [] : chaptersBySeries.get(seriesId),
  );
}

async function handleStoryboardAssets(request, response, pathname, searchParams) {
  await waitForDelay("assets");
  const forcedStatus = statusOverride("FIXTURE_ASSETS_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "assets", forcedStatus);
    return;
  }
  if (fixtureMode("assets") === "timeout") {
    return;
  }
  if (!hasValidBearer(request)) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (fixtureMode("assets") === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (fixtureMode("assets") === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }

  const seriesId = decodeSeriesId(pathname, "storyboard-assets");
  const selectedSeries = series.find((item) => item.id === seriesId);
  if (!selectedSeries || !chaptersBySeries.has(seriesId)) {
    sendJson(request, response, 404, { detail: "剧集不存在。" });
    return;
  }
  if (!selectedSeries.can_enter) {
    sendJson(request, response, 403, { detail: "该剧集当前由其他创作者负责。" });
    return;
  }
  const chapterId = searchParams.get("chapter_id");
  if (chapterId === null || chapterId.length === 0) {
    sendJson(request, response, 422, { detail: "chapter_id 参数无效。" });
    return;
  }
  if (process.env.FIXTURE_ASSETS_MODE === "invalid-structure") {
    sendJson(request, response, 200, [{ id: "missing-required-asset-fields" }]);
    return;
  }
  const chapterBelongsToSeries = chaptersBySeries.get(seriesId).some((chapter) => chapter.id === chapterId);
  if (!chapterBelongsToSeries || process.env.FIXTURE_ASSETS_EMPTY === "true") {
    sendJson(request, response, 200, []);
    return;
  }
  sendJson(request, response, 200, assetsBySeriesAndChapter.get(`${seriesId}:${chapterId}`) ?? []);
}

async function handleAssetCollection(request, response, pathname, endpoint) {
  await waitForDelay(endpoint);
  const forcedStatus = statusOverride(`FIXTURE_${endpoint.toUpperCase()}_STATUS`);
  if (forcedStatus !== null) {
    sendControlledError(request, response, endpoint, forcedStatus);
    return;
  }
  if (fixtureMode(endpoint) === "timeout") {
    return;
  }
  if (!hasValidBearer(request)) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (fixtureMode(endpoint) === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (fixtureMode(endpoint) === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }

  const seriesId = decodeSeriesId(pathname, endpoint);
  const selectedSeries = series.find((item) => item.id === seriesId);
  if (!selectedSeries || !assetCollectionsBySeries.has(seriesId)) {
    sendJson(request, response, 404, { detail: "剧集不存在。" });
    return;
  }
  if (!selectedSeries.can_enter) {
    sendJson(request, response, 403, { detail: "该剧集当前由其他创作者负责。" });
    return;
  }
  if (fixtureMode(endpoint) === "invalid-structure") {
    sendJson(request, response, 200, [{ id: `missing-required-${endpoint}-fields` }]);
    return;
  }
  if (process.env[`FIXTURE_${endpoint.toUpperCase()}_EMPTY`] === "true") {
    sendJson(request, response, 200, []);
    return;
  }

  sendJson(request, response, 200, assetCollectionsBySeries.get(seriesId)[endpoint]);
}

async function handlePersonalProductionNotes(request, response, pathname, url) {
  await waitForDelay("personal_notes");
  const forcedStatus = statusOverride("FIXTURE_PERSONAL_NOTES_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "personal_notes", forcedStatus);
    return;
  }
  if (fixtureMode("personal_notes") === "timeout") {
    return;
  }

  const currentUser = authenticatedUser(request);
  if (currentUser === null) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (fixtureMode("personal_notes") === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (fixtureMode("personal_notes") === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }

  if (url.search !== "") {
    sendJson(request, response, 422, { detail: "个人制作记录接口不接受查询参数。" });
    return;
  }
  const chapterId = decodePersonalNotesChapterId(pathname);
  const selectedSeries = chapterId === null ? undefined : seriesForChapter(chapterId);
  if (!selectedSeries) {
    sendJson(request, response, 404, { detail: "章节不存在。" });
    return;
  }
  if (!selectedSeries.can_enter) {
    sendJson(request, response, 403, { detail: "该剧集当前由其他创作者负责。" });
    return;
  }
  if (fixtureMode("personal_notes") === "invalid-structure") {
    sendJson(request, response, 200, { chapter_id: chapterId, frames: "invalid" });
    return;
  }

  const snapshot = personalNotesByChapterAndUser.get(`${currentUser.id}:${chapterId}`);
  if (!snapshot) {
    sendJson(request, response, 404, { detail: "章节个人记录不存在。" });
    return;
  }

  const payload = JSON.parse(JSON.stringify(snapshot));
  if (fixtureMode("personal_notes") === "mismatch" && payload.frames[0]) {
    const digest = payload.frames[0].asset_image_digest;
    const firstDigit = digest?.[0] === "0" ? "1" : "0";
    payload.frames[0].asset_image_digest = `${firstDigit}${digest?.slice(1) ?? "0".repeat(63)}`;
  }
  sendJson(request, response, 200, payload);
}

function writePersonalNote(snapshot, update) {
  const nextSnapshot = JSON.parse(JSON.stringify(snapshot));
  const updateFrame = update.frames[0];
  const note = nextSnapshot.frame_notes[updateFrame.storyboard_asset_id];
  const previousNote = typeof note === "object" && note !== null && !Array.isArray(note)
    ? note
    : {};
  Object.defineProperty(nextSnapshot.frame_notes, updateFrame.storyboard_asset_id, {
    configurable: true,
    enumerable: true,
    writable: true,
    value: {
      ...previousNote,
      status: updateFrame.status,
      note: updateFrame.note,
      approved_media_revision: updateFrame.status === "approved"
        ? updateFrame.expected_media_revision
        : null,
      needs_reconfirmation: false,
    },
  });
  nextSnapshot.revision = snapshot.revision + 1;
  return nextSnapshot;
}

function writePersonalResume(snapshot, update) {
  const nextSnapshot = JSON.parse(JSON.stringify(snapshot));
  nextSnapshot.resume_frame_id = update.resume_frame_id;
  nextSnapshot.revision = snapshot.revision + 1;
  return nextSnapshot;
}

function applyFixtureResumeMaintenance(snapshot) {
  const nextSnapshot = JSON.parse(JSON.stringify(snapshot));
  const firstFrame = nextSnapshot.frames[0];
  if (!firstFrame || typeof firstFrame.storyboard_asset_id !== "string") {
    return nextSnapshot;
  }

  const frameId = firstFrame.storyboard_asset_id;
  const rawNote = nextSnapshot.frame_notes[frameId];
  if (typeof rawNote === "object" && rawNote !== null && !Array.isArray(rawNote)) {
    const maintainedNote = { ...rawNote, needs_reconfirmation: true };
    delete maintainedNote.approved_media_revision;
    Object.defineProperty(nextSnapshot.frame_notes, frameId, {
      configurable: true,
      enumerable: true,
      writable: true,
      value: maintainedNote,
    });
  }

  firstFrame.media_revision = Number.isSafeInteger(firstFrame.media_revision)
    ? firstFrame.media_revision + 1
    : firstFrame.media_revision;
  firstFrame.asset_image_digest = sha256(`${frameId}:resume-maintenance-image`);
  firstFrame.preview_digest = sha256(`${frameId}:resume-maintenance-preview`);
  return nextSnapshot;
}

function applyFixtureMaintenanceConflict(snapshot, targetAssetId) {
  const nextSnapshot = JSON.parse(JSON.stringify(snapshot));
  const staleApprovedFrame = nextSnapshot.frames.find((frame) => {
    if (frame.storyboard_asset_id === targetAssetId || frame.storyboard_asset_id === null) {
      return false;
    }
    const rawNote = nextSnapshot.frame_notes[frame.storyboard_asset_id];
    return typeof rawNote === "object"
      && rawNote !== null
      && rawNote.status === "approved"
      && rawNote.approved_media_revision !== frame.media_revision;
  });
  if (!staleApprovedFrame) {
    return null;
  }

  const previousNote = nextSnapshot.frame_notes[staleApprovedFrame.storyboard_asset_id];
  Object.defineProperty(nextSnapshot.frame_notes, staleApprovedFrame.storyboard_asset_id, {
    configurable: true,
    enumerable: true,
    writable: true,
    value: {
      ...previousNote,
      status: "unmarked",
      approved_media_revision: null,
      needs_reconfirmation: true,
    },
  });
  nextSnapshot.revision += 1;
  return nextSnapshot;
}

async function handlePersonalProductionNotesSave(request, response, pathname, url) {
  const currentUser = authenticatedUser(request);
  if (currentUser === null) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }

  const forcedStatus = statusOverride("FIXTURE_PERSONAL_NOTES_SAVE_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "personal_notes_save", forcedStatus);
    return;
  }

  if (url.search !== "") {
    sendJson(request, response, 422, { detail: "个人制作记录保存接口不接受查询参数。" });
    return;
  }

  const chapterId = decodePersonalNotesChapterId(pathname);
  const selectedSeries = chapterId === null ? undefined : seriesForChapter(chapterId);
  if (!selectedSeries) {
    sendJson(request, response, 404, { detail: "章节不存在。" });
    return;
  }
  if (!selectedSeries.can_enter) {
    sendJson(request, response, 403, { detail: "该剧集当前由其他创作者负责。" });
    return;
  }

  let rawBody;
  try {
    rawBody = await readJsonBody(request);
  } catch {
    sendJson(request, response, 422, { detail: "个人制作记录保存正文格式无效。" });
    return;
  }
  const resumeUpdate = parsePersonalResumeUpdate(rawBody);
  const update = resumeUpdate === null ? parsePersonalNoteUpdate(rawBody) : null;
  if (resumeUpdate === null && update === null) {
    sendJson(request, response, 422, { detail: "个人制作记录保存正文格式无效。" });
    return;
  }

  const accountKey = `${currentUser.id}:${chapterId}`;
  const snapshot = personalNotesByChapterAndUser.get(accountKey);
  if (!snapshot) {
    sendJson(request, response, 404, { detail: "章节个人记录不存在。" });
    return;
  }

  const mode = fixtureMode("personal_notes_save");

  if (resumeUpdate !== null) {
    if (mode === "revision-conflict") {
      sendJson(request, response, 409, { detail: "个人制作记录版本已变化，请重新读取。" });
      return;
    }
    if (mode === "maintenance-conflict") {
      const maintainedSnapshot = applyFixtureMaintenanceConflict(snapshot, resumeUpdate.resume_frame_id);
      if (maintainedSnapshot !== null) {
        personalNotesByChapterAndUser.set(accountKey, maintainedSnapshot);
      }
      sendJson(request, response, 409, { detail: "历史认可维护已独立提交，请重新读取后核对。" });
      return;
    }
    if (snapshot.revision !== resumeUpdate.expected_revision) {
      sendJson(request, response, 409, { detail: "个人记录版本已变化，请重新读取。" });
      return;
    }

    const chapter = chaptersBySeries.get(selectedSeries.id)?.find((item) => item.id === chapterId);
    if (
      chapter === undefined
      || !hasValidResumeTarget(chapter, selectedSeries, snapshot, resumeUpdate.resume_frame_id)
    ) {
      sendJson(request, response, 422, { detail: "当前镜头身份无法核对，不能设置续作位置。" });
      return;
    }

    const canonicalBase = mode === "resume-maintenance-success" && resumeUpdate.resume_frame_id !== null
      ? applyFixtureResumeMaintenance(snapshot)
      : snapshot;
    const nextSnapshot = writePersonalResume(canonicalBase, resumeUpdate);
    personalNotesByChapterAndUser.set(accountKey, nextSnapshot);

    if (mode === "timeout") {
      logRequest(request.method ?? "PUT", safeRequestTarget(request), 0);
      return;
    }
    if (mode === "body-timeout") {
      await sendBodyTimeout(request, response);
      return;
    }
    if (mode === "applied-invalid-json") {
      sendInvalidJson(request, response);
      return;
    }
    if (mode === "applied-invalid-structure") {
      sendJson(request, response, 200, {
        chapter_id: chapterId,
        revision: nextSnapshot.revision,
        media_state: nextSnapshot.media_state,
        frames: "invalid",
        frame_notes: nextSnapshot.frame_notes,
        resume_frame_id: nextSnapshot.resume_frame_id,
      });
      return;
    }

    await waitForDelay("personal_notes_save");
    sendJson(request, response, 200, nextSnapshot);
    return;
  }

  if (mode === "revision-conflict") {
    sendJson(request, response, 409, { detail: "个人制作记录版本已变化，请重新读取。" });
    return;
  }
  if (mode === "media-conflict") {
    sendJson(request, response, 409, { detail: "镜头媒体版本已变化，请重新读取。" });
    return;
  }
  if (mode === "maintenance-conflict") {
    const maintainedSnapshot = applyFixtureMaintenanceConflict(
      snapshot,
      update.frames[0].storyboard_asset_id,
    );
    if (maintainedSnapshot !== null) {
      personalNotesByChapterAndUser.set(accountKey, maintainedSnapshot);
    }
    sendJson(request, response, 409, { detail: "历史认可维护已独立提交，请重新读取后核对。" });
    return;
  }

  const actualFrameMatches = snapshot.frames.filter((frame) => (
    frame.storyboard_asset_id === update.frames[0].storyboard_asset_id
  ));
  const targetFrame = actualFrameMatches[0];
  if (snapshot.revision !== update.expected_revision) {
    sendJson(request, response, 409, { detail: "个人记录版本已变化，请重新读取。" });
    return;
  }
  if (
    actualFrameMatches.length !== 1
    || targetFrame?.source_valid !== true
    || targetFrame.media_revision === null
  ) {
    sendJson(request, response, 422, { detail: "当前镜头身份无法核对，不能保存个人记录。" });
    return;
  }
  if (
    snapshot.media_state !== "ready"
    || targetFrame.media_revision !== update.frames[0].expected_media_revision
  ) {
    sendJson(request, response, 409, { detail: "镜头媒体版本已变化，请重新读取。" });
    return;
  }
  if (
    update.frames[0].status === "approved"
    && targetFrame.asset_image_digest === null
    && targetFrame.preview_digest === null
  ) {
    sendJson(request, response, 422, { detail: "当前镜头没有可核对的媒体，不能保存认可状态。" });
    return;
  }

  const nextSnapshot = writePersonalNote(snapshot, update);
  personalNotesByChapterAndUser.set(accountKey, nextSnapshot);

  if (mode === "timeout") {
    logRequest(request.method ?? "PUT", safeRequestTarget(request), 0);
    return;
  }
  if (mode === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (mode === "applied-invalid-json") {
    sendInvalidJson(request, response);
    return;
  }
  if (mode === "applied-invalid-structure") {
    sendJson(request, response, 200, {
      chapter_id: chapterId,
      revision: nextSnapshot.revision,
      media_state: "ready",
      frames: "invalid",
      frame_notes: nextSnapshot.frame_notes,
      resume_frame_id: nextSnapshot.resume_frame_id,
    });
    return;
  }

  await waitForDelay("personal_notes_save");
  sendJson(request, response, 200, nextSnapshot);
}

async function handlePersonalRoughCut(request, response, pathname, url) {
  await waitForDelay("rough_cut");
  const forcedStatus = statusOverride("FIXTURE_ROUGH_CUT_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "rough_cut", forcedStatus);
    return;
  }
  if (fixtureMode("rough_cut") === "timeout") {
    return;
  }

  const currentUser = authenticatedUser(request);
  if (currentUser === null) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (fixtureMode("rough_cut") === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (fixtureMode("rough_cut") === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }
  if (url.search !== "") {
    sendJson(request, response, 422, { detail: "个人粗剪接口不接受查询参数。" });
    return;
  }

  const chapterId = decodePersonalRoughCutChapterId(pathname);
  const selectedSeries = chapterId === null ? undefined : seriesForChapter(chapterId);
  const chapter = selectedSeries === undefined
    ? undefined
    : chaptersBySeries.get(selectedSeries.id)?.find((item) => item.id === chapterId);
  if (!selectedSeries || !chapter) {
    sendJson(request, response, 404, { detail: "章节不存在。" });
    return;
  }
  if (!selectedSeries.can_enter) {
    sendJson(request, response, 403, { detail: "该剧集当前由其他创作者负责。" });
    return;
  }
  if (fixtureMode("rough_cut") === "invalid-shape") {
    sendJson(request, response, 200, {
      chapter_id: chapter.id,
      revision: 1,
      saved: true,
      frames: [{ asset_id: "", frame_index: 0, text: "坏响应", preview_url: null, included: false, pending: false }],
      removed_asset_ids: [],
    });
    return;
  }

  const savedState = personalRoughCutsByChapterAndUser.get(
    roughCutOwnerKey(currentUser.id, chapter.id),
  );
  const savedSnapshot = savedState === undefined
    ? null
    : createSavedRoughCutSnapshot(chapter, savedState);
  sendJson(
    request,
    response,
    200,
    savedSnapshot ?? createPersonalRoughCutSnapshot(
      chapter,
      { user: currentUser },
      fixtureMode("rough_cut"),
    ),
  );
}

async function handlePersonalRoughCutSave(request, response, pathname, url) {
  await waitForDelay("rough_cut_save");
  const forcedStatus = statusOverride("FIXTURE_ROUGH_CUT_SAVE_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "rough_cut_save", forcedStatus);
    return;
  }

  const mode = fixtureMode("rough_cut_save");
  if (mode === "timeout") {
    logRequest(request.method ?? "PUT", safeRequestTarget(request), 0);
    return;
  }

  const currentUser = authenticatedUser(request);
  if (currentUser === null) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (url.search !== "") {
    sendJson(request, response, 422, { detail: "个人粗剪保存接口不接受查询参数。" });
    return;
  }

  const chapterId = decodePersonalRoughCutChapterId(pathname);
  const selectedSeries = chapterId === null ? undefined : seriesForChapter(chapterId);
  const chapter = selectedSeries === undefined
    ? undefined
    : chaptersBySeries.get(selectedSeries.id)?.find((item) => item.id === chapterId);
  if (!selectedSeries || !chapter) {
    sendJson(request, response, 404, { detail: "章节不存在。" });
    return;
  }
  if (!selectedSeries.can_enter) {
    sendJson(request, response, 403, { detail: "该剧集当前由其他创作者负责。" });
    return;
  }

  let rawBody;
  try {
    rawBody = await readJsonBody(request, 262_144);
  } catch {
    sendJson(request, response, 422, { detail: "粗剪保存内容不是有效的 JSON。" });
    return;
  }
  const update = parsePersonalRoughCutUpdate(rawBody);
  if (update === null) {
    sendJson(request, response, 422, { detail: "粗剪保存内容格式无效。" });
    return;
  }

  const sourceById = validateRoughCutSource(chapter, selectedSeries);
  if (
    sourceById === null
    || sourceById.size !== update.frames.length
    || update.frames.some(({ asset_id }) => !sourceById.has(asset_id))
  ) {
    sendJson(request, response, 422, { detail: "粗剪保存内容必须对应当前章节完整源镜头集合。" });
    return;
  }

  if (mode === "revision-conflict") {
    sendJson(request, response, 409, { detail: "粗剪版本已变化，请重新读取后再保存。" });
    return;
  }

  const ownerKey = roughCutOwnerKey(currentUser.id, chapter.id);
  const previousState = personalRoughCutsByChapterAndUser.get(ownerKey);
  const baseline = previousState ?? createPersonalRoughCutSnapshot(
    chapter,
    { user: currentUser },
    fixtureMode("rough_cut"),
  );
  if (update.expected_revision !== baseline.revision) {
    sendJson(request, response, 409, { detail: "粗剪版本已变化，请重新读取后再保存。" });
    return;
  }

  const nextState = {
    revision: update.expected_revision + 1,
    frames: update.frames.map(({ asset_id, included }) => ({ asset_id, included })),
  };
  personalRoughCutsByChapterAndUser.set(ownerKey, nextState);

  if (mode === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }

  const canonical = createSavedRoughCutSnapshot(chapter, nextState);
  if (canonical === null) {
    sendJson(request, response, 422, { detail: "粗剪保存后的当前源镜头无法核对。" });
    return;
  }
  if (mode === "applied-invalid-structure") {
    sendJson(request, response, 200, {
      ...canonical,
      frames: "invalid",
    });
    return;
  }

  if (mode === "source-maintenance-success") {
    const frameCount = canonical.frames.length;
    canonical.frames = canonical.frames.map((frame, index) => ({
      ...frame,
      frame_index: frameCount > 1 ? frameCount - index - 1 : frame.frame_index,
      text: frame.text + "（服务端维护）",
      preview_url: null,
      missing_reason: "服务端维护了当前视频元数据。",
    }));
  }
  sendJson(request, response, 200, canonical);
}

async function handleMyTeams(request, response, url) {
  await waitForDelay("teams");
  const forcedStatus = statusOverride("FIXTURE_TEAMS_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "teams", forcedStatus);
    return;
  }
  if (fixtureMode("teams") === "timeout") {
    return;
  }

  const currentUser = authenticatedUser(request);
  if (currentUser === null) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (url.search !== "") {
    sendJson(request, response, 422, { detail: "团队目录接口不接受查询参数。" });
    return;
  }
  if (fixtureMode("teams") === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }
  if (fixtureMode("teams") === "invalid-shape") {
    sendJson(request, response, 200, { teams: teamsByUser.get(currentUser.id) ?? [] });
    return;
  }

  let teams = teamsByUser.get(currentUser.id) ?? [];
  const mode = fixtureMode("teams");
  if (mode === "empty") {
    teams = [];
  } else if (mode === "removed") {
    const removedId = process.env.FIXTURE_TEAMS_REMOVED_ID ?? "fixture-team";
    teams = teams.filter(({ id }) => id !== removedId);
  }
  sendJson(request, response, 200, teams);
}

async function handleTasksList(request, response, url) {
  await waitForDelay("tasks");
  const forcedStatus = statusOverride("FIXTURE_TASKS_STATUS");
  if (forcedStatus !== null) {
    sendControlledError(request, response, "tasks", forcedStatus);
    return;
  }
  if (fixtureMode("tasks") === "timeout") {
    return;
  }

  const currentUser = authenticatedUser(request);
  if (currentUser === null) {
    sendJson(request, response, 401, { detail: "登录状态已失效。" });
    return;
  }
  if (fixtureMode("tasks") === "body-timeout") {
    await sendBodyTimeout(request, response);
    return;
  }
  if (fixtureMode("tasks") === "invalid-json") {
    sendInvalidJson(request, response);
    return;
  }

  const pageValues = url.searchParams.getAll("page");
  const pageSizeValues = url.searchParams.getAll("page_size");
  if (
    Array.from(url.searchParams.keys()).length !== 2
    || pageValues.length !== 1
    || pageSizeValues.length !== 1
    || !/^[1-9]\d*$/.test(pageValues[0] ?? "")
    || pageSizeValues[0] !== "10"
  ) {
    sendJson(request, response, 422, { detail: "仅支持正整数 page 与 page_size=10。" });
    return;
  }

  const page = Number(pageValues[0]);
  if (!Number.isSafeInteger(page) || page < 1) {
    sendJson(request, response, 422, { detail: "page 超出安全整数范围。" });
    return;
  }
  if (fixtureMode("tasks") === "invalid-structure") {
    sendJson(request, response, 200, { total: 23, page, page_size: 10, tasks: [{ id: "incomplete-task" }] });
    return;
  }

  const allTasks = tasksByUser.get(currentUser.id) ?? [];
  let total = allTasks.length;
  let tasks = allTasks.slice((page - 1) * 10, page * 10);
  const mode = fixtureMode("tasks");
  if (mode === "empty-page" && page > 1) {
    tasks = [];
  } else if (mode === "total-shrink") {
    total = 7;
    tasks = page === 1 ? allTasks.slice(0, 7) : [];
  }

  if (mode === "duplicate-id" && tasks.length > 1) {
    tasks[1] = { ...tasks[1], id: tasks[0].id };
  }
  const responsePage = mode === "page-mismatch" ? page + 1 : page;
  const responsePageSize = mode === "page-size-mismatch" ? 5 : 10;
  sendJson(request, response, 200, {
    total,
    page: responsePage,
    page_size: responsePageSize,
    tasks,
  });
}

function sendMedia(request, response, pathname) {
  setCors(request, response);
  const forcedStatus = statusOverride("FIXTURE_MEDIA_STATUS");
  if (forcedStatus !== null) {
    response.statusCode = forcedStatus;
    response.setHeader("Cache-Control", "no-store");
    logRequest(request.method ?? "UNKNOWN", pathname, forcedStatus);
    response.end("本地合成媒体不可用。");
    return;
  }

  response.statusCode = 200;
  response.setHeader("Cache-Control", "no-store");
  if (process.env.FIXTURE_MEDIA_MODE === "invalid-image") {
    response.setHeader("Content-Type", "image/png");
    logRequest(request.method ?? "UNKNOWN", pathname, 200);
    response.end("invalid synthetic image");
    return;
  }

  response.setHeader("Content-Type", "image/png");
  logRequest(request.method ?? "UNKNOWN", pathname, 200);
  response.end(Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/mqkAAAAASUVORK5CYII=",
    "base64",
  ));
}

const server = createServer(async (request, response) => {
  const origin = request.headers.origin;
  setCors(request, response);

  if (request.method === "OPTIONS") {
    if (typeof origin === "string" && !allowedOrigins.has(origin)) {
      sendJson(request, response, 403, { detail: "此浏览器来源未获本地 fixture 允许。" });
      return;
    }
    response.statusCode = 204;
    logRequest("OPTIONS", safeRequestTarget(request), 204);
    response.end();
    return;
  }

  const url = new URL(request.url ?? "/", "http://127.0.0.1");
  if (request.method === "POST" && url.pathname === "/api/auth/login") {
    await handleLogin(request, response);
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/auth/me") {
    await handleMe(request, response);
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/teams/my") {
    await handleMyTeams(request, response, url);
    return;
  }
  if (decodePersonalRoughCutChapterId(url.pathname) !== null && request.method === "GET") {
    await handlePersonalRoughCut(request, response, url.pathname, url);
    return;
  }
  if (decodePersonalRoughCutChapterId(url.pathname) !== null && request.method === "PUT") {
    await handlePersonalRoughCutSave(request, response, url.pathname, url);
    return;
  }
  if (request.method === "GET" && decodePersonalNotesChapterId(url.pathname) !== null) {
    await handlePersonalProductionNotes(request, response, url.pathname, url);
    return;
  }
  if (request.method === "PUT" && decodePersonalNotesChapterId(url.pathname) !== null) {
    await handlePersonalProductionNotesSave(request, response, url.pathname, url);
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/chat/tasks/list") {
    await handleTasksList(request, response, url);
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/series") {
    await handleSeries(request, response);
    return;
  }
  if (request.method === "GET" && decodeSeriesId(url.pathname, "chapters") !== null) {
    await handleChapters(request, response, url.pathname);
    return;
  }
  if (request.method === "GET" && decodeSeriesId(url.pathname, "storyboard-assets") !== null) {
    await handleStoryboardAssets(request, response, url.pathname, url.searchParams);
    return;
  }
  const assetEndpoint = ["characters", "scenes", "props"]
    .find((endpoint) => decodeSeriesId(url.pathname, endpoint) !== null);
  if (request.method === "GET" && assetEndpoint !== undefined) {
    await handleAssetCollection(request, response, url.pathname, assetEndpoint);
    return;
  }
  if (request.method === "GET" && /^\/media\/[A-Za-z0-9_-]+\.png$/.test(url.pathname)) {
    sendMedia(request, response, url.pathname);
    return;
  }

  sendJson(request, response, 404, { detail: "fixture 仅开放批准的本地业务接口及合成原图资源。" });
});

if (!Number.isInteger(port) || port < 0 || port > 65_535) {
  throw new Error("FIXTURE_PORT 必须是 0 到 65535 之间的整数。");
}

server.listen(port, host, () => {
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("本地 HTTP 服务没有可用的监听地址。");
  }
  process.stdout.write(`Local HTTP fixture listening on http://${host}:${address.port}/api\n`);
});
