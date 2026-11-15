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

const CARD_VERSION = "0.7.6";
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
  start: "Start",
  d_target: "Entiteit",
  d_action: "Actie",
  d_data: "Actie-data",
  d_on: "Waarde bij start",
  d_off: "Waarde aan het eind",
  d_ready: "Klaar om",
  d_duration: "Looptijd",
  d_offset: "Start-offset",
  d_enabled: "Ingeschakeld",
  d_planned: "Gepland",
  d_price: "Gem. prijs",
  d_status: "Status",
  d_last: "Laatst verstuurd",
  d_none: "nog niets",
  d_failed: "mislukt",
  d_ok: "gelukt",
  d_open: "Apparaat openen",
  d_close: "Sluiten",
  yes: "ja",
  no: "nee",
  hours: "uur",
  followsBlock: "volgt het goedkoopste blok",
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
  start: "Start",
  d_target: "Entity",
  d_action: "Action",
  d_data: "Action data",
  d_on: "Value at start",
  d_off: "Value at end",
  d_ready: "Ready by",
  d_duration: "Run time",
  d_offset: "Start offset",
  d_enabled: "Enabled",
  d_planned: "Planned",
  d_price: "Avg price",
  d_status: "Status",
  d_last: "Last sent",
  d_none: "nothing yet",
  d_failed: "failed",
  d_ok: "ok",
  d_open: "Open device",
  d_close: "Close",
  yes: "yes",
  no: "no",
  hours: "h",
  followsBlock: "follows the cheapest block",
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
    this._open = null; // "entry_id:record_id" of the record whose details are shown
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

    // Click a record name to show (or hide) its properties.
    this.shadowRoot.querySelectorAll("[data-rec]").forEach((el) =>
      el.addEventListener("click", () => {
        this._open = this._open === el.dataset.rec ? null : el.dataset.rec;
        this._render();
      })
    );
    this.shadowRoot.querySelectorAll("[data-nav]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        history.pushState(null, "", el.dataset.nav);
        window.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
      })
    );
  }

  _renderDetails(entry, d, hm, fmt) {
    const L = this._lang || EN;
    const r = d.record || {};
    const p = d.plan;
    const rows = [];
    const row = (k, v, cls = "") => rows.push(`<tr><th>${k}</th><td class="${cls}">${v}</td></tr>`);
    if (r.action) {
      row(L.d_action, `<code>${esc(r.action)}</code>`);
      if (r.action_data) row(L.d_data, `<pre>${esc(r.action_data)}</pre>`);
    }
    if (r.entity_id) {
      row(L.d_target, `<code>${esc(r.entity_id)}</code>`);
      if (!r.action) row(L.d_on, esc(r.on_value ?? "on"));
      if (r.off_value) row(L.d_off, esc(r.off_value));
    }
    row(L.d_ready, r.ready_by ? esc(String(r.ready_by).slice(0, 5)) : `<i>${L.followsBlock}</i>`);
    if (r.duration_hours) row(L.d_duration, `${r.duration_hours} ${L.hours}`);
    if (r.start_offset_minutes) row(L.d_offset, `${r.start_offset_minutes} min`);
    row(L.d_enabled, d.enabled === false ? L.no : L.yes);
    if (p) {
      const state = p.running ? "running" : t(p.end) <= (t(entry.now) || Date.now()) || p.stopped ? "done" : "planned";
      row(L.d_planned, `<b>${L.start} ${hm(t(p.start))}</b> – ${hm(t(p.end))}`);
      if (p.price != null) row(L.d_price, `${fmt(p.price)} ${esc(entry.unit)}`);
      row(L.d_status, L[state]);
    } else if (d.waiting) {
      row(L.d_status, esc(d.waiting));
    }
    const a = d.last_action;
    if (a) {
      const when = new Date(a.time).toLocaleString(this._hass?.locale?.language || undefined, {
        weekday: "short", hour: "2-digit", minute: "2-digit", hour12: this._hour12(),
      });
      row(
        L.d_last,
        `${when} · ${esc(a.what)} · <code>${esc(a.sent)}</code> · ${a.ok ? L.d_ok : `${L.d_failed}: ${esc(a.error)}`}`,
        a.ok ? "" : "error"
      );
    } else {
      row(L.d_last, `<i>${L.d_none}</i>`);
    }
    const link = d.device_id
      ? `<a href="/config/devices/device/${esc(d.device_id)}" data-nav="/config/devices/device/${esc(d.device_id)}">${L.d_open}</a>`
      : "";
    return `
      <div class="details">
        <div class="details-head"><b>${esc(d.name)}</b><span>${link}<a href="#" data-rec="${esc(entry.entry_id)}:${esc(d.id)}">${L.d_close}</a></span></div>
        <table>${rows.join("")}</table>
      </div>`;
  }

  _hour12() {
    const tf = this._hass?.locale?.time_format;
    return tf === "12" ? true : tf === "24" ? false : undefined;
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
    const hm = (ms) =>
      new Date(ms).toLocaleTimeString(this._hass?.locale?.language || undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: this._hour12(),
      });

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
      const recKey = `${entry.entry_id}:${d.id}`;
      svg += `<text class="label${off ? " off" : ""}${this._open === recKey ? " open" : ""}" data-rec="${esc(recKey)}" x="0" y="${ry + ROW_H / 2 + 4}"><title>${esc(d.name)}\n${esc(d.entity_id)}</title>${esc(name)}</text>`;
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
        const tip = `${d.name}\n${L.start} ${hm(ps)} – ${hm(pe)}  (${L[state]})${p.price != null ? `\n${L.avg} ${fmt(p.price)} ${entry.unit}` : ""}${p.value ? `\n→ ${p.value}` : ""}`;
        if (ps < end && pe > start) {
          svg += `<rect class="bar ${state}" x="${bx}" y="${ry + 5}" width="${bw}" height="${ROW_H - 10}" rx="4"><title>${esc(tip)}</title></rect>`;
          // The start time is what matters: spell it out when there is room.
          if (bw > 150) svg += `<text class="barlabel" x="${bx + bw / 2}" y="${ry + ROW_H / 2 + 4}" text-anchor="middle">${L.start} ${hm(ps)} – ${hm(pe)}</text>`;
          else if (bw > 78) svg += `<text class="barlabel" x="${bx + bw / 2}" y="${ry + ROW_H / 2 + 4}" text-anchor="middle">${label}</text>`;
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
        ${devices.filter((d) => this._open === `${entry.entry_id}:${d.id}`).map((d) => this._renderDetails(entry, d, hm, fmt)).join("")}
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
      .label { cursor: pointer; }
      .label:hover, .label.open { fill: var(--primary-color); text-decoration: underline; }
      .details { margin: 8px 0; padding: 10px 12px; border: 1px solid var(--divider-color, #ddd); border-radius: 8px; font-size: 13px; color: var(--primary-text-color); }
      .details-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; font-size: 14px; }
      .details-head a { margin-left: 14px; font-size: 12px; color: var(--primary-color); text-decoration: none; cursor: pointer; }
      .details table { border-collapse: collapse; width: 100%; }
      .details th { text-align: left; font-weight: 400; color: var(--secondary-text-color); padding: 2px 12px 2px 0; white-space: nowrap; vertical-align: top; width: 1%; }
      .details td { padding: 2px 0; overflow-wrap: anywhere; }
      .details code, .details pre { overflow-wrap: anywhere; white-space: pre-wrap; }
      .details td.error { color: var(--error-color, #db4437); }
      .details pre { margin: 0; white-space: pre-wrap; font-size: 12px; }
      .details code { font-size: 12px; }
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
