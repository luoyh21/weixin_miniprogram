const gate = require('../../utils/gate');

Page({
  data: {
    // 审核受限期展示的占位列表：仅「全部」栏，3 条无实际信息的测试内容。
    items: [{ id: 't1' }, { id: 't2' }, { id: 't3' }],
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
