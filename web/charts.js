// Arka Trades · small SVG charts shared by every page: lines with tap-to-read values, coloured bands, reference lines,
// optional log scale, and bar charts. No chart library, so pages stay fast on a phone.
import { nice } from "./app.js";

const CH = new Map(); let chN = 0;
export function resetCharts() { CH.clear(); chN = 0; }

// dates: ["YYYY-MM-DD"]; series: [{n, c, v, w, dash, fill}]; o: {h, fmt, bands: [[i0, i1, colour]], hlines: [[y, colour]], log, bars, marks: [[i, price, "up"|"dn"]]}
export function lineChart(dates, series, o = {}) {
  const H = o.h || 220, W = 1000, n = dates.length, tf = o.log ? (v) => (v > 0 ? Math.log(v) : null) : (v) => v;
  const all = series.flatMap((s) => s.v.map(tf).filter((x) => x != null && isFinite(x))).concat((o.hlines || []).map((x) => tf(x[0])).filter((x) => x != null));
  if (n < 2 || !all.length) return '<div class="note">Not enough history for a chart yet.</div>';
  let lo = Math.min(...all), hi = Math.max(...all); if (hi === lo) hi = lo + 1;
  const pad = (hi - lo) * 0.07; lo -= pad; hi += pad;
  const step = Math.max(1, Math.ceil(n / 700)), X = (i) => (i / (n - 1)) * W, Y = (v) => H - ((tf(v) - lo) / (hi - lo)) * H, id = "c" + ++chN, fmt = o.fmt || ((v) => String(v));
  const bands = (o.bands || []).map(([a, b, c]) => `<rect x="${X(a).toFixed(1)}" y="0" width="${Math.max(1, X(b) - X(a) + W / n).toFixed(1)}" height="${H}" fill="${c}" opacity=".13"/>`).join("");
  const hl = (o.hlines || []).map(([y, c]) => `<line x1="0" x2="${W}" y1="${Y(y).toFixed(1)}" y2="${Y(y).toFixed(1)}" stroke="${c}" stroke-dasharray="4 5" opacity=".7" vector-effect="non-scaling-stroke"/>`).join("");
  let bars = "";
  if (o.bars) { const z = Y(0), bw = Math.max(1, W / n - 0.5); bars = series[0].v.map((v, i) => (v == null || !isFinite(v) ? "" : `<rect x="${X(i).toFixed(1)}" y="${Math.min(Y(v), z).toFixed(1)}" width="${bw.toFixed(2)}" height="${Math.abs(Y(v) - z).toFixed(1)}" fill="${o.barColor ? o.barColor(v) : series[0].c}"/>`)).join(""); }
  const paths = o.bars ? "" : series.map((s) => {
    let d = "", pen = false;
    for (let i = 0; i < n; i += step) { const j = i + step >= n ? n - 1 : i, v = s.v[j]; if (v == null || !isFinite(v) || (o.log && v <= 0)) { pen = false; continue; } d += (pen ? "L" : "M") + X(j).toFixed(1) + " " + Y(v).toFixed(1); pen = true; if (j === n - 1) break; }
    return (s.fill ? `<path d="${d}L${W} ${H}L0 ${H}Z" fill="${s.c}" opacity=".09"/>` : "") + `<path d="${d}" fill="none" stroke="${s.c}" stroke-width="${s.w || 1.8}" ${s.dash ? 'stroke-dasharray="5 5"' : ""} vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`;
  }).join("");
  const ticks = [0, 1, 2, 3].map((k) => { const t = lo + ((hi - lo) * (k + 0.5)) / 4; return o.log ? Math.exp(t) : t; });
  CH.set(id, { dates, series, fmt, extra: o.extra });
  return `<div class="ch" data-ch="${id}"><div class="rd">${readout(id, n - 1)}</div><div class="plot" style="height:${H}px"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bands}${
    ticks.map((t) => `<line x1="0" x2="${W}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}" stroke="rgba(255,255,255,.06)" vector-effect="non-scaling-stroke"/>`).join("")}${hl}${bars}${paths}</svg>${(o.marks || []).filter(([i, v]) => i >= 0 && i < n && v != null && isFinite(tf(v))).map(([i, v, cl]) => `<b class="mk ${cl}" style="left:${((X(i) / W) * 100).toFixed(2)}%;top:${((Y(v) / H) * 100).toFixed(2)}%"></b>`).join("")}<i class="vx"></i>${
    ticks.map((t) => `<em style="top:${((Y(t) / H) * 100).toFixed(1)}%">${fmt(t)}</em>`).join("")}</div><div class="xl"><span>${nice(dates[0])}</span><span>${nice(dates[n >> 1])}</span><span>${nice(dates[n - 1])}</span></div></div>`;
}
function readout(id, i) {
  const c = CH.get(id); if (!c) return "";
  return `<span class="dt">${nice(c.dates[i])}</span>` + (c.extra ? c.extra(i) : "") + c.series.map((s) => `<span><i style="background:${s.c}"></i>${s.n} <b>${s.v[i] == null ? "—" : c.fmt(s.v[i])}</b></span>`).join("");
}
export function chartMove(e) {                            // move the reading line under the finger or pointer
  const plot = e.target.closest && e.target.closest(".ch .plot"); if (!plot) return;
  const box = plot.parentNode, c = CH.get(box.dataset.ch); if (!c) return;
  const r = plot.getBoundingClientRect(), f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), i = Math.round(f * (c.dates.length - 1));
  plot.querySelector(".vx").style.cssText = `display:block;left:${(f * 100).toFixed(2)}%`;
  box.querySelector(".rd").innerHTML = readout(box.dataset.ch, i);
}
export function bandsFrom(states, colourOf) {             // runs of the same state -> [[from, to, colour]]
  const out = []; let s0 = 0;
  for (let i = 1; i <= states.length; i++) if (i === states.length || states[i] !== states[s0]) { out.push([s0, i - 1, colourOf(states[s0])]); s0 = i; }
  return out;
}
export const legend = (items) => `<div class="lgd">${items.map(([n, c, box]) => `<span><i class="${box ? "bx" : ""}" style="background:${c}"></i>${n}</span>`).join("")}</div>`;
