import React, { useMemo } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Tooltip,
  Legend,
  Filler
} from "chart.js";
import { Bar, Doughnut, Line } from "react-chartjs-2";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Tooltip,
  Legend,
  Filler
);

const peach = "#f4a574";
const charcoal = "#2c2a28";
const cream = "#f7f1e8";

export default function DietCharts({ progress }) {
  const calorieData = useMemo(() => {
    const days = progress?.days || [];
    return {
      labels: days.map((day) => day.date.slice(5)),
      datasets: [
        {
          label: "Intake",
          data: days.map((day) => day.intake),
          backgroundColor: peach,
          borderRadius: 6
        },
        {
          label: "Target",
          data: days.map((day) => day.target ?? null),
          backgroundColor: "rgba(44, 42, 40, 0.25)",
          borderRadius: 6
        }
      ]
    };
  }, [progress]);

  const macroData = useMemo(() => {
    const protein = progress?.protein_g || 0;
    const carbs = progress?.carbs_g || 0;
    const fat = progress?.fat_g || 0;
    return {
      labels: ["Protein", "Carbs", "Fat"],
      datasets: [
        {
          data: [protein, carbs, fat],
          backgroundColor: [peach, "#d4a373", charcoal],
          borderWidth: 0
        }
      ]
    };
  }, [progress]);

  const weightData = useMemo(() => {
    const rows = progress?.weigh_ins || [];
    return {
      labels: rows.map((row) => row.logged_date.slice(5)),
      datasets: [
        {
          label: "Weight kg",
          data: rows.map((row) => row.weight_kg),
          borderColor: charcoal,
          backgroundColor: "rgba(244, 165, 116, 0.25)",
          fill: true,
          tension: 0.25
        }
      ]
    };
  }, [progress]);

  if (!progress?.days?.length) return null;

  const hasMacros =
    (progress.protein_g || 0) + (progress.carbs_g || 0) + (progress.fat_g || 0) > 0;
  const hasWeight = (progress.weigh_ins || []).length > 0;

  return (
    <section className="card diet-charts">
      <h3>Progress charts</h3>
      <p className="hint">
        Estimates from logged meals and extras. Not lab measurements.
      </p>
      <div className="diet-chart-grid">
        <div className="diet-chart">
          <h4>Daily kcal vs target</h4>
          <Bar
            data={calorieData}
            options={{
              responsive: true,
              plugins: { legend: { position: "bottom" } },
              scales: { y: { beginAtZero: true } }
            }}
          />
        </div>
        {hasMacros && (
          <div className="diet-chart">
            <h4>Macro split ({progress.window_days}d)</h4>
            <Doughnut
              data={macroData}
              options={{
                responsive: true,
                plugins: { legend: { position: "bottom" } }
              }}
            />
          </div>
        )}
        {hasWeight && (
          <div className="diet-chart">
            <h4>Weight toward goal</h4>
            <Line
              data={weightData}
              options={{
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: false } }
              }}
            />
            {progress.target_weight_kg != null && (
              <p className="hint" style={{ background: cream }}>
                Target {progress.target_weight_kg} kg
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
