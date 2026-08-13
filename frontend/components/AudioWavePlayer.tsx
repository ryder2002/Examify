"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, Volume2 } from "lucide-react";

import { assetUrl } from "@/lib/api";
import type { AudioRef } from "@/lib/utils";

function formatTime(value: number) {
  if (!Number.isFinite(value) || value < 0) return "00:00";
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60);
  return `${minutes.toString().padStart(2, "0")}:${seconds
    .toString()
    .padStart(2, "0")}`;
}

export default function AudioWavePlayer({
  audio,
  autoPlay = false,
  onEnded,
}: {
  audio: AudioRef;
  autoPlay?: boolean;
  onEnded?: () => void;
}) {
  const element = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const source = assetUrl(audio.url);
  const bars = useMemo(
    () =>
      Array.from({ length: 48 }, (_, index) => {
        const wave = Math.sin(index * 1.71) * 0.5 + Math.sin(index * 0.47) * 0.35;
        return Math.round(24 + Math.abs(wave) * 58);
      }),
    [],
  );
  const progress = duration > 0 ? currentTime / duration : 0;

  useEffect(() => {
    setPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    const audioElement = element.current;
    if (autoPlay && audioElement) {
      void audioElement.play().catch(() => undefined);
    }
  }, [autoPlay, source]);

  async function toggle() {
    const audioElement = element.current;
    if (!audioElement) return;
    if (audioElement.paused) await audioElement.play();
    else audioElement.pause();
  }

  return (
    <div className="flex w-full min-w-0 items-center gap-3 rounded-2xl border border-slate-300 bg-gradient-to-r from-white to-slate-50 px-3 py-2 shadow-sm lg:min-w-[520px]">
      <audio
        ref={element}
        src={source}
        preload="metadata"
        autoPlay={autoPlay}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          if (onEnded) onEnded();
        }}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
      />
      <button
        type="button"
        onClick={() => void toggle()}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#1f4e79] text-white shadow-md transition hover:scale-105 hover:bg-[#173a5c]"
        aria-label={playing ? "Tạm dừng audio" : "Phát audio"}
      >
        {playing ? <Pause className="h-5 w-5 fill-current" /> : <Play className="ml-0.5 h-5 w-5 fill-current" />}
      </button>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center justify-between gap-3">
          <span className="flex min-w-0 items-center gap-1.5 truncate text-[11px] font-extrabold uppercase tracking-wider text-[#1f4e79]">
            <Volume2 className="h-3.5 w-3.5 shrink-0" />
            {audio.part === "full"
              ? "Audio Full"
              : audio.part.replace("part_", "Part ")}
          </span>
          <span className="shrink-0 font-mono text-[11px] font-bold text-slate-500">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>
        <div className="relative h-8">
          <div className="absolute inset-0 flex items-center gap-[2px] overflow-hidden rounded-lg">
            {bars.map((height, index) => (
              <span
                key={index}
                className={`min-w-[2px] flex-1 rounded-full transition-colors ${
                  index / bars.length <= progress ? "bg-[#0068ff]" : "bg-slate-300"
                }`}
                style={{ height: `${height}%` }}
              />
            ))}
          </div>
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={Math.min(currentTime, duration || 0)}
            onChange={(event) => {
              const next = Number(event.target.value);
              if (element.current) element.current.currentTime = next;
              setCurrentTime(next);
            }}
            className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            aria-label="Tiến độ audio"
          />
        </div>
      </div>
    </div>
  );
}
