import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "hsl(224, 71%, 4%)",
        foreground: "hsl(210, 40%, 98%)",
        card: "hsl(224, 71%, 7%)",
        "card-foreground": "hsl(210, 40%, 98%)",
        primary: "hsl(263.4, 70%, 50.4%)", // Slate violet/purple
        "primary-foreground": "hsl(210, 40%, 98%)",
        border: "hsl(217.2, 32.6%, 17.5%)",
        ring: "hsl(263.4, 70%, 50.4%)",
      },
      boxShadow: {
        glow: "0 0 15px rgba(124, 58, 237, 0.4)",
        "glow-success": "0 0 15px rgba(16, 185, 129, 0.4)",
        "glow-error": "0 0 15px rgba(239, 68, 68, 0.4)",
      }
    },
  },
  plugins: [],
};
export default config;
