/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        ink: { 950: "#09090b", 900: "#0c0c0f", 850: "#111116", 800: "#1e1e24" },
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(56,189,248,.25), 0 8px 30px -8px rgba(56,189,248,.35)",
        "glow-emerald": "0 0 0 1px rgba(16,185,129,.3), 0 8px 30px -8px rgba(16,185,129,.45)",
      },
      keyframes: {
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
        pulseDot: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.35 } },
      },
      animation: {
        shimmer: "shimmer 2.5s linear infinite",
        "pulse-dot": "pulseDot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
