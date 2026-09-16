/* Input parsing only. All football/scoring/comparison logic belongs to the Python API. */
window.FPLInput = {
  parseLeagueId(value) {
    const input = String(value || "").trim();
    const normalise = (id) => /^\d+$/.test(id) && Number.isSafeInteger(Number(id)) && Number(id) > 0 ? String(Number(id)) : "";
    if (/^\d+$/.test(input)) return normalise(input);
    try {
      const url = new URL(input.startsWith("fantasy.premierleague.com/") ? `https://${input}` : input);
      if (!["http:", "https:"].includes(url.protocol) || url.hostname !== "fantasy.premierleague.com" || url.username || url.password) return "";
      const match = url.pathname.match(/^\/(?:en\/)?leagues\/(\d+)\/(?:standings\/(?:c\/?|classic\/?)?)?$/);
      return match ? normalise(match[1]) : "";
    } catch { return ""; }
  },
  read(key) { try { return localStorage.getItem(key); } catch { return null; } },
  write(key, value) { try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ } },
};
