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
        // Core tailwind overrides to fit BMW M Canvas/Foreground theme by default
        background: "#000000",
        foreground: "#ffffff",
        card: "#1a1a1a",
        "card-foreground": "#bbbbbb",
        primary: "#ffffff",
        "primary-foreground": "#000000",
        border: "#3c3c3c",
        ring: "#ffffff",
        
        // BMW M specific tokens
        canvas: "#000000",
        "surface-soft": "#0d0d0d",
        "surface-card": "#1a1a1a",
        "surface-elevated": "#262626",
        "carbon-gray": "#2b2b2b",
        hairline: "#3c3c3c",
        "hairline-strong": "#262626",
        "on-dark": "#ffffff",
        "body-text": "#bbbbbb",
        "body-strong": "#e6e6e6",
        muted: "#7e7e7e",
        
        // BMW M Tri-color Stripe & Electric Accents
        "m-blue-light": "#0066b1",
        "m-blue-dark": "#1c69d4",
        "m-red": "#e22718",
        "electric-blue": "#0653b6",
        
        // Semantic Accents
        warning: "#f4b400",
        success: "#0fa336",
      },
      spacing: {
        xxs: "4px",
        xs: "8px",
        sm: "12px",
        md: "16px",
        lg: "24px",
        xl: "40px",
        xxl: "64px",
        section: "96px",
      },
      borderRadius: {
        none: "0px",
        xs: "2px",
        sm: "4px",
        md: "6px",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "sans-serif"],
        display: ["var(--font-saira-condensed)", "sans-serif"],
      },
      letterSpacing: {
        machined: "1.5px",
      },
      boxShadow: {
        glow: "0 0 15px rgba(255, 255, 255, 0.2)",
        "glow-success": "0 0 15px rgba(15, 163, 54, 0.3)",
        "glow-error": "0 0 15px rgba(226, 39, 24, 0.3)",
      }
    },
  },
  plugins: [],
};
export default config;
