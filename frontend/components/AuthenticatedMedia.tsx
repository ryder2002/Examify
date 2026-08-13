"use client";

import {
  useEffect,
  useState,
  type AudioHTMLAttributes,
  type ImgHTMLAttributes,
} from "react";

import { apiFetch, assetUrl } from "@/lib/api";

type MediaState = {
  url: string;
  loading: boolean;
  error: string | null;
};

function isPrivateApiUrl(source: string) {
  return (
    source.startsWith("/api/extractions/") ||
    source.startsWith("/api/desktop/")
  );
}

/**
 * Browser image/audio elements cannot attach Examify's device-auth header.
 * Fetch private media through the normal authenticated API client and expose
 * only a short-lived in-memory blob URL to the element.
 */
export function useAuthenticatedMediaUrl(source: string): MediaState {
  const [state, setState] = useState<MediaState>({
    url: isPrivateApiUrl(source) ? "" : assetUrl(source),
    loading: isPrivateApiUrl(source),
    error: null,
  });

  useEffect(() => {
    if (!isPrivateApiUrl(source)) {
      setState({ url: assetUrl(source), loading: false, error: null });
      return;
    }

    const controller = new AbortController();
    let objectUrl = "";
    setState({ url: "", loading: true, error: null });

    apiFetch(source, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || "Không tải được tệp riêng tư");
        }
        return response.blob();
      })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ url: objectUrl, loading: false, error: null });
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setState({
          url: "",
          loading: false,
          error: reason instanceof Error ? reason.message : "Không tải được tệp",
        });
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source]);

  return state;
}

type AuthenticatedImageProps = Omit<
  ImgHTMLAttributes<HTMLImageElement>,
  "src"
> & {
  source: string;
};

export function AuthenticatedImage({
  source,
  className = "",
  alt = "",
  ...props
}: AuthenticatedImageProps) {
  const media = useAuthenticatedMediaUrl(source);

  if (!media.url) {
    return (
      <div
        role="img"
        aria-label={alt}
        className={`${className} flex items-center justify-center bg-slate-100 px-4 text-center text-xs text-slate-500`}
      >
        {media.error || "Đang tải ảnh..."}
      </div>
    );
  }

  // A normal img is intentional: the source is an in-memory authenticated blob.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={media.url} alt={alt} className={className} {...props} />;
}

type AuthenticatedAudioProps = Omit<
  AudioHTMLAttributes<HTMLAudioElement>,
  "src"
> & {
  source: string;
};

export function AuthenticatedAudio({
  source,
  className = "",
  ...props
}: AuthenticatedAudioProps) {
  const media = useAuthenticatedMediaUrl(source);

  if (!media.url) {
    return (
      <div
        className={`${className} flex items-center text-[11px] text-slate-500`}
      >
        {media.error || "Đang tải audio..."}
      </div>
    );
  }

  return <audio src={media.url} className={className} {...props} />;
}
