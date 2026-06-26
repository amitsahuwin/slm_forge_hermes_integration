import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Arial', '"Helvetica Neue"', 'Helvetica', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        hcl: {
          'dark-teal': '#17707F',
          'teal': '#2EC0CB',
          'mid-teal': '#36D6D9',
          'light-teal': '#AAFFFF',
          'dark-blue': '#0F5FDC',
          'tech-blue': '#3C91FF',
          'mid-blue': '#8AC6F8',
          'light-blue': '#DCE6F0',
          'software-blue': '#000032',
          'tech-grey': '#ECF3F8',
          'dark': '#14142B',
          'bg': '#F7F7FC',
          'bg-alt': '#F4F4F4',
          'navy': '#0B1D3A',
          'success': '#00C3CD',
          'warning': '#F5A623',
          'error': '#DC3545',
          'info': '#3C91FF',
        },
      },
      backgroundImage: {
        'hcl-gradient': 'linear-gradient(135deg, #0B1D3A 0%, #17707F 45%, #2EC0CB 85%, #36D6D9 100%)',
        'hcl-gradient-dark': 'linear-gradient(135deg, #0B1D3A 0%, #17707F 40%, #2EC0CB 70%, #36D6D9 100%)',
      },
    },
  },
  plugins: [],
} satisfies Config;
