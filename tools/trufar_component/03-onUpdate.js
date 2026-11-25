// 贴到平台的「页面更新事件响应（onUpdate）」。
//
// 平台给到的变量：dom / data / prevData / props
//   data     —— 当前最新的页面数据
//   prevData —— 更新前的数据
//
// 这个块很薄：只负责"从哪取数据"和"什么时候重画"。真正的渲染在 onMount
// 里定义的 dom.__elench.render（它先跑，所以一定存在）。

(function () {
  "use strict";

  var st = dom.__elench;
  if (!st || typeof st.render !== "function") {
    // 走到这里说明 onMount 没跑成（被删了或被改了）。不要静默失败——
    // 静默失败会让人以为是"数据没来"，实际是"挂载没成"。
    var host = dom.querySelector('[data-role="fns"]');
    if (host) {
      host.innerHTML = '<div class="ec-err">组件没挂载成功：onMount 没有定义渲染器。' +
                       "四段代码要一起贴。</div>";
    }
    return;
  }

  // 数据可能在 data.elench，也可能在 props.elench（取决于工作流把变量挂在哪）
  var cur = (data && (data.elench || data.result && data.result.elench)) ||
            (props && (props.elench || (props.data && props.data.elench))) || null;
  var prev = (prevData && (prevData.elench || prevData.result && prevData.result.elench)) || null;

  if (!cur) {
    // 没有结果：如果是"第一次还没算"，保持空状态；如果"上次有、这次没了"，
    // 要明说，否则页面会一直显示旧结果 —— 那是最容易误导人的一种 bug。
    if (st.last) {
      var el = st.el || {};
      if (el.badge) { el.badge.className = "ec-badge unk"; el.badge.textContent = "无结果"; }
      if (el.fns) el.fns.innerHTML = '<div class="ec-note">这一轮没有拿到 elench 结果（上一次的结果已作废，不再显示）。</div>';
      if (el.heat) { el.heat.hidden = true; el.heat.innerHTML = ""; }
      if (el.diags) el.diags.innerHTML = "";
      if (el.foot) el.foot.textContent = "data.elench 为空";
      st.last = null;
    }
    return;
  }

  st.render(cur, prev || st.last);
})();
