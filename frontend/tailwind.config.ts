import type { Config } from "tailwindcss";
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./features/**/*.{ts,tsx}"],
  theme: { extend: { colors: { ink: "#0f1115", panel: "#171a21", line: "#262b36", accent: "#7c5cff", accent2: "#22d3a6" },
    fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] } } },
  plugins: [],
} satisfies Config;
