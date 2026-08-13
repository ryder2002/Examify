"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { assetUrl } from "@/lib/api";
import {
  getToeicFullAudioIntroEndTime,
  getToeicFullAudioQuestionEndTime,
  getToeicListeningQuestionEndTime,
  type AudioRef,
} from "@/lib/utils";

export default function HiddenExamAudio({
  audios,
  active,
  currentQuestionNumber,
  currentQuestionNumbers,
  onAutoAdvance,
  onListeningComplete,
  showingDirections,
  onHideDirections,
}: {
  audios: AudioRef[];
  active: boolean;
  currentQuestionNumber?: number;
  currentQuestionNumbers?: number[];
  onAutoAdvance?: () => void;
  onListeningComplete?: () => void;
  showingDirections?: boolean;
  onHideDirections?: () => void;
}) {
  const element = useRef<HTMLAudioElement>(null);
  const directionElement = useRef<HTMLAudioElement>(null);
  const [legacyPartIndex, setLegacyPartIndex] = useState(0);
  const [mediaDuration, setMediaDuration] = useState(0);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const lastAdvancedQuestion = useRef<number | null>(null);
  const lastCompletedAudio = useRef<string | null>(null);

  const directionAudio = useMemo(
    () => audios.find((audio) => audio.part === "directions_part_1"),
    [audios],
  );
  const examAudios = useMemo(
    () => audios.filter((audio) => audio.part !== "directions_part_1"),
    [audios],
  );
  const fullAudio = useMemo(
    () => examAudios.find((audio) => audio.part === "full"),
    [examAudios],
  );
  const itemAudios = useMemo(
    () =>
      examAudios.filter(
        (audio) =>
          audio.scope === "question" ||
          audio.scope === "group" ||
          Boolean(audio.question_numbers?.length),
      ),
    [examAudios],
  );
  const legacyPartAudios = useMemo(
    () =>
      [...examAudios]
        .filter(
          (audio) =>
            /^part_[1-4]$/.test(audio.part) &&
            audio.scope !== "question" &&
            audio.scope !== "group" &&
            !audio.question_numbers?.length,
        )
        .sort((left, right) => left.part.localeCompare(right.part)),
    [examAudios],
  );
  const audioMode = fullAudio
    ? "full"
    : itemAudios.length > 0
      ? "item"
      : "legacy-part";
  const activeQuestionNumbers = useMemo(
    () =>
      currentQuestionNumbers?.length
        ? currentQuestionNumbers
        : currentQuestionNumber
          ? [currentQuestionNumber]
          : [],
    [currentQuestionNumber, currentQuestionNumbers],
  );
  const activeItemAudio = useMemo(
    () =>
      itemAudios.find((audio) =>
        audio.question_numbers?.some((number) =>
          activeQuestionNumbers.includes(number),
        ),
      ),
    [activeQuestionNumbers, itemAudios],
  );
  const activeAudio =
    audioMode === "full"
      ? fullAudio
      : audioMode === "item"
        ? activeItemAudio
        : legacyPartAudios[legacyPartIndex];

  useEffect(() => {
    lastCompletedAudio.current = null;
    setMediaDuration(0);
  }, [activeAudio?.id]);

  useEffect(() => {
    const audio = element.current;
    if (!audio) return;
    if (!active || (showingDirections && audioMode !== "full")) {
      audio.pause();
      return;
    }
    void audio.play().catch(() => {
      setPlaybackError("Trình duyệt đang chặn audio tự động. Vui lòng cho phép tự phát media rồi tải lại bài thi.");
    });
  }, [active, activeAudio?.id, audioMode, showingDirections]);

  useEffect(() => {
    const direction = directionElement.current;
    if (!direction) return;
    if (active && showingDirections && audioMode !== "full") {
      direction.currentTime = 0;
      void direction.play().catch(() => {
        setPlaybackError("Trình duyệt đang chặn audio tự động. Vui lòng cho phép tự phát media rồi tải lại bài thi.");
      });
    } else {
      direction.pause();
    }
  }, [active, audioMode, showingDirections]);

  useEffect(() => {
    if (showingDirections || !active) return;
    directionElement.current?.pause();
    const audio = element.current;
    if (!audio) return;
    if (audioMode === "full" && mediaDuration > 0) {
      const introEnd = getToeicFullAudioIntroEndTime(mediaDuration);
      if (audio.currentTime < introEnd) audio.currentTime = introEnd;
    }
    void audio.play().catch(() => {
      setPlaybackError("Trình duyệt đang chặn audio tự động. Vui lòng cho phép tự phát media rồi tải lại bài thi.");
    });
  }, [active, activeAudio?.id, audioMode, mediaDuration, showingDirections]);

  useEffect(() => {
    if (
      active &&
      showingDirections &&
      audioMode === "full" &&
      mediaDuration > 0 &&
      getToeicFullAudioIntroEndTime(mediaDuration) === 0
    ) {
      onHideDirections?.();
    }
  }, [active, audioMode, mediaDuration, onHideDirections, showingDirections]);

  const handleTimeUpdate = (event: React.SyntheticEvent<HTMLAudioElement>) => {
    if (!active || !onAutoAdvance || audioMode === "item") return;
    const timelineQuestion = Math.max(...activeQuestionNumbers, 0);
    if (timelineQuestion < 1 || timelineQuestion > 100) return;
    const audio = event.currentTarget;
    const introEnd =
      audioMode === "full"
        ? getToeicFullAudioIntroEndTime(audio.duration)
        : 0;

    if (
      showingDirections &&
      audioMode === "full" &&
      audio.currentTime >= introEnd &&
      onHideDirections
    ) {
      onHideDirections();
    }

    const targetEndTime =
      audioMode === "full"
        ? getToeicFullAudioQuestionEndTime(timelineQuestion, audio.duration)
        : getToeicListeningQuestionEndTime(timelineQuestion, "part");
    if (
      audio.currentTime >= targetEndTime &&
      lastAdvancedQuestion.current !== timelineQuestion
    ) {
      lastAdvancedQuestion.current = timelineQuestion;
      onAutoAdvance();
    }
  };

  const handleDirectionEnded = () => {
    if (showingDirections) onHideDirections?.();
  };

  const handleEnded = () => {
    if (!activeAudio || lastCompletedAudio.current === activeAudio.id) return;
    lastCompletedAudio.current = activeAudio.id;
    if (audioMode === "item") {
      onAutoAdvance?.();
      return;
    }
    if (audioMode === "legacy-part" && legacyPartIndex < legacyPartAudios.length - 1) {
      setLegacyPartIndex((current) => current + 1);
      return;
    }
    if (audioMode === "full") onListeningComplete?.();
  };

  const missingItemAudio = audioMode === "item" && !activeAudio;

  return (
    <div className="flex min-w-0 flex-col items-end gap-1">
      {(playbackError || missingItemAudio) && (
        <span role="status" className="max-w-xs text-right text-[10px] font-semibold text-amber-700">
          {playbackError || "Chưa có audio khớp với câu/nhóm hiện tại."}
        </span>
      )}
      {audioMode !== "full" && directionAudio && (
        <audio
          ref={directionElement}
          className="hidden"
          src={assetUrl(directionAudio.url)}
          preload="metadata"
          onEnded={handleDirectionEnded}
          onPlay={() => {
            setPlaybackError(null);
          }}
          aria-hidden="true"
        />
      )}
      {activeAudio && (
        <audio
          key={activeAudio.id}
          ref={element}
          className="hidden"
          src={assetUrl(activeAudio.url)}
          autoPlay={active && (!showingDirections || audioMode === "full")}
          preload="metadata"
          onLoadedMetadata={(event) => {
            const duration = event.currentTarget.duration;
            setMediaDuration(
              Number.isFinite(duration) && duration > 0 ? duration : 0,
            );
          }}
          onTimeUpdate={handleTimeUpdate}
          onEnded={handleEnded}
          onPlay={() => {
            setPlaybackError(null);
          }}
          aria-hidden="true"
        />
      )}
    </div>
  );
}
