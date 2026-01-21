(function () {
  function fmt(n) {
    if (!isFinite(n)) return "-";
    return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }

  function getEl(id) { return document.getElementById(id); }

  function readNum(id) {
    const el = getEl(id);
    const v = parseFloat(el ? el.value : "");
    return isFinite(v) ? v : 0;
  }

  function setVal(id, v) {
    const el = getEl(id);
    if (el) el.value = v;
  }

  function readTiming() {
    const el = getEl("timing");
    return el ? el.value : "end"; // end | begin
  }

  function buildShareUrl(p0, pm, r, y, timing) {
    const url = new URL(window.location.href);
    url.searchParams.set("p0", String(p0));
    url.searchParams.set("pm", String(pm));
    url.searchParams.set("r", String(r));
    url.searchParams.set("y", String(y));
    url.searchParams.set("timing", timing);
    return url.toString();
  }

  function applyFromUrl() {
    const sp = new URLSearchParams(window.location.search);
    const p0 = sp.get("p0");
    const pm = sp.get("pm");
    const r  = sp.get("r");
    const y  = sp.get("y");
    const timing = sp.get("timing");

    if (p0 !== null) setVal("p0", p0);
    if (pm !== null) setVal("pm", pm);
    if (r  !== null) setVal("r",  r);
    if (y  !== null) setVal("y",  y);
    if (timing && getEl("timing")) getEl("timing").value = timing;
  }

  function calc() {
    const p0 = Math.max(0, readNum("p0"));
    const pm = Math.max(0, readNum("pm"));
    const rPct = Math.max(0, readNum("r"));
    const y = Math.max(0, readNum("y"));
    const timing = readTiming();

    const r = rPct / 100;
    const months = Math.round(y * 12);
    const rm = r / 12;

    let bal = p0;
    let inTotal = p0;

    const rows = [];
    for (let m = 1; m <= months; m++) {
      if (timing === "begin") {
        // 月初投入
        bal += pm;
        inTotal += pm;
        bal = bal * (1 + rm);
      } else {
        // 月末投入（默认）
        bal = bal * (1 + rm);
        bal += pm;
        inTotal += pm;
      }

      if (m % 12 === 0) {
        const year = m / 12;
        const profit = bal - inTotal;
        rows.push({ year, bal, inTotal, profit });
      }
    }

    const finalVal = bal;
    const profit = finalVal - inTotal;

    const totalInEl = getEl("totalIn");
    const finalEl = getEl("finalVal");
    const profitEl = getEl("profit");

    if (totalInEl) totalInEl.textContent = fmt(inTotal) + " 元";
    if (finalEl) finalEl.textContent = fmt(finalVal) + " 元";
    if (profitEl) profitEl.textContent = fmt(profit) + " 元";

    const tbody = document.querySelector("#tbl tbody");
    if (tbody) {
      tbody.innerHTML = "";
      rows.forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${r.year}</td>
          <td>${fmt(r.bal)}</td>
          <td>${fmt(r.inTotal)}</td>
          <td>${fmt(r.profit)}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    // 实时更新 URL（不刷新页面）
    const shareUrl = buildShareUrl(p0, pm, rPct, y, timing);
    window.history.replaceState({}, "", shareUrl);
  }

  function reset() {
    setVal("p0", 10000);
    setVal("pm", 1000);
    setVal("r", 8);
    setVal("y", 10);
    const t = getEl("timing");
    if (t) t.value = "end";
    calc();
  }

  async function copyShare() {
    const shareUrl = window.location.href;
    try {
      await navigator.clipboard.writeText(shareUrl);
      alert("已复制链接，可直接分享或收藏。");
    } catch {
      // 兼容性兜底
      prompt("复制下面的链接：", shareUrl);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyFromUrl();

    const calcBtn = getEl("calcBtn");
    const resetBtn = getEl("resetBtn");
    const copyBtn = getEl("copyBtn");

    if (calcBtn) calcBtn.addEventListener("click", calc);
    if (resetBtn) resetBtn.addEventListener("click", reset);
    if (copyBtn) copyBtn.addEventListener("click", copyShare);

    ["p0", "pm", "r", "y", "timing"].forEach((id) => {
      const el = getEl(id);
      if (el) el.addEventListener("input", () => calc());
      if (el && el.tagName === "SELECT") el.addEventListener("change", () => calc());
    });

    if (getEl("p0")) calc();
  });
})();
