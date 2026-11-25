// 贴到平台的「挂载事件响应（onMount）」。
//
// 平台给到的变量：dom / value / onChange / props
//
// ⚠ 这个块里带着**渲染器**（dom.__elench.render）。它比 onUpdate 先跑，
//   所以 onUpdate 可以直接用。四段代码是一起贴的，别单独删这一段。
//
// 不做网络请求、不引外部库：全部数据来自平台注入的 props / data。

(function () {
  "use strict";

  var st = (dom.__elench = { el: {}, last: null });

  var q = function (role) { return dom.querySelector('[data-role="' + role + '"]'); };
  st.el = { badge: q("badge"), fns: q("fns"), heat: q("heat"),
            diags: q("diags"), empty: q("empty"), foot: q("foot") };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function badgeClass(v) {
    if (v === "ok") return "ok";
    if (v === "debt") return "debt";
    if (v === "unknown") return "unk";
    return "unk";
  }

  function badgeText(v) {
    if (v === "ok") return "达成";
    if (v === "debt") return "债务（超出承诺）";
    if (v === "unknown") return "无法判定";
    // 第四档：没有 .tu 就没有契约可判。既不是达成也不是债务，别显示英文原词。
    if (v === "undeclared") return "未声明契约";
    return v ? String(v) : "（无判决）";
  }

  // 判决的"差"程度：unknown 最差（连结论都没有），其次 debt，最好 ok。
  function rank(v) { return v === "ok" ? 2 : (v === "debt" ? 1 : 0); }
  // `undeclared` 不进这个排序（它不在判定范围内）

  function renderHeat(lines) {
    var host = st.el.heat;
    if (!host) return;
    if (!Array.isArray(lines) || !lines.length) { host.hidden = true; host.innerHTML = ""; return; }
    var max = 1;
    lines.forEach(function (x) { if (x && x.hits > max) max = x.hits; });
    var html = "";
    lines.forEach(function (x) {
      if (!x) return;
      var h = Math.max(2, Math.round(46 * (x.hits || 0) / max));
      html += '<i style="height:' + h + 'px" title="第 ' + esc(x.n) + " 行：" +
              esc(x.hits) + ' 次"></i>';
    });
    host.innerHTML = html;
    host.hidden = false;
  }

  function render(d, prev) {
    if (!d) return;
    var el = st.el;
    st.last = d;

    if (el.empty) el.empty.hidden = true;

    // ---- 工具没跑成：这是"组件/流程"的问题，不是"代码不达标"
    if (d.ok === false && d.error) {
      if (el.badge) { el.badge.className = "ec-badge err"; el.badge.textContent = "调用失败"; }
      if (el.fns) el.fns.innerHTML = '<div class="ec-err">' + esc(d.error) + "</div>";
      if (el.heat) { el.heat.hidden = true; el.heat.innerHTML = ""; }
      if (el.diags) el.diags.innerHTML = "";
      if (el.foot) el.foot.textContent = "工具没跑成：先检查工作流传进来的 tu / eln 是否是字符串";
      return;
    }

    // ---- 顶层判决（多函数时后端已取最差）
    var verdict = d.verdict || null;
    if (!verdict && d.functions) {
      var vs = Object.keys(d.functions).map(function (k) { return d.functions[k].verdict; });
      vs.forEach(function (v) { if (rank(v) < rank(verdict)) verdict = v; });
    }

    if (el.badge) {
      el.badge.className = "ec-badge " + badgeClass(verdict);
      el.badge.textContent = badgeText(verdict);
    }

    // ---- 变差时闪一下（用 prevData 对比；只在"真的变差"时动）
    if (prev) {
      var pv = prev.verdict || null;
      if (pv && verdict && rank(verdict) < rank(pv)) {
        dom.classList.remove("ec-worse");
        void dom.offsetWidth;             // 强制重排，让动画能重新触发
        dom.classList.add("ec-worse");
      }
    }

    // ---- 每个函数：κ 与分配
    var html = "";
    var fns = d.functions || {};
    var names = Object.keys(fns);
    if (!names.length && d.op === "check") {
      html = '<div class="ec-note">静态检查没有返回函数（可能解析就失败了，见下面的诊断）。</div>';
    }
    names.forEach(function (n) {
      var f = fns[n] || {};
      var changed = false;
      if (prev && prev.functions && prev.functions[n]) {
        changed = prev.functions[n].kappa !== f.kappa;
      }
      html += '<div class="ec-fn" data-fn="' + esc(n) + '">' +
                '<div class="ec-fn-name">' + esc(n) + "</div>";
      if (f.kappa !== undefined && f.kappa !== null) {
        html += '<div class="ec-kappa' + (changed ? " ec-fade" : "") + '">κ ≤ ' + esc(f.kappa) + "</div>";
      }
      if (f.explanation) {
        html += '<div class="ec-note">' + esc(f.explanation) + "</div>";
      }
      if (f.allocation_verdict) {
        html += '<div class="ec-note">分配：' + esc(f.allocation) + "　" +
                esc(badgeText(f.allocation_verdict)) +
                (f.allocation_explanation ? "　" + esc(f.allocation_explanation) : "") + "</div>";
      }
      html += "</div>";
    });
    if (el.fns) el.fns.innerHTML = html;

    renderHeat(d.lines);

    // ---- 诊断（带行列与改法）
    var dh = "";
    var ds = d.diagnostics || [];
    if (ds.length) {
      ds.forEach(function (x) {
        dh += '<div class="ec-diag">' +
                '<span class="ec-code">' + esc(x.code) + "</span> " +
                '<span class="ec-where">@' + esc(x.line) + ":" + esc(x.col) + "</span>　" +
                esc(x.message) +
                (x.fix ? '<div class="ec-fix">改法：' + esc(x.fix) + "</div>" : "") +
              "</div>";
      });
    }
    if (el.diags) el.diags.innerHTML = dh;

    // ---- 页脚
    if (el.foot) {
      var bits = [];
      if (d.op) bits.push(d.op === "cost" ? "成本判定" : "静态检查");
      if (d.elapsed_ms != null) bits.push(d.elapsed_ms + " ms");
      if (d.exit_code != null) bits.push("exit " + d.exit_code);
      el.foot.textContent = bits.join(" · ");
    }

    dom.classList.toggle("ec-bad", verdict === "debt" || verdict === "unknown");
  }

  st.render = render;

  // 初始数据：可能在 props 里（工作流已经算好），也可能还没有。
  var seed = (props && (props.elench || (props.data && props.data.elench))) || null;
  if (seed) {
    render(seed, null);
  } else {
    if (st.el.empty) st.el.empty.hidden = false;
    if (st.el.foot) st.el.foot.textContent = "等待工作流把 elench_cost 的结果传进来";
  }
})();
