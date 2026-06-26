# HCL Software Brand Guidelines

This document contains the official HCL Software (HCLSW) brand identity extracted from the corporate brand kit. Apply these guidelines to every visual output — presentations, documents, dashboards, charts, spreadsheets, HTML pages, and React components.

---

## Color Palette

### Primary Colors

| Role | Name | Hex | RGB | Usage |
|------|------|-----|-----|-------|
| **Primary Teal** | Dark Teal | `#17707F` | rgb(23, 112, 127) | Key headings, primary buttons, chart accents, hero elements |
| **Secondary Teal** | Software Teal | `#2EC0CB` | rgb(46, 192, 203) | Highlights, links, secondary buttons, data visualization accents |

### Secondary Colors

| Role | Name | Hex | RGB | Usage |
|------|------|-----|-----|-------|
| **Light Teal** | Mid Teal | `#36D6D9` | rgb(54, 214, 217) | Hover states, borders, lighter chart elements |
| **Very Light Teal** | Light Teal | `#AAFFFF` | rgb(170, 255, 255) | Subtle backgrounds, highlights, glow effects |
| **Dark Blue** | Dark Blue | `#0F5FDC` | rgb(15, 95, 220) | Gradients, primary data series, accent elements |
| **Medium Blue** | Tech Blue | `#3C91FF` | rgb(60, 145, 255) | Secondary data series, info callouts |
| **Light Blue** | Mid Blue | `#8AC6F8` | rgb(138, 198, 248) | Backgrounds, soft callouts |
| **Ice Blue** | Light Blue | `#DCE6F0` | rgb(220, 230, 240) | Table headers, card backgrounds, section dividers |
| **Navy Blue** | Software Blue | `#000032` | rgb(0, 0, 50) | Deep contrast elements |
| **Pale Grey** | Tech Grey | `#ECF3F8` | rgb(236, 243, 248) | Subtle backgrounds and fills |

### Neutrals

| Role | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Black** | `#000000` | rgb(0, 0, 0) | Primary text, logo on light backgrounds |
| **Dark Text** | `#14142B` | rgb(20, 20, 43) | Body text (slightly softer than pure black) |
| **White** | `#FFFFFF` | rgb(255, 255, 255) | Backgrounds, logo on dark backgrounds, reversed text |
| **Light BG 1** | `#F7F7FC` | rgb(247, 247, 252) | Page backgrounds, subtle grey tint |
| **Light BG 2** | `#F4F4F4` | rgb(244, 244, 244) | Alternating row backgrounds, card surfaces |

### Brand Gradient

The signature HCLSW gradient flows from dark navy (top-left) through Dark Teal to bright teal (bottom-right). Use on title slides, social media, and hero sections.

```css
background: linear-gradient(135deg, #0B1D3A 0%, #17707F 45%, #2EC0CB 85%, #36D6D9 100%);
```

---

## Typography

| Element | Font | Weight | Size Guidance |
|---------|------|--------|---------------|
| **H1** | Arial | Bold (700) | 36pt (slides) / 28pt (docs) / 2.25rem (web) |
| **H2** | Arial | Bold (700) | 25pt (slides) / 22pt (docs) / 1.75rem (web) |
| **Subheadings** | Arial | Semi-Bold (600) | 18pt (slides) / 16pt (docs) / 1.25rem (web) |
| **Body** | Arial | Regular (400) | 14pt (slides) / 11pt (docs) / 1rem (web) |
| **Captions** | Arial | Regular (400) | 9pt (slides) / 9pt (docs) / 0.875rem (web) |

Arial is the universal brand font. Fallback stack:

```css
font-family: Arial, "Helvetica Neue", Helvetica, sans-serif;
```

---

## Logo Usage

### Logo Selection Guide

| Context | Logo File |
|---------|-----------|
| Light backgrounds | `hclsw-logo-horizontal-black.svg` |
| Dark/gradient backgrounds | `hclsw-logo-horizontal-white.svg` |
| Narrow spaces | `hclsw-logo-vertical-black.svg` or `-white.svg` |
| Print (300dpi) | `hclsw-logo-horizontal-black-300dpi.png` |
| Screen (72dpi) | `hclsw-logo-horizontal-black-72dpi.png` |
| Social / thumbnail | `hclsw-social-avatar.jpg` |

### Logo Placement Rules

- Place in the **top-left** or **bottom-left** of documents and slides
- Maintain clear space equal to the height of the "H" in "HCL"
- **Never** stretch, distort, rotate, recolor, or add effects to the logo
- On gradient/dark backgrounds, always use the **white** logo variant

