/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Schibsted Grotesk Variable"', "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "Consolas", "monospace"],
      },
      colors: {
        mist: "#EEF1F5",
        ink: "#172033",
        slate2: "#5B667A",
        rule: "#D6DCE5",
        signal: "#3347D8",
        ok: "#17795A",
        warn: "#B5710B",
        bad: "#B3362E",
      },
    },
  },
  plugins: [],
};
