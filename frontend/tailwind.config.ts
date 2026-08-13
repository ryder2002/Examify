import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f3f6fb",
          100: "#e8eef7",
          200: "#cbd8e8",
          300: "#9db3ce",
          400: "#5f7ea4",
          500: "#2c4f7a",
          600: "#1e3a5f",
          700: "#1f4e79",
          800: "#173a5c",
          900: "#040d1a",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
