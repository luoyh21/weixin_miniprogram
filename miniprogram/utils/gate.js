// 前端展示总开关（由服务端控制，改开关无需重新提交小程序版本）。
//
//   real=true  → 显示真实内容（速递/专题/问答）
//   real=false → 显示计算器 + 登录注册（默认；未知/网络失败时也按此处理）
//
// 服务端开关位置：weixin_miniprogram/backend/data/gate.json，例如 {"real": true}
// （改文件即时生效，无需重启、无需重新提交小程序）。也可用环境变量 MP_SHOW_REAL=1。
//
// 判定取「上次拉到并缓存的值」做同步判断（保证页面 onLoad/onShow 能即时决策）；
// 每次进入页面再后台拉取最新开关，若发生变化则对当前页重新路由。
const api = require('./api');

const CACHE_KEY = 'gate_real_v1';
const REAL_PAGES = ['/pages/news/news', '/pages/topic/topic', '/pages/ask/ask'];
const CALC_PAGE = '/pages/calc/calc';
const HOME_PAGE = '/pages/news/news';

let _real = null; // null=未知；true/false=已知

function _loadCache() {
  if (_real === null) {
    try {
      const v = wx.getStorageSync(CACHE_KEY);
      if (v === true || v === false) _real = v;
    } catch (e) { /* ignore */ }
  }
  return _real;
}

// 同步判定：是否展示真实内容。未知按 false（计算器）处理。
function isReal() {
  return _loadCache() === true;
}

// 受限态 = 非真实态（展示计算器/登录注册）。
function restricted() {
  return !isReal();
}

// 后台拉取最新开关，更新缓存。返回 Promise<{ real, changed }>。
function refresh() {
  return api.get('/gate', { auth: false })
    .then((res) => {
      const real = !!(res && res.real);
      const changed = real !== _real;
      _real = real;
      try { wx.setStorageSync(CACHE_KEY, real); } catch (e) { /* ignore */ }
      return { real, changed };
    })
    .catch(() => ({ real: isReal(), changed: false }));
}

// 开关变化后，对「当前页」重新路由：真实↔计算器之间切换；不需切页则刷新底栏并通知页面。
function applyToCurrentPage() {
  const pages = getCurrentPages();
  const cur = pages && pages[pages.length - 1];
  if (!cur) return;
  const route = '/' + cur.route;
  const real = isReal();
  if (real && route === CALC_PAGE) { wx.reLaunch({ url: HOME_PAGE }); return; }
  if (!real && REAL_PAGES.indexOf(route) >= 0) { wx.reLaunch({ url: CALC_PAGE }); return; }
  const tb = cur.getTabBar && cur.getTabBar();
  if (tb) tb.refresh();
  if (typeof cur.onGateChange === 'function') cur.onGateChange(real);
}

module.exports = {
  CACHE_KEY,
  HOME_PAGE,
  CALC_PAGE,
  isReal,
  restricted,
  refresh,
  applyToCurrentPage,
};
