import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export function ResultChart({
  data,
}: {
  data: { day: string; gmv: number; lastWeek: number }[];
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: { trigger: "axis" },
      legend: { data: ["本周", "上周"], top: 0 },
      grid: { left: 60, right: 16, top: 32, bottom: 28 },
      xAxis: { type: "category", data: data.map((item) => item.day) },
      yAxis: { type: "value" },
      series: [
        {
          name: "本周",
          type: "bar",
          data: data.map((item) => item.gmv),
          itemStyle: { color: "#3b7dd8" },
        },
        {
          name: "上周",
          type: "line",
          smooth: true,
          data: data.map((item) => item.lastWeek),
          itemStyle: { color: "#c2c8d4" },
        },
      ],
    });
    return () => chart.dispose();
  }, [data]);

  return <div ref={ref} className="chart" />;
}
