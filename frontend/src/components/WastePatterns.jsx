import React, { useMemo } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Tooltip,
  Legend
} from "chart.js";
import { Bar } from "react-chartjs-2";
import { CATEGORY_META } from "../categories";
import { prettyLabel } from "../utils";

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend);

const peach = "#f4a574";
const charcoal = "#2c2a28";

function categoryLabel(category) {
  if (!category) return CATEGORY_META.unknown.label;
  return CATEGORY_META[category]?.label || prettyLabel(category);
}

function topCategory(rows) {
  if (!rows?.length) return null;
  return [...rows].sort((a, b) => b.events - a.events)[0];
}

export default function WastePatterns({ report }) {
  const chart = useMemo(() => {
    if (!report) return null;
    const used = report.consumed_by_category || [];
    const wasted = report.by_category || [];
    const keys = [
      ...new Set([
        ...used.map((row) => row.category ?? ""),
        ...wasted.map((row) => row.category ?? "")
      ])
    ];
    if (!keys.length) return null;
    const usedByKey = Object.fromEntries(
      used.map((row) => [row.category ?? "", row.events])
    );
    const wastedByKey = Object.fromEntries(
      wasted.map((row) => [row.category ?? "", row.events])
    );
    return {
      labels: keys.map((key) => categoryLabel(key || null)),
      datasets: [
        {
          label: "Used",
          data: keys.map((key) => usedByKey[key] || 0),
          backgroundColor: peach,
          borderRadius: 6
        },
        {
          label: "Wasted",
          data: keys.map((key) => wastedByKey[key] || 0),
          backgroundColor: charcoal,
          borderRadius: 6
        }
      ]
    };
  }, [report]);

  if (!report) return null;

  const usedEvents = report.consumed?.events || 0;
  const wastedEvents = report.wasted?.events || 0;
  const total = usedEvents + wastedEvents;
  const usedTop = topCategory(report.consumed_by_category);
  const wastedTop = topCategory(report.by_category);
  const rate = Math.round((report.waste_rate || 0) * 100);

  return (
    <section className="card">
      <h2>Use and waste</h2>
      {total === 0 ? (
        <p className="hint">
          Mark items Used or Wasted to see which aisles you eat versus throw
          out. Last {report.window_days} days, counted per log — not mixed
          into one fake total of grams and litres.
        </p>
      ) : (
        <>
          <p className="hint">
            Last {report.window_days} days: {usedEvents} used, {wastedEvents}{" "}
            wasted ({rate}% of logs were waste)
            {report.wasted_after_expiry
              ? ` · ${report.wasted_after_expiry} already expired`
              : ""}
            .
            {usedTop
              ? ` You used ${categoryLabel(usedTop.category)} most often.`
              : ""}
            {wastedTop
              ? ` You wasted ${categoryLabel(wastedTop.category)} most often.`
              : ""}
          </p>
          {chart && (
            <div className="waste-chart">
              <Bar
                data={chart}
                options={{
                  responsive: true,
                  plugins: { legend: { position: "bottom" } },
                  scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
                }}
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}
