import { forwardRef, type ButtonHTMLAttributes } from "react";
import clsx from "clsx";
import { useAuthStore } from "@/store/auth";
import { DEMO_WRITE_MSG, isDemoUser } from "@/utils/demo";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost";
type Size = "sm" | "md" | "lg";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  /**
   * Public demo (sprint6 §5): primary/danger and submit buttons are write actions and are disabled with the demo
   * tooltip. `demoSafe` marks a read-only action (filters, print, the AI question) that stays enabled.
   */
  demoSafe?: boolean;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover",
  secondary: "border border-line-strong bg-surface text-fg hover:bg-surface-2",
  ghost: "text-fg-muted hover:bg-surface-2 hover:text-fg",
  danger: "bg-danger-solid text-white hover:bg-red-700",
  "danger-ghost": "text-danger hover:bg-danger-solid/10",
};

const SIZES: Record<Size, string> = {
  sm: "min-h-8 px-3 text-caption",
  md: "min-h-10 px-4 text-body",
  lg: "min-h-12 px-5 text-body",
};

/** Minimum 40px tall (44px at lg) — usable on a tablet with gloves. */
const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "secondary", size = "md", loading, disabled, className, children, type = "button", demoSafe, title, ...rest },
  ref,
) {
  const demo = useAuthStore((s) => isDemoUser(s.user));
  const demoBlocked = demo && !demoSafe && (variant === "primary" || variant === "danger" || type === "submit");
  return (
    <button
      ref={ref}
      type={type}
      title={demoBlocked ? DEMO_WRITE_MSG : title}
      data-demo-disabled={demoBlocked || undefined}
      disabled={disabled || loading || demoBlocked}
      aria-busy={loading || undefined}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading && <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
});

export default Button;
