export type DictionaryDirection = "en" | "vi";

export type DictionaryLookupRequest = {
  id: number;
  text: string;
  source: DictionaryDirection;
};

export type DictionaryPhonetic = {
  text: string;
  accent: string | null;
  has_audio: boolean;
  variant: number;
};

export type DictionarySense = {
  definition: string;
  example: string | null;
  example_source: "dictionary" | null;
  synonyms: string[];
  antonyms: string[];
};

export type DictionaryMeaning = {
  part_of_speech: string;
  senses: DictionarySense[];
};

export type DictionaryExample = {
  text: string;
  author: string;
  license: string;
  url: string;
  id: number;
};

export type DictionaryAttribution = {
  name: string;
  url: string;
  license: string;
};

export type DictionaryResult = {
  query: string;
  direction: "en-vi" | "vi-en";
  resolved_english_word: string;
  translations: string[];
  phonetics: DictionaryPhonetic[];
  meanings: DictionaryMeaning[];
  fallback_examples: DictionaryExample[];
  attribution: DictionaryAttribution[];
  warnings: string[];
  cached: boolean;
};

export function isDictionaryAvailable(quizMode: "practice" | "exam"): boolean {
  return quizMode === "practice";
}

const VIETNAMESE_MARKS =
  /[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]/i;

export function dictionarySourceForText(value: string): DictionaryDirection {
  return VIETNAMESE_MARKS.test(value.normalize("NFC")) ? "vi" : "en";
}

export function normalizeSelectedDictionaryText(value: string): string | null {
  const normalized = value
    .normalize("NFC")
    .replace(/\s+/g, " ")
    .trim()
    .replace(
      /^[\s"'“”‘’()[\]{},.!?;:…]+|[\s"'“”‘’()[\]{},.!?;:…]+$/g,
      "",
    )
    .trim();
  if (!normalized || normalized.length > 80) return null;
  if (normalized.split(/\s+/).length > 5) return null;
  return normalized;
}

export function dictionaryErrorMessage(status: number, detail?: string): string {
  if (detail) return detail;
  if (status === 401) return "Phiên kích hoạt đã hết hạn. Vui lòng kích hoạt lại.";
  if (status === 404) return "Không tìm thấy từ phù hợp.";
  if (status === 422) return "Chỉ hỗ trợ từ hoặc cụm từ ngắn.";
  if (status === 503) return "Dịch vụ từ điển đang tạm gián đoạn.";
  return "Không thể tra từ lúc này. Vui lòng thử lại.";
}
