/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0a0a0f", surface: "#111118", surface2: "#0d0d14", side: "#0d0d14", line: "#1e1e2e",
        brand: { DEFAULT: "#7c3aed", soft: "#a78bfa" },
        up: "#22c55e", down: "#ef4444", warn: "#f59e0b",
        ink: "#f1f5f9", mute: "#64748b",
      },
      borderRadius: { card: "12px", input: "8px" },
      boxShadow: { glow: "0 0 20px rgba(124,58,237,0.3)", glowlg: "0 0 32px rgba(124,58,237,0.45)" },
      transitionDuration: { DEFAULT: "150ms" },
      keyframes: {
        pulseSlow: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.35 } },
        shimmer: { "0%,100%": { opacity: 0.55 }, "50%": { opacity: 0.25 } },
      },
      animation: { "pulse-slow": "pulseSlow 2.4s ease-in-out infinite", shimmer: "shimmer 1.4s ease-in-out infinite" },
    },
  },
  plugins: [],
};
