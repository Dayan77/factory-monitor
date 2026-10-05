interface Props {
  value: number; // 0–100
  size?: "sm" | "md" | "lg";
  label?: string;
}

const SIZE = { sm: 80, md: 120, lg: 160 };
const STROKE = { sm: 8, md: 10, lg: 12 };

export default function OeeGauge({ value, size = "md", label }: Props) {
  const px = SIZE[size];
  const stroke = STROKE[size];
  const r = (px - stroke) / 2;
  const circ = 2 * Math.PI * r;
  // Only draw 270° (¾ circle). offset starts at bottom-left.
  const arc = circ * 0.75;
  const filled = arc * (Math.min(100, Math.max(0, value)) / 100);
  const dash = `${filled} ${circ}`;

  const color =
    value >= 85 ? "rgb(var(--c-ok-solid))"
    : value >= 65 ? "rgb(var(--c-warn-solid))"
    : "rgb(var(--c-danger-solid))";

  const fontSize = size === "lg" ? 28 : size === "md" ? 22 : 16;

  return (
    <div className="flex flex-col items-center gap-1.5">
      <svg
        width={px}
        height={px}
        viewBox={`0 0 ${px} ${px}`}
        style={{ transform: "rotate(135deg)" }}
      >
        {/* Track */}
        <circle
          cx={px / 2} cy={px / 2} r={r}
          fill="none"
          stroke="rgb(var(--gray-800))"
          strokeWidth={stroke}
          strokeDasharray={`${arc} ${circ}`}
          strokeLinecap="round"
        />
        {/* Fill */}
        <circle
          cx={px / 2} cy={px / 2} r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeDasharray={dash}
          strokeLinecap="round"
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
      </svg>
      {/* Value overlay */}
      <div
        className="absolute flex flex-col items-center"
        style={{
          marginTop: -(px / 2 + fontSize / 2 + 4),
          position: "relative",
          top: -(px * 0.55),
        }}
      >
        <span
          className="font-bold leading-none"
          style={{ fontSize, color }}
        >
          {value.toFixed(0)}%
        </span>
      </div>
      {label && <p className="text-xs text-gray-500 -mt-1">{label}</p>}
    </div>
  );
}
