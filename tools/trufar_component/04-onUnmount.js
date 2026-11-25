// 贴到平台的「取消挂载事件响应（onUnmount）」。
//
// 平台给到的变量：props
//
// 诚实说明：这个组件**没有定时器、没有全局监听器、没有第三方库实例**，
// 所以它其实不需要清理。保留这一段是为了两件事：
//   ① 以后加定时器/监听器时，别忘了在这里摘掉；
//   ② 把缓存的 DOM 引用清空，避免平台复用节点时读到上一次的结果。
//
// 平台调用 onUnmount 时**只给 props**，拿不到 dom。所以这里用 try/catch
// 加存在性判断：能清就清，清不了也不阻断卸载（但会在控制台留一句，
// 不假装清理成功）。

(function () {
  "use strict";

  var owner = (typeof dom !== "undefined" && dom && dom.__elench) ? dom : null;

  if (!owner) {
    // 拿不到 dom：这是平台的接口限制，不是错误。但把话说出来，
    // 免得以后有人以为"清理已经做了"。
    if (typeof console !== "undefined" && console.debug) {
      console.debug("[elench] onUnmount 拿不到 dom：本组件无定时器/监听器，无需清理。");
    }
    return;
  }

  try {
    var st = owner.__elench;
    st.render = null;
    st.last = null;
    st.el = {};
    try { delete owner.__elench; } catch (e) { owner.__elench = null; }
  } catch (e) {
    if (typeof console !== "undefined" && console.warn) {
      console.warn("[elench] onUnmount 清理时出错：", e);
    }
  }
})();
