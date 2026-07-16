const gate = require('../../utils/gate');

// 审核受限期展示的占位列表：仅「全部」栏，每条是一道乘法算式及其结果（纯静态演示，无实际信息）。
// 结果由 a*b 现算而非硬编码，避免手误写错。
const PAIRS = [
  [6, 7], [8, 9], [12, 4], [15, 3], [9, 11],
  [13, 6], [7, 14], [16, 5], [21, 3], [19, 4],
  [17, 6], [23, 3], [22, 5], [14, 9], [25, 4],
];

const ITEMS = PAIRS.map(([a, b], i) => ({
  id: 'calc' + (i + 1),
  title: a + ' × ' + b + ' = ' + (a * b),
  summary: a + ' 乘以 ' + b + ' 的计算结果是 ' + (a * b) + '。',
  meta: '第 ' + (i + 1) + ' 题 / 共 ' + PAIRS.length + ' 题',
}));

Page({
  data: {
    items: ITEMS,
  },

  onLoad() {
    this._guard();
  },

  onShow() {
    if (this._guard()) return;
    const tb = this.getTabBar && this.getTabBar();
    if (tb) {
      tb.refresh();
      tb.setSelectedByPath('/pages/calc/calc');
    }
    gate.refresh().then((r) => { if (r.changed) gate.applyToCurrentPage(); });
  },

  // 开关切到真实态：占位页不再展示，回到速递。
  onGateChange(real) {
    if (real) wx.reLaunch({ url: '/pages/news/news' });
  },

  // 开关为真实态时，占位页不展示，回到速递。
  _guard() {
    if (!gate.restricted()) {
      wx.reLaunch({ url: '/pages/news/news' });
      return true;
    }
    return false;
  },
});