### Logo Aspect Ratio (Critical)

The horizontal logo has a native **8:1** aspect ratio (width ÷ height = 8). This must be preserved in all contexts.

| Context | Width | Height |
|---------|-------|--------|
| Word doc headers | 200px | 25px |
| Email signatures | 150px | 18.75px |
| Slide top-left | 180px | 22.5px |
| Web page header | 240px | 30px |

**Formula:** `height = width ÷ 8`

❌ Never set width and height independently  
✅ Always calculate one from the other using the 8:1 ratio

---

## CSS Variables (HTML / Web)

```css
:root {
  --hcl-dark-teal:     #17707F;
  --hcl-software-teal: #2EC0CB;
  --hcl-mid-teal:      #36D6D9;
  --hcl-light-teal:    #AAFFFF;
  --hcl-dark-blue:     #0F5FDC;
  --hcl-tech-blue:     #3C91FF;
  --hcl-mid-blue:      #8AC6F8;
  --hcl-light-blue:    #DCE6F0;
  --hcl-software-blue: #000032;
  --hcl-tech-grey:     #ECF3F8;
  --hcl-dark:          #14142B;
  --hcl-bg:            #F7F7FC;
  --hcl-bg-alt:        #F4F4F4;
  --hcl-navy:          #0B1D3A;
  --hcl-gradient: linear-gradient(135deg, #0B1D3A 0%, #17707F 45%, #2EC0CB 85%, #36D6D9 100%);
  --font-main: Arial, "Helvetica Neue", Helvetica, sans-serif;
}
```

---

## Chart & Data Visualization Colors

Use in this order for consistent brand sequencing:

1. `#17707F` — Dark Teal *(primary series)*
2. `#2EC0CB` — Software Teal
3. `#36D6D9` — Mid Teal
4. `#AAFFFF` — Light Teal
5. `#0F5FDC` — Dark Blue
6. `#3C91FF` — Tech Blue
7. `#8AC6F8` — Mid Blue
8. `#DCE6F0` — Light Blue

**Semantic / status colors:**

| Status | Color |
|--------|-------|
| Success | `#00C3CD` (teal) |
| Warning | `#F5A623` (amber) |
| Error | `#DC3545` (red) |
| Info | `#3C91FF` (blue) |

---

## Output-Specific Guidelines

### HTML Dashboards & Web Pages
- Gradient for headers and hero banners; white text on dark backgrounds
- Cards: white bg, `1px solid #DCE6F0` border, `border-radius: 8px`
- Table headers: `#0F5FDC` bg + white text, or `#DCE6F0` bg + dark text
- Alternating rows: `#FFFFFF` / `#F7F7FC`

### React (JSX)
Use the CSS variables above, or the brand color array for chart libraries:

```javascript
const HCLSW_COLORS = ['#17707F','#2EC0CB','#36D6D9','#AAFFFF','#0F5FDC','#3C91FF','#8AC6F8','#DCE6F0'];
```

### PowerPoint (PPTX)
- Title slides: gradient bg + white logo + white text
- Content slides: white bg, black body text, teal headings
- Font: Arial throughout, no exceptions
- Use the POTX template at `assets/HCLSW_TEMPLATE.pptx`

### Word Documents (DOCX)
- Headings: Arial Bold, `#17707F` or `#2EC0CB`
- Body: Arial Regular 11pt, `#000000` or `#14142B`
- Table headers: `#17707F` bg + white text
- Alternating rows: white / `#F7F7FC`
- Cover page: gradient bg + white text + white logo

### Excel Spreadsheets (XLSX)
- Header row: `#17707F` fill, white bold text
- Sub-headers: `#ECF3F8` fill, black text
- Alternating rows: white / `#F7F7FC`
- Totals/accent rows: `#2EC0CB` fill, white text
- Borders: thin, `#DCE6F0`

### PDF Reports
Follow Word document rules. Use 300dpi PNG logo for cover pages.

---

## Do's and Don'ts

**Do:**
- Always use Arial (or its fallbacks)
- Use Dark Teal and Software Teal as the core visual identity
- Apply the gradient to hero/title sections
- Keep plenty of white space — the brand is clean and modern
- White logo on dark backgrounds; black logo on light backgrounds

**Don't:**
- Use off-brand colors as primary colors
- Stretch, distort, or disproportionately size the logo (always maintain 8:1 ratio)
- Rotate, recolor, or add effects to the logo
- Use heavy drop shadows or 3D effects (brand aesthetic is flat and modern)
- Mix more than 2–3 accent colors per section
- Use any font other than Arial and its specified fallbacks
