export type CharacterAsset = { characterId: string; imageSrc?: string };

// Character binaries are intentionally absent. Add a relative public path here
// later without changing layout components or production data contracts.
export const characterAssets: Record<string, CharacterAsset> = {
  "lim-potato": { characterId: "lim-potato" },
  "general-potato": { characterId: "general-potato" },
  "code-potato": { characterId: "code-potato" },
  "money-potato": { characterId: "money-potato" },
  "marketing-potato": { characterId: "marketing-potato" },
  "hr-potato": { characterId: "hr-potato" },
  "writer-potato": { characterId: "writer-potato" },
  "design-potato": { characterId: "design-potato" },
  "music-potato": { characterId: "music-potato" },
  "video-potato": { characterId: "video-potato" },
  "research-potato": { characterId: "research-potato" },
  "qa-potato": { characterId: "qa-potato" },
  "file-potato": { characterId: "file-potato" },
};

export function characterImage(characterId: string) {
  return characterAssets[characterId]?.imageSrc;
}
