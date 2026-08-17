import React from "react";

/** Semicircle gauge: intake vs daily target. */
export default function CalorieGauge({ intake = 0, target = 2000 }) {
  const safeTarget = target > 0 ? target : 2000;
  const ratio = Math.min(Math.max(intake / safeTarget, 0), 1.2);
  const filled = Math.min(ratio, 1);
  const radius = 90;
  const stroke = 14;
  const cx = 110;
  const cy = 110;
  const startAngle = Math.PI;
  const endAngle = 0;
  const arc = (t) => {
    const angle = startAngle + (endAngle - startAngle) * t;
    return {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle)
    };
  };
  const start = arc(0);
  const mid = arc(filled);
  const end = arc(1);
  const large = filled > 0.5 ? 1 : 0;
  const track = `M ${start.x} ${start.y} A ${radius} ${radius} 0 1 1 ${end.x} ${end.y}`;
  const progress =
    filled <= 0
      ? ""
      : `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${large} 1 ${mid.x} ${mid.y}`;

  const today = new Date().toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric"
  });

  return (
    <div className="calorie-gauge">
      <svg viewBox="0 0 220 130" className="calorie-gauge-svg" aria-hidden="true">
        <path
          d={track}
          fill="none"
          stroke="var(--cream-deep)"
          strokeWidth={stroke}
          strokeLinecap="round"
        />
        {progress ? (
          <path
            d={progress}
            fill="none"
            stroke="var(--peach)"
            strokeWidth={stroke}
            strokeLinecap="round"
          />
        ) : null}
      </svg>
      <div className="calorie-gauge-label">
        <p className="hint">{today}</p>
        <p className="calorie-gauge-value">{Math.round(intake)} kcal</p>
        <p className="calorie-gauge-goal">Goal {Math.round(safeTarget)} kcal</p>
      </div>
    </div>
  );
}
