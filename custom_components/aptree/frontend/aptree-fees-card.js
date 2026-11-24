/* APTREE fee dashboard card: bill-style tables, charts and original PDFs. */
(() => {
  const CHART_MONTHS = 13;
  const won = (v) => (v == null ? "–" : Math.round(v).toLocaleString("ko-KR"));
  const num = (v) => (v == null ? "–" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 3 }));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const ym = (m) => m.slice(0, 7);
  const label = (m) => `${m.slice(0, 4)}년 ${Number(m.slice(5, 7))}월분`;
  const shiftMonth = (m, d) => {
    const i = Number(m.slice(0, 4)) * 12 + Number(m.slice(5, 7)) - 1 + d;
    return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
  };

  /* Round axis steps (1, 2, 5 x 10^n) so that ticks are easy to read. */
  const niceAxis = (max, target = 5) => {
    if (!(max > 0)) return { step: 1, max: 1 };
    const raw = max / target;
    const base = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 5, 10].map((f) => f * base).find((s) => s >= raw * 0.999);
    return { step, max: Math.ceil((max / step) * 0.9999) * step };
  };
  const tickText = (v, step) =>
    v.toLocaleString("ko-KR", { maximumFractionDigits: Math.max(0, -Math.floor(Math.log10(step))) });

  /* Website and app bills spell the same line differently. */
  const PREFIXES = ["세대", "전기차", "급탕", "난방"];
  const key = (t) => {
    const k = String(t ?? "").replace(/[\s()]/g, "");
    if (k === "생활폐기물수수") return "음식물쓰레기수수료";
    if (k === "급탕요금") return "급탕";
    return PREFIXES.find((p) => k.startsWith(p)) || k;
  };
  const TOTALS = new Set(["소계", "전기+수도계", "당일부과금합계"]);
  const items = (row, list) => ((row && row[list]) || []).filter((i) => !TOTALS.has(String(i.title).replace(/\s/g, "")));
  const subtotal = (row, list) => {
    if (!row) return null;
    const printed = (row[list] || []).find((i) => i.title === "소계");
    return printed ? printed.amount : items(row, list).reduce((s, i) => s + (i.amount || 0), 0);
  };
  const display = (t) => {
    const s = String(t).replace(/\s+/g, " ").trim();
    const m = s.replace(/ /g, "").match(/^(세대|전기차|급탕|난방)\(?([\d.,]*)(k|t|M)\)?$/i);
    if (m) {
      const unit = { k: "kWh", t: "m³", m: "MWh" }[m[3].toLowerCase()];
      return m[2] ? `${m[1]} <small>${m[2]} ${unit}</small>` : m[1];
    }
    return esc(key(s) === "음식물쓰레기수수료" ? "음식물쓰레기 수수료" : s);
  };

  const delta = (d, unit = "") => {
    if (d == null) return '<span class="mut">–</span>';
    if (d === 0) return '<span class="mut">0</span>';
    return d > 0
      ? `<span class="up">▲ ${num(d)}${unit}</span>`
      : `<span class="down">▼ ${num(-d)}${unit}</span>`;
  };
  const compared = (cur, ref, unit = "") =>
    cur == null || ref == null
      ? '<span class="mut">자료 없음</span>'
      : `${delta(Number((cur - ref).toFixed(3)), unit)} <span class="mut">(${num(ref)})</span>`;

  /* ------------------------------------------------------------ charts */
  function chart({ months, series, unit, selected, values = true, height = 250 }) {
    const W = 640, H = height, L = 62, R = 10, T = 22, B = 40;
    const all = series.flatMap((s) => s.data.filter((v) => v != null));
    if (!all.length || Math.max(...all) <= 0) return '<div class="empty">표시할 자료가 없습니다.</div>';
    const axis = niceAxis(Math.max(...all));
    const iw = W - L - R, ih = H - T - B;
    const y = (v) => T + ih * (1 - Math.max(0, v) / axis.max);
    const slot = iw / months.length;
    const x = (i) => L + slot * (i + 0.5);
    let g = "";
    const ticks = Math.round(axis.max / axis.step);
    for (let i = 0; i <= ticks; i++) {
      const v = i * axis.step;
      g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>` +
        `<text class="tick" x="${L - 7}" y="${y(v) + 4}" text-anchor="end">${tickText(v, axis.step)}</text>`;
    }
    g += `<text class="unit" x="${L - 7}" y="11" text-anchor="end">(${esc(unit)})</text>`;
    months.forEach((m, i) => {
      const mm = Number(m.slice(5, 7));
      const on = m === selected;
      g += `<text class="tick${on ? " on" : ""}" x="${x(i)}" y="${H - B + 17}" text-anchor="middle">${mm}월</text>`;
      if (i === 0 || mm === 1)
        g += `<text class="year" x="${x(i)}" y="${H - B + 32}" text-anchor="middle">${m.slice(0, 4)}</text>`;
    });
    const bars = series.filter((s) => s.type !== "line");
    const bw = Math.min(34, (slot * 0.68) / Math.max(1, bars.length));
    bars.forEach((s, b) =>
      s.data.forEach((v, i) => {
        if (v == null) return;
        const bx = x(i) - (bw * bars.length) / 2 + b * bw;
        const on = months[i] === selected;
        g += `<rect x="${bx + 1}" y="${y(v)}" width="${bw - 2}" height="${T + ih - y(v)}" rx="2" fill="${s.color}" opacity="${on || !selected ? 1 : 0.62}"/>`;
        if (values && bars.length === 1)
          g += `<text class="val${on ? " on" : ""}" x="${x(i)}" y="${y(v) - 5}" text-anchor="middle">${num(v)}</text>`;
      })
    );
    series.filter((s) => s.type === "line").forEach((s) => {
      let d = "", pen = false;
      s.data.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i)} ${y(v)}`;
        pen = true;
      });
      g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.5"/>`;
      s.data.forEach((v, i) => {
        if (v != null) g += `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="${s.color}" class="dot"/>`;
      });
    });
    months.forEach((m, i) => {
      const tip = [label(m), ...series.map((s) => `${s.name}: ${num(s.data[i])}${s.data[i] == null ? "" : " " + unit}`)].join("\n");
      g += `<rect class="hit" data-month="${m}" x="${L + slot * i}" y="${T}" width="${slot}" height="${ih + B}"><title>${esc(tip)}</title></rect>`;
    });
    const legend = series.length > 1
      ? `<div class="legend">${series.map((s) => `<span><i class="${s.type === "line" ? "ln" : ""}" style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`
      : "";
    return `<svg viewBox="0 0 ${W} ${H}" role="img">${g}</svg>${legend}`;
  }

  const PALETTE = ["#f5a623", "#7cb342", "#42a5f5", "#ab47bc", "#26a69a", "#ef6c57", "#8d6e63", "#78909c"];
  function donut(parts, total) {
    const sum = parts.reduce((s, p) => s + p.value, 0);
    if (!(sum > 0)) return "";
    const R = 80, r = 50;
    let a = -Math.PI / 2, g = "";
    parts.forEach((p, i) => {
      const e = a + (p.value / sum) * 2 * Math.PI * 0.9999;
      const big = e - a > Math.PI ? 1 : 0;
      const pt = (rad, ang) => `${(100 + rad * Math.cos(ang)).toFixed(2)} ${(100 + rad * Math.sin(ang)).toFixed(2)}`;
      g += `<path fill="${PALETTE[i % PALETTE.length]}" d="M${pt(R, a)}A${R} ${R} 0 ${big} 1 ${pt(R, e)}L${pt(r, e)}A${r} ${r} 0 ${big} 0 ${pt(r, a)}Z"><title>${esc(p.name)} ${won(p.value)}원</title></path>`;
      a = e;
    });
    return `<div class="donut"><svg viewBox="10 10 180 180"><g>${g}</g><text x="100" y="97" text-anchor="middle" class="dl">당월</text><text x="100" y="114" text-anchor="middle" class="dv">${won(total)}</text></svg>
      <ul>${parts.map((p, i) => `<li><i style="background:${PALETTE[i % PALETTE.length]}"></i><span>${esc(p.name)}</span><b>${((p.value / sum) * 100).toFixed(1)}%</b><em>${won(p.value)}원</em></li>`).join("")}</ul></div>`;
  }

  const CSS = `
  :host{display:block}
  .wrap{padding:8px;max-width:1500px;margin:0 auto;color:var(--primary-text-color);font-size:14px}
  .nav{display:flex;justify-content:center;align-items:center;gap:8px;margin:4px 0 12px;flex-wrap:wrap}
  button,select{font:inherit;color:inherit;background:var(--ha-card-background,var(--card-background-color));border:1px solid var(--divider-color);border-radius:10px;min-height:42px;padding:0 14px;cursor:pointer}
  button:disabled{opacity:.35;cursor:default} select{font-weight:600}
  select option{color:var(--primary-text-color);background:var(--card-background-color)}
  .cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,430px),1fr));gap:12px;align-items:start}
  .col{display:grid;gap:12px;min-width:0}
  .card{background:var(--ha-card-background,var(--card-background-color));border:1px solid var(--divider-color);border-radius:12px;padding:14px;min-width:0}
  h3{font-size:15px;font-weight:600;margin:0 0 10px} h3 small{font-weight:400;color:var(--secondary-text-color)}
  .scroll{overflow-x:auto}
  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th,td{padding:6px;text-align:right;border-bottom:1px solid var(--divider-color);white-space:nowrap}
  th{font-weight:500;color:var(--secondary-text-color);font-size:12px}
  th:first-child,td:first-child{text-align:left;white-space:normal;word-break:keep-all}
  td.cur{font-weight:600} td small{color:var(--secondary-text-color)}
  tr.sum td{font-weight:700;background:rgba(127,127,127,.12)}
  .mut{color:var(--secondary-text-color);font-weight:400}
  .up{color:var(--error-color,#e5534b)} .down{color:var(--info-color,#42a5f5)}
  .box{display:grid;grid-template-columns:minmax(110px,auto) 1fr;gap:12px;align-items:stretch;margin-bottom:10px}
  .now{border-radius:10px;overflow:hidden;text-align:center;border:2px solid var(--c);display:flex;flex-direction:column}
  .now span{background:var(--c);color:#1b1b1b;font-weight:600;padding:4px}
  .now b{font-size:17px;padding:8px 6px 2px} .now em{font-style:normal;padding:2px 6px 8px}
  .grand{display:flex;justify-content:space-between;align-items:center;font-size:16px;font-weight:700}
  svg{width:100%;height:auto;display:block}
  .grid{stroke:var(--divider-color);stroke-width:1}
  text{fill:var(--secondary-text-color);font-size:12px}
  text.val{font-size:10.5px} text.year,text.unit{font-size:11px}
  text.on{fill:var(--primary-text-color);font-weight:700}
  .dot{stroke:var(--card-background-color);stroke-width:1.5}
  .hit{fill:transparent;cursor:pointer} .hit:hover{fill:rgba(127,127,127,.12)}
  .legend{display:flex;gap:16px;justify-content:center;font-size:12px;margin-top:4px;color:var(--secondary-text-color)}
  .legend i,.donut li i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;flex:none} .legend i.ln{height:3px;vertical-align:middle}
  .donut{display:grid;grid-template-columns:minmax(130px,190px) 1fr;gap:16px;align-items:center}
  .donut ul{list-style:none;margin:0;padding:0} .donut li{display:flex;align-items:center;padding:3px 0}
  .donut li span{flex:1} .donut li em{font-style:normal;color:var(--secondary-text-color);min-width:84px;text-align:right}
  .dv{font-size:15px;font-weight:700;fill:var(--primary-text-color)}
  .empty{padding:24px;text-align:center;color:var(--secondary-text-color)}
  .note{font-size:12px;color:var(--secondary-text-color);margin-top:8px}
  .months{display:flex;flex-wrap:wrap;gap:8px} .months button.on{border-color:var(--primary-color);color:var(--primary-color);font-weight:700}
  iframe{width:100%;height:82vh;border:0;border-radius:8px;background:#fff}
  a.open{display:inline-flex;align-items:center;min-height:42px;padding:0 14px;border-radius:10px;background:var(--primary-color);color:var(--text-primary-color,#fff);text-decoration:none;margin-bottom:10px}
  @media(max-width:500px){.wrap{padding:4px}.card{padding:10px}th,td{padding:5px 3px;font-size:12.5px}td .mut.ref{display:none}.donut{grid-template-columns:1fr;justify-items:center}.donut ul{width:100%}text{font-size:17px}text.val{font-size:14px}text.year,text.unit{font-size:15px}.box{grid-template-columns:1fr}}
  `;

  class AptreeFeesCard extends HTMLElement {
    setConfig(config) {
      this._config = { view: "overview", entity: "sensor.apateuri_12_month_history", ...config };
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    }
    getCardSize() { return 12; }
    getGridOptions() { return { columns: "full" }; }

    set hass(hass) {
      this._hass = hass;
      const state = hass.states[this._config.entity];
      const stamp = state ? state.last_updated : "none";
      if (stamp === this._stamp) return;
      this._stamp = stamp;
      this._load();
    }

    async _load() {
      try {
        this._data = await this._hass.callWS({ type: "aptree/bills" });
        this._error = null;
      } catch (err) {
        this._error = err && err.code === "unknown_command"
          ? "APTREE 통합을 업데이트하고 Home Assistant를 다시 시작해 주세요."
          : "관리비 자료를 불러오지 못했습니다.";
        this._stamp = undefined;
      }
      this._render();
    }

    get _month() {
      const months = this._data.bills.map((b) => ym(b.month));
      const picked = window.__aptreeMonth;
      return months.includes(picked) ? picked : months[months.length - 1];
    }
    _pick(month) {
      window.__aptreeMonth = month;
      this._render();
    }
    _row(month) { return this._data.bills.find((b) => ym(b.month) === month); }

    /* Continuous range ending at the newest bill: unbilled months never appear. */
    _range() {
      const bills = this._data.bills;
      const last = ym(bills[bills.length - 1].month);
      const first = ym(bills[0].month);
      const sel = this._month;
      let end = last;
      while (shiftMonth(end, -(CHART_MONTHS - 1)) > sel) end = shiftMonth(end, -1);
      const out = [];
      for (let i = CHART_MONTHS - 1; i >= 0; i--) {
        const m = shiftMonth(end, -i);
        if (m >= first) out.push(m);
      }
      return out;
    }
    _chart(defs, unit, opts = {}) {
      const months = this._range();
      return chart({
        months, unit, selected: this._month, ...opts,
        series: defs.map((d) => ({ ...d, data: months.map((m) => { const r = this._row(m); return r ? (r[d.field] ?? null) : null; }) }))
          .filter((s) => s.data.some((v) => v != null)),
      });
    }
    _ours(field, color) { return { name: "우리 집", field, color }; }
    _avg(field) { return { name: "동일면적 평균", field, color: "#e5534b", type: "line" }; }

    _nav() {
      const months = this._data.bills.map((b) => ym(b.month));
      const i = months.indexOf(this._month);
      return `<div class="nav"><button data-month="${months[i - 1] || ""}" ${i <= 0 ? "disabled" : ""} aria-label="이전 달">‹</button>
        <select aria-label="조회 월">${[...months].reverse().map((m) => `<option value="${m}" ${m === this._month ? "selected" : ""}>${label(m)}</option>`).join("")}</select>
        <button data-month="${months[i + 1] || ""}" ${i >= months.length - 1 ? "disabled" : ""} aria-label="다음 달">›</button></div>`;
    }

    _diffs(item, list, prev, year) {
      const find = (row) => {
        if (!row) return undefined;
        const hit = items(row, list).find((i) => key(i.title) === key(item.title));
        return hit ? hit.amount : undefined;
      };
      const calc = (printed, row) => {
        if (printed != null) return printed;
        const ref = find(row);
        return ref === undefined ? null : item.amount - ref;
      };
      return [calc(item.prevMonthDiff, prev), calc(item.lastYearDiff, year)];
    }
    _table(title, list, { hideZero = false } = {}) {
      const m = this._month, row = this._row(m);
      const prev = this._row(shiftMonth(m, -1)), year = this._row(shiftMonth(m, -12));
      const cell = (cur, d) => (d == null ? '<td class="mut">–</td>' : `<td>${delta(d)} <span class="mut ref">(${won(cur - d)})</span></td>`);
      const rows = items(row, list)
        .map((it) => ({ it, d: this._diffs(it, list, prev, year) }))
        .filter(({ it, d }) => !hideZero || it.amount || d[0] || d[1]);
      if (!rows.length) return "";
      const sum = subtotal(row, list);
      const printed = (row[list] || []).find((i) => i.title === "소계") || {};
      const diff = (p, r) => (p != null ? p : r ? sum - subtotal(r, list) : null);
      return `<div class="card"><h3>${title}</h3><div class="scroll"><table><thead><tr><th>항목</th><th>당월</th><th>차액(전월)</th><th>차액(전년동월)</th></tr></thead><tbody>
        ${rows.map(({ it, d }) => `<tr><td>${display(it.title)}</td><td class="cur">${won(it.amount)}</td>${cell(it.amount, d[0])}${cell(it.amount, d[1])}</tr>`).join("")}
        <tr class="sum"><td>소계</td><td>${won(sum)}</td>${cell(sum, diff(printed.prevMonthDiff, prev))}${cell(sum, diff(printed.lastYearDiff, year))}</tr></tbody></table></div></div>`;
    }

    _compareBox(color, head, { usage, amount, unit, avgUsage, avgAmount }) {
      const m = this._month, row = this._row(m);
      const prev = this._row(shiftMonth(m, -1)) || {}, year = this._row(shiftMonth(m, -12)) || {};
      const line = (name, u, a) =>
        `<tr><td>${name}</td>${usage ? `<td>${compared(row[usage], u)}</td>` : ""}<td>${compared(row[amount], a)}</td></tr>`;
      return `<div class="box" style="--c:${color}"><div class="now"><span>${head}</span>${usage ? `<b>${num(row[usage])} ${unit}</b><em>${won(row[amount])}원</em>` : `<b>${won(row[amount])}원</b><em></em>`}</div>
        <div class="scroll"><table><thead><tr><th></th>${usage ? `<th>사용량 (${unit})</th>` : ""}<th>금액 (원)</th></tr></thead><tbody>
        ${line("전월보다", prev[usage], prev[amount])}${line("전년동월보다", year[usage], year[amount])}${line("동일면적평균", row[avgUsage], row[avgAmount])}</tbody></table></div></div>`;
    }
    _section(title, body) { return `<div class="card"><h3>${title}</h3>${body}</div>`; }

    _kinds() {
      return {
        electricity: { name: "전기", color: "#f5a623", unit: "kWh", list: "electricity_items" },
        water: { name: "수도", color: "#42a5f5", unit: "m³", list: "water_items" },
        heating: { name: "난방", color: "#ef6c57", unit: "MWh" },
      };
    }
    _box(k) {
      const c = this._kinds()[k];
      return this._compareBox(c.color, "당월", { usage: `${k}_usage`, amount: `${k}_amount`, unit: c.unit, avgUsage: `${k}_average_usage`, avgAmount: `${k}_average_amount` });
    }
    _usageChart(k) {
      const c = this._kinds()[k];
      return this._chart([this._ours(`${k}_usage`, c.color), this._avg(`${k}_average_usage`)], c.unit);
    }
    _amountChart(k) {
      const c = this._kinds()[k];
      return this._chart([this._ours(`${k}_amount`, c.color), this._avg(`${k}_average_amount`)], "원", { values: false });
    }

    _overview() {
      const row = this._row(this._month);
      const groups = [
        ["전기료", subtotal(row, "electricity_items")], ["수도료", subtotal(row, "water_items")],
      ];
      const named = { 일반관리비: "일반관리비", 청소비: "청소비", 경비비: "경비비", 기본난방비: "기본난방비", 승강기유지비: "승강기유지비" };
      let rest = subtotal(row, "other_items");
      for (const it of items(row, "other_items"))
        if (named[key(it.title)] && it.amount > 0) { groups.push([named[key(it.title)], it.amount]); rest -= it.amount; }
      if (rest > 0) groups.push(["기타", rest]);
      const parts = groups.filter((g) => g[1] > 0).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);
      const range = row.same_area_max
        ? `<tr><td>동일면적 최대</td><td>${won(row.same_area_max)}</td></tr><tr><td>동일면적 최소</td><td>${won(row.same_area_min)}</td></tr>` : "";
      const due = row.due_date ? `<div class="note">납부마감일 ${esc(row.due_date)}</div>` : "";
      const alert = this._data.unpaid > 0 || this._data.overdue > 0
        ? `<div class="card up"><b>미납 ${won(this._data.unpaid)}원 · 연체 ${won(this._data.overdue)}원</b></div>` : "";
      return `${alert}<div class="cols"><div class="col">
        ${this._section("관리비 비교분석", this._compareBox("#f3d46b", "당월", { amount: "total", avgAmount: "same_area_average" }) + (range ? `<table><tbody>${range}</tbody></table>` : "") + due)}
        ${this._section("월별 부과추이", this._chart([this._ours("total", "#7cb342"), this._avg("same_area_average")], "원", { height: 290 }))}
        ${this._section("부과항목별 비율", donut(parts, row.total))}</div><div class="col">
        ${this._section("전기 사용량", this._usageChart("electricity"))}
        ${this._section("수도 사용량", this._usageChart("water"))}
        ${this._section("난방 사용량", this._usageChart("heating"))}
        ${this._section("음식물쓰레기 요금", this._chart([this._ours("food_waste_amount", "#8d6e63"), this._avg("food_waste_average_amount")], "원"))}
        </div></div>`;
    }
    _readings(row) {
      if (!row.readings) return "";
      const names = { electricity: "전기", water: "수도", hot_water: "급탕", heating: "난방" };
      return this._section("당월·전월 사용량별 지침", `<table><thead><tr><th>항목</th><th>당월지침</th><th>전월지침</th><th>차이</th></tr></thead><tbody>${Object.entries(names).filter(([k]) => row.readings[k]).map(([k, n]) => { const r = row.readings[k]; return `<tr><td>${n}</td><td class="cur">${num(r.current)}</td><td>${num(r.previous)}</td><td>${num(Number((r.current - r.previous).toFixed(3)))}</td></tr>`; }).join("")}</tbody></table><div class="note">고지서 PDF에 인쇄된 지침입니다.</div>`);
    }
    _details() {
      const row = this._row(this._month);
      const fee = row.climate_fee != null ? `<small>기후환경요금 ${won(row.climate_fee)}원 · 연료비조정액 ${won(row.fuel_adjustment)}원</small>` : "";
      const disc = items(row, "discount_items").some((i) => i.amount) ? this._table("할인·차감", "discount_items", { hideZero: true }) : "";
      const pdf = row.has_pdf ? `<div class="card"><button data-go="pdf">고지서 원본 PDF 보기</button></div>` : "";
      return `<div class="cols"><div class="col">${this._table(`① 전기료 ${fee}`, "electricity_items")}${this._table("② 수도료", "water_items")}
        <div class="card grand"><span>① + ② + ③ = 당월 부과금액</span><span>${won(row.total)}원</span></div>${this._readings(row)}</div>
        <div class="col">${this._table("③ 그 외 항목", "other_items")}${disc}${pdf}</div></div>`;
    }
    _utility(k) {
      const c = this._kinds()[k];
      const table = c.list ? this._table(`${c.name}료 항목`, c.list) : "";
      const hot = k === "water" ? this._section("급탕 사용량", this._chart([this._ours("hot_water_usage", "#ef6c57")], "m³")) : "";
      const basis = k === "water" ? '<div class="note">수도 금액은 세대 수도요금과 하수도를 더한 값이며, 동일면적 평균과 같은 기준입니다.</div>' : "";
      return `<div class="cols"><div class="col">${this._section(`${c.name} 비교분석`, this._box(k) + basis)}${this._section(`${c.name} 사용량 추이`, this._usageChart(k))}${this._section(`${c.name} 요금 추이`, this._amountChart(k))}</div>
        <div class="col">${table}${hot}</div></div>`;
    }
    _pdf() {
      const months = [...this._data.pdf_months].reverse();
      if (!months.length) return '<div class="card empty">보관된 고지서 PDF가 없습니다.</div>';
      const m = this._month;
      const has = months.includes(m);
      return `<div class="card"><h3>보관된 고지서 원본</h3><div class="months">${months.map((x) => `<button data-month="${x}" class="${x === m ? "on" : ""}">${label(x)}</button>`).join("")}</div></div>
        <div class="card" style="margin-top:12px">${has ? `<div id="pdf" data-pdf="${m}"><div class="empty">불러오는 중…</div></div>` : `<div class="empty">${label(m)} 고지서 PDF는 보관되어 있지 않습니다. 위에서 보관된 달을 선택하세요.</div>`}</div>`;
    }
    async _showPdf() {
      const box = this.shadowRoot.getElementById("pdf");
      if (!box) return;
      try {
        const signed = await this._hass.callWS({
          type: "auth/sign_path", path: `/api/aptree/bill/${this._data.entry_id}/${box.dataset.pdf}`, expires: 3600,
        });
        const url = this._hass.hassUrl ? this._hass.hassUrl(signed.path) : signed.path;
        if (box.isConnected)
          box.innerHTML = `<a class="open" href="${esc(url)}" target="_blank" rel="noreferrer">새 창에서 열기 / 저장</a><iframe src="${esc(url)}" title="관리비 고지서"></iframe>`;
      } catch (err) {
        if (box.isConnected) box.innerHTML = '<div class="empty">고지서를 열지 못했습니다.</div>';
      }
    }

    _render() {
      const root = this.shadowRoot;
      let body;
      if (this._error) body = `<div class="card empty">${this._error}</div>`;
      else if (!this._data || !this._data.bills.length) body = '<div class="card empty">관리비 자료가 아직 없습니다.</div>';
      else {
        const view = this._config.view;
        body = this._nav() + (view === "details" ? this._details()
          : view === "pdf" ? this._pdf()
          : this._kinds()[view] ? this._utility(view) : this._overview());
      }
      root.innerHTML = `<style>${CSS}</style><div class="wrap">${body}</div>`;
      root.querySelectorAll("[data-month]").forEach((el) =>
        el.addEventListener("click", () => el.dataset.month && this._row(el.dataset.month) && this._pick(el.dataset.month)));
      const select = root.querySelector("select");
      if (select) select.addEventListener("change", () => this._pick(select.value));
      root.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => {
        const base = window.location.pathname.split("/").slice(0, 2).join("/");
        window.history.pushState(null, "", `${base}/${el.dataset.go}`);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      }));
      this._showPdf();
    }
    connectedCallback() { if (this._data) this._render(); }
  }

  if (!customElements.get("aptree-fees-card")) {
    customElements.define("aptree-fees-card", AptreeFeesCard);
    window.customCards = window.customCards || [];
    window.customCards.push({ type: "aptree-fees-card", name: "APTREE 관리비", description: "고지서형 관리비 표와 그래프" });
  }
})();
