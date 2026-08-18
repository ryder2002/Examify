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

export function stemEnglishWord(word: string): string {
  const candidates = getEnglishCandidates(word);
  return candidates[candidates.length - 1] || word;
}

export function getEnglishCandidates(word: string): string[] {
  const cleaned = word.trim().toLowerCase();
  if (!cleaned || cleaned.length <= 2) return [word];

  const candidates: string[] = [word, cleaned];

  if (cleaned.endsWith("ed") && cleaned.length > 3) {
    if (cleaned.endsWith("ied") && cleaned.length > 4) {
      candidates.push(cleaned.slice(0, -3) + "y");
    }
    candidates.push(cleaned.slice(0, -1));
    candidates.push(cleaned.slice(0, -2));
    if (
      cleaned.length > 4 &&
      cleaned[cleaned.length - 3] === cleaned[cleaned.length - 4] &&
      "bcdfghlmnprst".includes(cleaned[cleaned.length - 3])
    ) {
      candidates.push(cleaned.slice(0, -3));
    }
  }

  if (cleaned.endsWith("ing") && cleaned.length > 4) {
    candidates.push(cleaned.slice(0, -3));
    candidates.push(cleaned.slice(0, -3) + "e");
    if (cleaned.endsWith("ying") && cleaned.length > 4) {
      candidates.push(cleaned.slice(0, -4) + "y");
    }
    if (
      cleaned.length > 5 &&
      cleaned[cleaned.length - 4] === cleaned[cleaned.length - 5] &&
      "bcdfghlmnprst".includes(cleaned[cleaned.length - 4])
    ) {
      candidates.push(cleaned.slice(0, -4));
    }
  }

  if (cleaned.endsWith("s") && cleaned.length > 3 && !cleaned.endsWith("ss")) {
    if (cleaned.endsWith("ies") && cleaned.length > 4) {
      candidates.push(cleaned.slice(0, -3) + "y");
    } else if (cleaned.endsWith("es") && cleaned.length > 4) {
      candidates.push(cleaned.slice(0, -2));
      candidates.push(cleaned.slice(0, -1));
    } else {
      candidates.push(cleaned.slice(0, -1));
    }
  }

  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of candidates) {
    const key = item.toLowerCase();
    if (key && !seen.has(key) && key.length >= 2) {
      seen.add(key);
      result.push(item);
    }
  }
  return result;
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
