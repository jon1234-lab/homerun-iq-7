import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        diamond: { 950: "#05070f", 900: "#0a0e1a", 800: "#111827" },
      },
    },
  },
  plugins: [],
};
export default config;
