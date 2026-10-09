import type { Character, Prop, Scene } from "../../shared/api/contracts";
import { demoSeries } from "../series/demoSeries";

export interface DemoAssetLibraryData {
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
}

const timestamp = "2026-10-04T08:30:00";

export function getDemoAssetLibraryData(seriesId: string): DemoAssetLibraryData | null {
  if (!demoSeries.some((series) => series.id === seriesId)) {
    return null;
  }

  const sharedId = seriesId + "-shared-material-id";

  return {
    characters: [
      {
        id: seriesId + "-character-linlan",
        series_id: seriesId,
        name: "林岚",
        gender: "女",
        age: "27",
        role: "主角 · 旧城记者",
        appearance: "短发，常穿深色外套，随身带着旧式录音笔。",
        description: "负责追查旧街档案失踪一事。",
        image_url: null,
        audio_url: null,
        voice_ref: null,
        aliases: ["林编辑", "阿岚"],
        canonical_key: "林岚",
        created_at: timestamp,
        updated_at: timestamp,
      },
      {
        id: sharedId,
        series_id: seriesId,
        name: "顾行舟",
        gender: "男",
        age: "31",
        role: "档案馆管理员",
        appearance: "戴细框眼镜，衣着整洁。",
        description: null,
        image_url: null,
        audio_url: null,
        voice_ref: null,
        aliases: null,
        canonical_key: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
    scenes: [
      {
        id: seriesId + "-scene-old-street",
        series_id: seriesId,
        title: "旧街清晨",
        description: "晨雾未散，街边店铺刚刚开门。",
        image_url: null,
        aliases: ["旧街早晨"],
        canonical_key: "旧街|清晨",
        created_at: timestamp,
        updated_at: timestamp,
      },
      {
        id: sharedId,
        series_id: seriesId,
        title: "档案馆地下室",
        description: "窄窗透进一束斜光，四周堆满纸箱。",
        image_url: null,
        aliases: null,
        canonical_key: "档案馆|室内|日",
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
    props: [
      {
        id: sharedId,
        series_id: seriesId,
        name: "旧信封",
        description: "封口处留有一枚褪色的蓝色印记。",
        image_url: null,
        aliases: ["无署名信封", "蓝印信封"],
        canonical_key: "旧信封",
        created_at: timestamp,
        updated_at: timestamp,
      },
      {
        id: seriesId + "-prop-key",
        series_id: seriesId,
        name: "黄铜钥匙",
        description: null,
        image_url: null,
        aliases: null,
        canonical_key: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
  };
}
