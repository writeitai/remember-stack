import Image from "next/image";
import { cn } from "@/lib/utils";

type BrandLockupProps = {
  className?: string;
  compact?: boolean;
  priority?: boolean;
};

/**
 * Render the canonical RememberStack graph mark and OSS documentation lockup.
 */
export function BrandLockup({
  className,
  compact = false,
  priority = false,
}: BrandLockupProps) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <Image
        src="/brand/mark.svg"
        width={800}
        height={680}
        alt=""
        aria-hidden
        priority={priority}
        className="h-[34px] w-auto shrink-0"
      />
      <span className="flex min-w-0 flex-col gap-1">
        <span className="font-display text-[17px] font-bold leading-none tracking-tight text-ink sm:text-[18px]">
          RememberStack
        </span>
        {!compact && (
          <span className="flex items-center gap-2 font-mono text-[9px] font-medium uppercase leading-none tracking-[0.18em] text-ink-2 sm:text-[10px]">
            <span
              className="inline-block h-[1.5px] w-3 shrink-0 bg-coral"
              aria-hidden
            />
            docs.remember.dev
          </span>
        )}
      </span>
    </span>
  );
}
