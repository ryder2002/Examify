import Image from "next/image";

type ExamifyLoaderProps = {
  message?: string;
  fullScreen?: boolean;
};

/** Branded loading state shared by route and data loading screens. */
export default function ExamifyLoader({
  message = "Đang tải...",
  fullScreen = true,
}: ExamifyLoaderProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={`${
        fullScreen ? "min-h-screen bg-slate-50" : "min-h-48 bg-transparent"
      } flex items-center justify-center px-6 py-10 text-[#1f4e79]`}
    >
      <div className="flex flex-col items-center text-center">
        <div className="relative flex w-full justify-center shrink-0">
          <Image
            src="/logo.png"
            alt="Examify Logo"
            width={512}
            height={512}
            priority
            unoptimized
            className="h-16 sm:h-20 w-auto object-contain shrink-0"
          />
        </div>
        <div className="mt-4 flex items-center gap-2 text-sm font-extrabold tracking-[0.18em] text-[#1f4e79]">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#b9d1e1] border-t-[#1f4e79]" />
          Examify
        </div>
        <p className="mt-2 text-sm font-medium text-slate-500">{message}</p>
      </div>
    </div>
  );
}
