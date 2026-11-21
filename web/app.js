(() => {
  const SAMPLE_EFT = `[Rifter, Sample Skirmish]
Damage Control II
Counterbalanced Compact Gyrostabilizer
Small Armor Repairer II
Multispectrum Coating II

5MN Y-T8 Compact Microwarpdrive
Faint Epsilon Scoped Warp Scrambler
Fleeting Compact Stasis Webifier

150mm Light AutoCannon II, Republic Fleet EMP S
150mm Light AutoCannon II, Republic Fleet EMP S
150mm Light AutoCannon II, Republic Fleet EMP S

Small Processor Overclocking Unit I
Small Auxiliary Nano Pump I
Small Trimark Armor Pump I
`;

  const el = {
    eftInput: document.getElementById("eft-input"),
    skillsSelect: document.getElementById("skills-select"),
    profileSelect: document.getElementById("profile-select"),
    btnSample: document.getElementById("btn-sample"),
    btnCopyEft: document.getElementById("btn-copy-eft"),
    btnCalc: document.getElementById("btn-calc"),
    btnAdvise: document.getElementById("btn-advise"),
    errorBox: document.getElementById("error-box"),
    validationBox: document.getElementById("validation-box"),
    fitTitle: document.getElementById("fit-title"),
    fitCost: document.getElementById("fit-cost"),
    shipRender: document.getElementById("ship-render"),
    wheelArcs: document.getElementById("wheel-arcs"),
    wheelSlots: document.getElementById("wheel-slots"),
    slotTooltip: document.getElementById("slot-tooltip"),
    resCpuText: document.getElementById("res-cpu-text"),
    resCpuBar: document.getElementById("res-cpu-bar"),
    resPgText: document.getElementById("res-pg-text"),
    resPgBar: document.getElementById("res-pg-bar"),
    resCalText: document.getElementById("res-cal-text"),
    resCalBar: document.getElementById("res-cal-bar"),
    capBadge: document.getElementById("cap-badge"),
    statsContent: document.getElementById("stats-content"),
    advisorSection: document.getElementById("advisor-section"),
    advisorMeta: document.getElementById("advisor-meta"),
    advisorSummary: document.getElementById("advisor-summary"),
    advisorCards: document.getElementById("advisor-cards"),
    versionFooter: document.getElementById("version-footer"),
  };

  let currentCanonicalEft = "";

  function fmtIsk(val) {
    if (val === null || val === undefined) return "price unknown";
    if (val >= 1e9) return `${(val / 1e9).toFixed(2)}B ISK`;
    if (val >= 1e6) return `${(val / 1e6).toFixed(2)}M ISK`;
    if (val >= 1e3) return `${(val / 1e3).toFixed(1)}k ISK`;
    return `${Math.round(val)} ISK`;
  }

  function fmtDuration(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return m > 0 ? `${m}m ${rem}s` : `${rem}s`;
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function getSkillPayload() {
    const val = el.skillsSelect.value;
    if (val.startsWith("char:")) {
      return { skills_preset: "custom", character_id: Number(val.slice(5)) };
    }
    return { skills_preset: val, character_id: null };
  }

  function showError(msg) {
    if (!msg) {
      el.errorBox.classList.add("hidden");
      el.errorBox.innerHTML = "";
      return;
    }
    el.errorBox.classList.remove("hidden");
    el.errorBox.innerHTML = escapeHtml(msg);
  }

  function showValidation(validation) {
    if (!validation || validation.valid) {
      el.validationBox.classList.add("hidden");
      el.validationBox.innerHTML = "";
      return;
    }
    const items = validation.issues
      .map((i) => `<li><strong>${escapeHtml(i.code)}</strong>: ${escapeHtml(i.message)}</li>`)
      .join("");
    el.validationBox.classList.remove("hidden");
    el.validationBox.innerHTML = `<strong>Fit Validation Failed (${validation.issues.length} issue${validation.issues.length === 1 ? "" : "s"}):</strong><ul>${items}</ul>`;
  }

  function setBar(barEl, textEl, used, output, unit) {
    const pct = output > 0 ? (used / output) * 100 : (used > 0 ? 100 : 0);
    barEl.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    barEl.classList.toggle("bar-over", used > output + 1e-4);
    textEl.textContent = `${used.toFixed(unit ? 1 : 0)} / ${output.toFixed(unit ? 1 : 0)}${unit ? " " + unit : ""}`;
  }

  function renderWheel(stats) {
    el.shipRender.setAttribute("href", `/api/images/render/${stats.ship_type_id}`);
    el.wheelArcs.innerHTML = "";
    el.wheelSlots.innerHTML = "";

    const cx = 260, cy = 260, radius = 196;
    const svgNS = "http://www.w3.org/2000/svg";

    // Group modules by slot type
    const bySlot = { high: [], med: [], low: [], rig: [], subsystem: [] };
    for (const m of stats.modules || []) {
      if (bySlot[m.slot]) bySlot[m.slot].push(m);
    }

    // Pad up to ship's total slots with empty placeholders for visual fidelity
    for (const slot of ["high", "med", "low", "rig", "subsystem"]) {
      const total = (stats.slots_total && stats.slots_total[slot]) || 0;
      while (bySlot[slot].length < total) {
        bySlot[slot].push({ slot, name: "", empty: true, state: "offline" });
      }
    }

    // Angular ranges (degrees, 0 = 3 o'clock, -90 = 12 o'clock)
    const arcs = [
      { key: "high", start: -145, end: -35, color: "#ef4444", r: radius },
      { key: "med", start: -20, end: 70, color: "#38bdf8", r: radius },
      { key: "low", start: 85, end: 175, color: "#f59e0b", r: radius },
      { key: "rig", start: 195, end: 235, color: "#a78bfa", r: 158 },
      { key: "subsystem", start: -175, end: -150, color: "#34d399", r: 158 },
    ];

    for (const arc of arcs) {
      const list = bySlot[arc.key];
      if (!list || list.length === 0) continue;

      const count = list.length;
      list.forEach((mod, idx) => {
        const frac = count === 1 ? 0.5 : idx / (count - 1);
        const deg = arc.start + frac * (arc.end - arc.start);
        const rad = (deg * Math.PI) / 180;
        const x = cx + arc.r * Math.cos(rad);
        const y = cy + arc.r * Math.sin(rad);

        const g = document.createElementNS(svgNS, "g");
        g.setAttribute("class", "slot-node");

        const ring = document.createElementNS(svgNS, "circle");
        ring.setAttribute("class", "slot-ring");
        ring.setAttribute("cx", x.toFixed(1));
        ring.setAttribute("cy", y.toFixed(1));
        ring.setAttribute("r", "19");
        ring.setAttribute("fill", mod.empty ? "#09101b" : "#111d30");
        ring.setAttribute("stroke", mod.state === "offline" ? "#475569" : arc.color);
        ring.setAttribute("stroke-width", mod.empty ? "1.2" : "2");
        if (mod.empty) ring.setAttribute("stroke-dasharray", "3 3");
        g.appendChild(ring);

        if (!mod.empty && mod.type_id) {
          const img = document.createElementNS(svgNS, "image");
          img.setAttribute("x", (x - 14).toFixed(1));
          img.setAttribute("y", (y - 14).toFixed(1));
          img.setAttribute("width", "28");
          img.setAttribute("height", "28");
          img.setAttribute("href", `/api/images/icon/${mod.type_id}`);
          if (mod.state === "offline") img.setAttribute("opacity", "0.45");
          g.appendChild(img);
        }

        const label = mod.empty
          ? `Empty ${arc.key} slot`
          : `${mod.name}${mod.charge ? " — " + mod.charge : ""} (${mod.state})`;

        g.addEventListener("mouseenter", () => {
          el.slotTooltip.textContent = label;
          el.slotTooltip.classList.remove("hidden");
        });
        g.addEventListener("mouseleave", () => {
          el.slotTooltip.classList.add("hidden");
        });

        el.wheelSlots.appendChild(g);
      });
    }
  }

  function renderStats(stats, cost) {
    el.fitTitle.textContent = `[${stats.ship}] ${stats.fit_name}`;
    el.fitCost.textContent = fmtIsk(cost);

    setBar(el.resCpuBar, el.resCpuText, stats.cpu_used, stats.cpu_output, "tf");
    setBar(el.resPgBar, el.resPgText, stats.pg_used, stats.pg_output, "MW");
    setBar(el.resCalBar, el.resCalText, stats.calibration_used, stats.calibration_output, "");

    if (stats.cap_stable) {
      el.capBadge.textContent = `Cap Stable ${(stats.cap_stable_pct || 0).toFixed(1)}%`;
      el.capBadge.className = "badge ok";
    } else {
      el.capBadge.textContent = `Cap Lasts ${fmtDuration(stats.cap_duration_s)}`;
      el.capBadge.className = "badge warn";
    }

    const rS = stats.resists_shield;
    const rA = stats.resists_armor;
    const rH = stats.resists_hull;

    el.statsContent.innerHTML = `
      <div class="stat-card">
        <h3>Offense</h3>
        <div class="stat-kv"><span>Total DPS</span><span class="val">${stats.dps_total.toFixed(1)}</span></div>
        <div class="stat-kv"><span>Weapon / Drone</span><span class="val">${stats.dps_weapon.toFixed(1)} / ${stats.dps_drone.toFixed(1)}</span></div>
        <div class="stat-kv"><span>Alpha Volley</span><span class="val">${stats.volley_total.toFixed(1)}</span></div>
      </div>

      <div class="stat-card">
        <h3>Defense &amp; Resists</h3>
        <div class="stat-kv"><span>Total EHP</span><span class="val">${Math.round(stats.ehp_total).toLocaleString()}</span></div>
        <div class="stat-kv"><span>Shield / Armor / Hull EHP</span><span class="val">${Math.round(stats.ehp_shield).toLocaleString()} / ${Math.round(stats.ehp_armor).toLocaleString()} / ${Math.round(stats.ehp_hull).toLocaleString()}</span></div>
        <div class="stat-kv"><span>Sustained Tank (Shd / Arm)</span><span class="val">${(stats.tank_passive_shield + stats.tank_shield_repair).toFixed(1)} / ${stats.tank_armor_repair.toFixed(1)} EHP/s</span></div>
        <table class="resist-table">
          <thead><tr><th>Layer</th><th>EM</th><th>Thm</th><th>Kin</th><th>Exp</th></tr></thead>
          <tbody>
            <tr><td>Shield</td><td>${rS.em.toFixed(0)}%</td><td>${rS.thermal.toFixed(0)}%</td><td>${rS.kinetic.toFixed(0)}%</td><td>${rS.explosive.toFixed(0)}%</td></tr>
            <tr><td>Armor</td><td>${rA.em.toFixed(0)}%</td><td>${rA.thermal.toFixed(0)}%</td><td>${rA.kinetic.toFixed(0)}%</td><td>${rA.explosive.toFixed(0)}%</td></tr>
            <tr><td>Hull</td><td>${rH.em.toFixed(0)}%</td><td>${rH.thermal.toFixed(0)}%</td><td>${rH.kinetic.toFixed(0)}%</td><td>${rH.explosive.toFixed(0)}%</td></tr>
          </tbody>
        </table>
      </div>

      <div class="stat-card">
        <h3>Navigation &amp; Capacitor</h3>
        <div class="stat-kv"><span>Max Speed</span><span class="val">${stats.max_speed.toFixed(1)} m/s</span></div>
        <div class="stat-kv"><span>Align Time</span><span class="val">${stats.align_time.toFixed(2)} s</span></div>
        <div class="stat-kv"><span>Sig Radius</span><span class="val">${stats.signature_radius.toFixed(1)} m</span></div>
        <div class="stat-kv"><span>Cap Delta</span><span class="val">${stats.cap_delta >= 0 ? "+" : ""}${stats.cap_delta.toFixed(2)} GJ/s</span></div>
      </div>
    `;
  }

  function deltaSpan(diff, unit = "", digits = 1) {
    const cls = diff > 0.05 ? "delta-pos" : diff < -0.05 ? "delta-neg" : "muted";
    const sign = diff > 0 ? "+" : "";
    return `<span class="${cls}">(${sign}${diff.toFixed(digits)}${unit})</span>`;
  }

  function renderAdvisor(data) {
    el.advisorSection.classList.remove("hidden");
    el.advisorSummary.textContent = data.summary || "No summary provided.";
    el.advisorMeta.textContent = `Profile: ${data.profile} | Tool calls: ${data.tool_calls_made} | Discarded invalid: ${data.discarded_proposals.length}`;

    if (!data.recommendations || data.recommendations.length === 0) {
      el.advisorCards.innerHTML = `<p class="muted">No legal recommendations passed engine validation.</p>`;
      return;
    }

    const base = data.baseline_stats;
    el.advisorCards.innerHTML = "";
    data.recommendations.forEach((rec, idx) => {
      const s = rec.stats;
      const card = document.createElement("div");
      card.className = "rec-card";
      card.innerHTML = `
        <div class="panel-header">
          <strong>Option #${idx + 1} — ${escapeHtml(s.fit_name)}</strong>
          <span class="badge">${fmtIsk(rec.total_cost_isk)}</span>
        </div>
        <p class="rec-reasoning">${escapeHtml(rec.reasoning)}</p>
        <div class="stat-kv"><span>DPS</span><span class="val">${s.dps_total.toFixed(1)} ${deltaSpan(s.dps_total - base.dps_total)}</span></div>
        <div class="stat-kv"><span>EHP</span><span class="val">${Math.round(s.ehp_total).toLocaleString()} ${deltaSpan(s.ehp_total - base.ehp_total, "", 0)}</span></div>
        <div class="stat-kv"><span>Speed</span><span class="val">${s.max_speed.toFixed(1)} m/s ${deltaSpan(s.max_speed - base.max_speed)}</span></div>
        <div class="stat-kv"><span>Capacitor</span><span class="val">${s.cap_stable ? `Stable ${(s.cap_stable_pct || 0).toFixed(0)}%` : `Lasts ${fmtDuration(s.cap_duration_s)}`}</span></div>
        <div class="action-row">
          <button class="btn btn-ghost btn-sm btn-load-rec" type="button">Load in Wheel</button>
          <button class="btn btn-ghost btn-sm btn-copy-rec" type="button">Copy EFT</button>
        </div>
      `;
      card.querySelector(".btn-load-rec").addEventListener("click", () => {
        el.eftInput.value = rec.eft_text;
        runCalculate();
      });
      card.querySelector(".btn-copy-rec").addEventListener("click", () => {
        navigator.clipboard?.writeText(rec.eft_text);
      });
      el.advisorCards.appendChild(card);
    });
  }

  async function runCalculate() {
    showError("");
    const text = el.eftInput.value.trim();
    if (!text) {
      showError("Paste an EFT fit first.");
      return;
    }
    el.btnCalc.disabled = true;
    try {
      const res = await fetch("/api/calculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ eft: text, ...getSkillPayload() }),
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = typeof data.detail === "string" ? data.detail : data.detail?.message || "Calculation failed";
        showError(msg);
        return;
      }
      currentCanonicalEft = data.eft_canonical;
      showValidation(data.validation);
      renderWheel(data.stats);
      renderStats(data.stats, data.total_cost_isk);
    } catch (err) {
      showError(`Request failed: ${err.message}`);
    } finally {
      el.btnCalc.disabled = false;
    }
  }

  async function runAdvise() {
    showError("");
    const text = el.eftInput.value.trim();
    if (!text) {
      showError("Paste an EFT fit first.");
      return;
    }
    el.btnAdvise.disabled = true;
    el.btnAdvise.textContent = "Advising...";
    try {
      const res = await fetch("/api/advise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          eft: text,
          profile: el.profileSelect.value || "pve",
          ...getSkillPayload(),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = typeof data.detail === "string" ? data.detail : data.detail?.message || "Advisor failed";
        showError(msg);
        return;
      }
      renderAdvisor(data);
    } catch (err) {
      showError(`Advisor request failed: ${err.message}`);
    } finally {
      el.btnAdvise.disabled = false;
      el.btnAdvise.textContent = "Run Advisor";
    }
  }

  async function loadMeta() {
    try {
      const res = await fetch("/api/meta");
      const meta = await res.json();
      el.versionFooter.textContent = `Pyfa ${meta.pyfa_version} | Gamedata ${meta.gamedata_version} | LLM: ${meta.llm_provider}`;
      el.profileSelect.innerHTML = "";
      for (const p of meta.profiles || []) {
        const opt = document.createElement("option");
        opt.value = p.key;
        opt.textContent = p.name;
        if (p.key === "pve") opt.selected = true;
        el.profileSelect.appendChild(opt);
      }
      for (const ch of meta.characters || []) {
        const opt = document.createElement("option");
        opt.value = `char:${ch.character_id}`;
        opt.textContent = `${ch.character_name} (ESI)`;
        el.skillsSelect.appendChild(opt);
      }
    } catch {
      el.versionFooter.textContent = "Unable to load metadata";
    }
  }

  el.btnSample.addEventListener("click", () => {
    el.eftInput.value = SAMPLE_EFT;
    runCalculate();
  });
  el.btnCalc.addEventListener("click", runCalculate);
  el.btnAdvise.addEventListener("click", runAdvise);
  el.btnCopyEft.addEventListener("click", () => {
    const out = currentCanonicalEft || el.eftInput.value;
    if (out) navigator.clipboard?.writeText(out);
  });

  loadMeta();
})();
