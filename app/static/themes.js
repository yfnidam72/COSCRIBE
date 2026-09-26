"use strict";
/* Built-in caption themes. Each `s` is merged over DEFAULT_STYLE, so it only lists what
   it changes. `show` (original / translation / both) is never part of a theme: it's a
   per-project choice. Every theme renders through the same preview + libass path. */
const THEME_CATS = ["catEssentials", "catSocial", "catCinema", "catArabic", "catBold"];

const BUILTIN_THEMES = [
  // ---------------------------------------------------------------- essentials
  { id: "classic", cat: "catEssentials", name: "Classic box",
    s: { font: "Cairo", bold: true, color: "#FFFFFF", bg: "box", bgColor: "#000000", bgOpacity: 70, pad: 0.3, size: 6, position: "bottom", offset: 8 } },
  { id: "clean", cat: "catEssentials", name: "Clean",
    s: { font: "IBM Plex Sans Arabic", bold: true, color: "#FFFFFF", bg: "shadow", bgColor: "#000000", bgOpacity: 75, size: 5.6, position: "bottom", offset: 7 } },
  { id: "paper", cat: "catEssentials", name: "Paper",
    s: { font: "Tajawal", bold: true, color: "#111111", bg: "box", bgColor: "#FFFFFF", bgOpacity: 95, pad: 0.28, size: 5.8, position: "bottom", offset: 9, secondaryColor: "#5B45F0" } },
  { id: "subtle", cat: "catEssentials", name: "Subtle",
    s: { font: "Readex Pro", bold: false, color: "#F2F2F2", bg: "box", bgColor: "#000000", bgOpacity: 45, pad: 0.22, size: 5, position: "bottom", offset: 6 } },

  // ---------------------------------------------------------------- social
  { id: "reel", cat: "catSocial", name: "Reel pop",
    s: { font: "Lalezar", bold: false, uppercase: true, color: "#FFD84D", bg: "outline", bgColor: "#000000", outline: 0.11, size: 8.5, position: "middle" } },
  { id: "tiktok", cat: "catSocial", name: "Punchy",
    s: { font: "Montserrat", bold: true, uppercase: true, color: "#FFFFFF", bg: "outline", bgColor: "#000000", outline: 0.13, size: 7.5, position: "middle", sideMargin: 10 } },
  { id: "bubble", cat: "catSocial", name: "Bubble",
    s: { font: "Poppins", bold: true, color: "#FFFFFF", bg: "box", bgColor: "#FF3D7F", bgOpacity: 100, pad: 0.3, size: 6.4, position: "bottom", offset: 14, secondaryColor: "#FFFFFF" } },
  { id: "story", cat: "catSocial", name: "Story",
    s: { font: "Cairo", bold: true, color: "#111111", bg: "box", bgColor: "#FFD84D", bgOpacity: 100, pad: 0.3, size: 6.6, position: "top", offset: 12, secondaryColor: "#111111" } },

  // ---------------------------------------------------------------- cinema
  { id: "cinema", cat: "catCinema", name: "Cinema",
    s: { font: "Amiri", bold: false, color: "#F4F1EA", bg: "shadow", bgColor: "#000000", bgOpacity: 90, size: 5.4, position: "bottom", offset: 6 } },
  { id: "festival", cat: "catCinema", name: "Festival",
    s: { font: "Inter", bold: false, color: "#FFFFFF", bg: "shadow", bgColor: "#000000", bgOpacity: 85, size: 4.6, position: "bottom", offset: 5, sideMargin: 12 } },
  { id: "documentary", cat: "catCinema", name: "Documentary",
    s: { font: "Noto Naskh Arabic", bold: true, color: "#FFF6DC", bg: "outline", bgColor: "#1A1A1A", outline: 0.06, size: 5.4, position: "bottom", offset: 7 } },

  // ---------------------------------------------------------------- arabic calligraphy
  { id: "naskh", cat: "catArabic", name: "Naskh",
    s: { font: "Noto Naskh Arabic", bold: true, color: "#FFFFFF", bg: "box", bgColor: "#0F2A24", bgOpacity: 85, pad: 0.26, size: 6, position: "bottom", offset: 8 } },
  { id: "kufi", cat: "catArabic", name: "Kufi",
    s: { font: "Noto Kufi Arabic", bold: true, color: "#FFFFFF", bg: "shadow", bgColor: "#000000", bgOpacity: 80, size: 5.4, position: "bottom", offset: 8 } },
  { id: "amiri-gold", cat: "catArabic", name: "Amiri gold",
    s: { font: "Amiri", bold: true, color: "#F5C451", bg: "outline", bgColor: "#1B1206", outline: 0.07, size: 6.4, position: "bottom", offset: 8 } },

  // ---------------------------------------------------------------- bold & bright
  { id: "violet", cat: "catBold", name: "Violet",
    s: { font: "Readex Pro", bold: true, color: "#FFFFFF", bg: "box", bgColor: "#5B45F0", bgOpacity: 92, pad: 0.32, size: 6.2, position: "bottom", offset: 10, secondaryColor: "#FFD84D" } },
  { id: "poster", cat: "catBold", name: "Poster",
    s: { font: "Anton", bold: false, uppercase: true, color: "#FFFFFF", bg: "box", bgColor: "#E63946", bgOpacity: 100, pad: 0.2, size: 7.2, position: "middle", secondaryColor: "#FFFFFF" } },
];
