/*
 * Cheapest Hour Scheduler card
 *
 * Shows, from left to right over time, the energy prices and a bar per device
 * for the period it is planned to be on.
 *
 * type: custom:cheapest-hour-card
 * entity: sensor.cheapest_hours_block_start   # optional, any entity of the integration
 * title: Goedkoopste uren                     # optional
 * hours_back: 2                               # optional, history shown before "now"
 * hours: 36                                   # optional, max hours shown
 */

const CARD_VERSION = "0.6.2";
const WS_TYPE = "cheapest_hour_scheduler/plan";
const REFRESH_MS = 60 * 1000;

const LABEL_W = 120; // device name column
const PRICE_H = 120; // price chart height
const ROW_H = 28; // device row height
const AXIS_H = 34; // time axis height
const PAD = 8;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const t = (iso) => (iso ? new Date(iso).getTime() : null);

const NL = {
  noData: "Geen prijsdata",
  waiting: "wacht",
  block: "Goedkoopste blok",
  avg: "gem.",
  cache: "Prijssensor niet beschikbaar, opgeslagen prijzen worden gebruikt",
  disabled: "Planner staat uit",
  readyBy: "klaar om",
  running: "aan",
  done: "klaar",
  planned: "gepland",
  now: "nu",
  off: "uitgeschakeld",
};
const EN = {
  noData: "No price data",
  waiting: "waiting",
  block: "Cheapest block",
  avg: "avg",
  cache: "Price sensor unavailable, using stored prices",
  disabled: "Scheduler is disabled",
  readyBy: "ready by",
  running: "on",
  done: "done",
  planned: "planned",
  now: "now",
  off: "disabled",
};

class CheapestHourCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._error = null;
    this._width = 600;
    this._lastFetch = 0;
    this._watchUpdated = null;
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { hours_back: 2, ...config };
    this._render();
  }

  getCardSize() {
    const rows = (this._data?.entries || []).reduce((n, e) => n + e.devices.length, 0);
    return Math.ceil((PRICE_H + AXIS_H + rows * ROW_H + 60) / 50);
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._lang = (hass.locale?.language || hass.language || "en").startsWith("nl") ? NL : EN;
    let changed = false;
    if (this._config?.entity) {
      const st = hass.states[this._config.entity];
      const upd = st?.last_updated;
      if (upd && upd !== this._watchUpdated) {
        this._watchUpdated = upd;
        changed = true;
      }
    }
    if (first || changed || Date.now() - this._lastFetch > REFRESH_MS) this._fetch();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._fetch(), REFRESH_MS);
    this._ro = new ResizeObserver((entries) => {
      const w = Math.floor(entries[0].contentRect.width);
      if (w > 0 && Math.abs(w - this._width) > 2) {
        this._width = w;
        this._render();
      }
    });
    this._ro.observe(this);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._ro?.disconnect();
  }

  async _fetch() {
    if (!this._hass) return;
    this._lastFetch = Date.now();
    const msg = { type: WS_TYPE };
    if (this._config?.entity) msg.entity_id = this._config.entity;
    try {
      this._data = await this._hass.callWS(msg);
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._render();
  }

  // ------------------------------------------------------------------ render

  _render() {
    if (!this._config) return;
    const L = this._lang || EN;
    const entries = this._data?.entries || [];
    const title = this._config.title ?? (entries.length === 1 ? entries[0].title : "Cheapest Hour Scheduler");

    let body = "";
    if (this._error) body = `<div class="msg error">${esc(this._error)}</div>`;
    else if (!this._data) body = `<div class="msg">…</div>`;
    else if (!entries.length) body = `<div class="msg">${L.noData}</div>`;
    else body = entries.map((e) => this._renderEntry(e, entries.length > 1)).join("");

    this.shadowRoot.innerHTML = `
      <style>${CheapestHourCard.styles}</style>
      <ha-card>
        <div class="header">${esc(title)}</div>
        <div class="content">${body}</div>
      </ha-card>`;
  }

  _renderEntry(entry, showTitle) {
    const L = this._lang || EN;
    const now = t(entry.now) || Date.now();
    const prices = entry.prices.map((p) => ({ s: t(p.start), e: t(p.end), v: p.price }));

    // ---- time range
    const hourMs = 3600e3;
    let start = Math.floor((now - (this._config.hours_back ?? 2) * hourMs) / hourMs) * hourMs;
    let end = prices.length ? prices[prices.length - 1].e : now + 12 * hourMs;
    for (const d of entry.devices) {
      if (d.plan) end = Math.max(end, t(d.plan.end) || 0, t(d.plan.deadline) || 0);
    }
    if (this._config.hours) end = Math.min(end, start + this._config.hours * hourMs);
    if (end <= start) end = start + 12 * hourMs;

    const W = Math.max(320, this._width - 2 * 16);
    const plotW = W - LABEL_W - PAD;
    const x = (ms) => LABEL_W + ((Math.min(Math.max(ms, start), end) - start) / (end - start)) * plotW;
    const devices = entry.devices;
    const H = PRICE_H + devices.length * ROW_H + AXIS_H + PAD;
    const rowsTop = PRICE_H + PAD;
    const axisTop = rowsTop + devices.length * ROW_H;

    // ---- price scale
    const visible = prices.filter((p) => p.e > start && p.s < end);
    const vals = visible.map((p) => p.v);
    const min = vals.length ? Math.min(...vals) : 0;
    const max = vals.length ? Math.max(...vals) : 1;
    const lo = Math.max(0, min - (max - min) * 0.15);
    const hi = max + (max - min) * 0.05 || lo + 1;
    const y = (v) => PRICE_H - 14 - ((v - lo) / (hi - lo)) * (PRICE_H - 34);
    const color = (v) => {
      const r = max > min ? (v - min) / (max - min) : 0;
      return `hsl(${Math.round(120 - 120 * r)}, 65%, 48%)`;
    };
    const fmt = (v) => (v == null ? "–" : (Math.round(v * 1000) / 1000).toFixed(3));
    const hm = (ms) => new Date(ms).toLocaleTimeString(this._hass?.locale?.language || undefined, { hour: "2-digit", minute: "2-digit" });

    let svg = "";

    // grid + axis labels
    const pxPerHour = plotW / ((end - start) / hourMs);
    const step = [1, 2, 3, 4, 6, 12].find((h) => h * pxPerHour >= 42) || 24;
    for (let ms = Math.ceil(start / hourMs) * hourMs; ms <= end; ms += hourMs) {
      const d = new Date(ms);
      const midnight = d.getHours() === 0;
      if (!midnight && d.getHours() % step) continue;
      const gx = x(ms);
      svg += `<line class="${midnight ? "grid day" : "grid"}" x1="${gx}" x2="${gx}" y1="0" y2="${axisTop}"/>`;
      if (gx > LABEL_W + plotW - 16) continue;
      svg += `<text class="axis" x="${gx}" y="${axisTop + 14}" text-anchor="middle">${String(d.getHours()).padStart(2, "0")}:00</text>`;
      if (midnight && gx < LABEL_W + plotW - 70)
        svg += `<text class="axis date" x="${gx + 3}" y="${axisTop + 28}">${esc(d.toLocaleDateString(this._hass?.locale?.language || undefined, { weekday: "short", day: "numeric", month: "short" }))}</text>`;
    }

    // shared block band
    if (entry.block) {
      const bx = x(t(entry.block.start));
      const bw = x(t(entry.block.end)) - bx;
      if (bw > 0)
        svg += `<rect class="band" x="${bx}" y="0" width="${bw}" height="${axisTop}"><title>${L.block}: ${hm(t(entry.block.start))}–${hm(t(entry.block.end))} (${L.avg} ${fmt(entry.block.price)} ${esc(entry.unit)})</title></rect>`;
    }

    // price bars
    for (const p of visible) {
      const px = x(p.s);
      const pw = Math.max(0.5, x(p.e) - px - (pxPerHour > 60 ? 1 : 0));
      const py = y(p.v);
      svg += `<rect x="${px}" y="${py}" width="${pw}" height="${PRICE_H - 14 - py}" fill="${color(p.v)}" class="${p.e <= now ? "past" : ""}"><title>${hm(p.s)}–${hm(p.e)}: ${fmt(p.v)} ${esc(entry.unit)}</title></rect>`;
    }
    if (!visible.length) svg += `<text class="axis" x="${LABEL_W + plotW / 2}" y="${PRICE_H / 2}" text-anchor="middle">${L.noData}</text>`;
    // price scale labels
    svg += `<text class="axis" x="${LABEL_W - 6}" y="${y(max) + 4}" text-anchor="end">${fmt(max)}</text>`;
    svg += `<text class="axis" x="${LABEL_W - 6}" y="${y(min) + 4}" text-anchor="end">${fmt(min)}</text>`;
    svg += `<text class="axis unit" x="${LABEL_W - 6}" y="${y(max) - 12}" text-anchor="end">${esc(entry.unit)}</text>`;
    svg += `<line class="base" x1="${LABEL_W}" x2="${LABEL_W + plotW}" y1="${PRICE_H - 14}" y2="${PRICE_H - 14}"/>`;

    // device rows
    devices.forEach((d, i) => {
      const ry = rowsTop + i * ROW_H;
      const name = d.name.length > 18 ? d.name.slice(0, 17) + "…" : d.name;
      svg += `<line class="rowline" x1="0" x2="${LABEL_W + plotW}" y1="${ry + ROW_H}" y2="${ry + ROW_H}"/>`;
      const off = d.enabled === false;
      svg += `<text class="label${off ? " off" : ""}" x="0" y="${ry + ROW_H / 2 + 4}"><title>${esc(d.name)}\n${esc(d.entity_id)}</title>${esc(name)}</text>`;
      const p = d.plan;
      if (off) {
        svg += `<text class="axis muted" x="${LABEL_W + 4}" y="${ry + ROW_H / 2 + 4}">${L.off}</text>`;
      } else if (p) {
        const ps = t(p.start);
        const pe = t(p.end);
        const state = p.running ? "running" : pe <= now || p.stopped ? "done" : "planned";
        const bx = x(ps);
        const bw = Math.max(3, x(pe) - bx);
        const label = `${hm(ps)}–${hm(pe)}`;
        const tip = `${d.name}\n${label}  (${L[state]})${p.price != null ? `\n${L.avg} ${fmt(p.price)} ${entry.unit}` : ""}${p.value ? `\n→ ${p.value}` : ""}`;
        if (ps < end && pe > start) {
          svg += `<rect class="bar ${state}" x="${bx}" y="${ry + 5}" width="${bw}" height="${ROW_H - 10}" rx="4"><title>${esc(tip)}</title></rect>`;
          if (bw > 78) svg += `<text class="barlabel" x="${bx + bw / 2}" y="${ry + ROW_H / 2 + 4}" text-anchor="middle">${label}</text>`;
          else {
            // Right of the bar, unless the ready-by flag or the edge is in the way.
            const LW = 78;
            let lx = bx + bw + 5;
            let anchor = "start";
            const dx = p.deadline ? x(t(p.deadline)) : null;
            const blocked = (dx !== null && dx > lx - 8 && dx < lx + LW) || lx + LW > LABEL_W + plotW;
            if (blocked && bx - 5 - LW > LABEL_W) {
              lx = bx - 5;
              anchor = "end";
            } else if (blocked && dx !== null) {
              lx = Math.max(lx, dx + 8);
            }
            svg += `<text class="axis" x="${lx}" y="${ry + ROW_H / 2 + 4}" text-anchor="${anchor}">${label}</text>`;
          }
        }
        if (p.deadline) {
          const dx = x(t(p.deadline));
          svg += `<path class="deadline" d="M${dx} ${ry + 3} v${ROW_H - 6} M${dx} ${ry + 3} l-6 0 l0 6 l6 0"><title>${L.readyBy} ${hm(t(p.deadline))}</title></path>`;
        }
      } else {
        svg += `<text class="axis muted" x="${LABEL_W + 4}" y="${ry + ROW_H / 2 + 4}">${esc(d.waiting || L.waiting)}</text>`;
      }
    });

    // now line
    if (now >= start && now <= end) {
      const nx = x(now);
      svg += `<line class="now" x1="${nx}" x2="${nx}" y1="0" y2="${axisTop}"/>`;
      svg += `<rect class="nowbg" x="${nx - 12}" y="0" width="24" height="14" rx="3"/>`;
      svg += `<text class="nowlabel" x="${nx}" y="11" text-anchor="middle">${L.now}</text>`;
    }

    // footer
    const notes = [];
    if (!entry.enabled) notes.push(`<span class="warn">${L.disabled}</span>`);
    if (entry.price_source === "cache") notes.push(`<span class="warn">${L.cache}</span>`);
    if (entry.block)
      notes.push(`${L.block}: <b>${hm(t(entry.block.start))}–${hm(t(entry.block.end))}</b> · ${L.avg} ${fmt(entry.block.price)} ${esc(entry.unit)}`);

    return `
      <div class="entry">
        ${showTitle ? `<div class="subtitle">${esc(entry.title)}</div>` : ""}
        <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">${svg}</svg>
        <div class="notes">${notes.join("<br>")}</div>
      </div>`;
  }

  static get styles() {
    return `
      :host { display: block; }
      ha-card { overflow: hidden; }
      .header { padding: 16px 16px 4px; font-size: 1.2em; font-weight: 500; color: var(--primary-text-color); }
      .content { padding: 4px 16px 16px; }
      .subtitle { font-weight: 500; margin: 8px 0 4px; color: var(--primary-text-color); }
      .msg { padding: 16px 0; color: var(--secondary-text-color); }
      .msg.error { color: var(--error-color, #db4437); }
      svg { display: block; max-width: 100%; height: auto; font-family: var(--paper-font-body1_-_font-family, inherit); }
      .grid { stroke: var(--divider-color, #ddd); stroke-width: 1; }
      .grid.day { stroke: var(--secondary-text-color); stroke-dasharray: 3 3; opacity: .6; }
      .base, .rowline { stroke: var(--divider-color, #ddd); stroke-width: 1; }
      .band { fill: var(--primary-color); opacity: .08; }
      .past { opacity: .35; }
      .axis { font-size: 11px; fill: var(--secondary-text-color); }
      .axis.unit { font-size: 10px; }
      .axis.date { font-weight: 500; }
      .muted { font-style: italic; }
      .label { font-size: 12px; fill: var(--primary-text-color); }
      .label.off { fill: var(--disabled-text-color, #9e9e9e); text-decoration: line-through; }
      .bar.planned { fill: var(--primary-color); }
      .bar.running { fill: var(--success-color, #43a047); }
      .bar.done { fill: var(--disabled-text-color, #9e9e9e); opacity: .7; }
      .barlabel { font-size: 11px; fill: var(--text-primary-color, #fff); pointer-events: none; }
      .deadline { stroke: var(--warning-color, #ffa600); stroke-width: 2; fill: var(--warning-color, #ffa600); fill-opacity: .6; }
      .now { stroke: var(--error-color, #db4437); stroke-width: 1.5; }
      .nowbg { fill: var(--error-color, #db4437); }
      .nowlabel { font-size: 10px; font-weight: 500; fill: #fff; }
      .notes { font-size: 12px; color: var(--secondary-text-color); margin-top: 4px; line-height: 1.6; }
      .warn { color: var(--warning-color, #ffa600); }
    `;
  }
}

if (!customElements.get("cheapest-hour-card")) {
  customElements.define("cheapest-hour-card", CheapestHourCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "cheapest-hour-card",
    name: "Cheapest Hour Scheduler",
    description: "Prices and planned device runs over time",
    preview: false,
    documentationURL: "https://github.com/rbr101/cheapest-hour-scheduler",
  });
  console.info(`%c CHEAPEST-HOUR-CARD %c ${CARD_VERSION} `, "background:#03a9f4;color:#fff", "");
}
