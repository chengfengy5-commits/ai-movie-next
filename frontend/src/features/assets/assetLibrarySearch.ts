import type { AssetLibraryType, Character, Prop, Scene } from "../../shared/api/contracts";

export interface AssetLibraryItemsByCategory {
  characters: Character;
  scenes: Scene;
  props: Prop;
}

export interface IndexedAssetLibraryItem<Item> {
  readonly item: Item;
  readonly originalIndex: number;
}

function originalName(type: AssetLibraryType, item: Character | Scene | Prop): string {
  if (type === "scenes") {
    return "title" in item && typeof item.title === "string" ? item.title : "";
  }
  return "name" in item && typeof item.name === "string" ? item.name : "";
}

function searchableValues(type: AssetLibraryType, item: Character | Scene | Prop): string[] {
  const values = [originalName(type, item)];
  for (const alias of item.aliases ?? []) {
    if (alias.trim() !== "") {
      values.push(alias);
    }
  }
  return values;
}

export function searchAssetLibraryItems<Category extends AssetLibraryType>(
  category: Category,
  items: readonly AssetLibraryItemsByCategory[Category][],
  query: string,
): readonly IndexedAssetLibraryItem<AssetLibraryItemsByCategory[Category]>[] {
  const normalizedQuery = query.trim().toLowerCase();
  const matches: IndexedAssetLibraryItem<AssetLibraryItemsByCategory[Category]>[] = [];

  items.forEach((item, originalIndex) => {
    const matchesQuery = normalizedQuery === ""
      || searchableValues(category, item).some((value) => value.toLowerCase().includes(normalizedQuery));
    if (matchesQuery) {
      matches.push({ item, originalIndex });
    }
  });

  return matches;
}
