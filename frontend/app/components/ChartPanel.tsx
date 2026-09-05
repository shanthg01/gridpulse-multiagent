"use client";

import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartSpec } from "../lib/types";

const PIE_COLORS = [
  "#2563eb",
  "#16a34a",
  "#d97706",
  "#dc2626",
  "#7c3aed",
  "#0891b2",
  "#be185d",
];

function formatTick(value: unknown): string {
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatFullDate(value: unknown): string {
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString();
}

/** Adapts our minimal internal chart_spec shape (agents/timeseries_agent.py
 * `_summarize()`: {mark: "line"|"arc", encoding, data.values}) into Recharts
 * props. "line" -> time-series line chart, "arc" -> pie/donut share chart. */
export function ChartPanel({ spec }: { spec: ChartSpec }) {
  const values = spec.data?.values ?? [];
  if (values.length === 0) {
    return <p className="text-sm text-zinc-500">No chart data.</p>;
  }

  if (spec.mark === "line") {
    const xField = spec.encoding.x?.field ?? "ts";
    const yField = spec.encoding.y?.field ?? "mwh";
    return (
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={values} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
          <XAxis
            dataKey={xField}
            tickFormatter={formatTick}
            minTickGap={40}
            tick={{ fontSize: 12 }}
          />
          <YAxis tick={{ fontSize: 12 }} width={70} />
          <Tooltip
            labelFormatter={formatFullDate}
            formatter={(value) => [`${Number(value).toLocaleString()} MWh`, yField]}
          />
          <Line type="monotone" dataKey={yField} stroke="#2563eb" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    );
  }

  if (spec.mark === "arc") {
    const thetaField = spec.encoding.theta?.field ?? "mwh";
    const colorField = spec.encoding.color?.field ?? "fuel_type";
    return (
      <ResponsiveContainer width="100%" height={320}>
        <PieChart>
          <Pie
            data={values}
            dataKey={thetaField}
            nameKey={colorField}
            innerRadius={60}
            outerRadius={110}
            paddingAngle={2}
            label={(props) => String((props as unknown as Record<string, unknown>)[colorField] ?? "")}
          >
            {values.map((_, i) => (
              <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip formatter={(value) => `${Number(value).toLocaleString()} MWh`} />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  return <p className="text-sm text-zinc-500">Unsupported chart mark: {spec.mark}</p>;
}
