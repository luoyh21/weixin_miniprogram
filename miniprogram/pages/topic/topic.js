const api = require('../../utils/api');
const gate = require('../../utils/gate');

// 专题数量少（个位数），搜索直接在已加载数据上做客户端模糊匹配，无需请求后端。
function filterTopics(topics, q) {
  q = (q || '').trim().toLowerCase();
  if (!q) return topics;
  return (topics || []).filter((t) =>
    ((t.title || '') + ' ' + (t.intro || '')).toLowerCase().indexOf(q) >= 0);
}

Page({
  data: {
    topics: [],
    shown: [],
    searchText: '',
    loading: true,
    error: '',
  },

  onShow() {
    if (gate.restricted()) { wx.reLaunch({ url: '/pages/calc/calc' }); return; }
    const tb = this.getTabBar && this.getTabBar();
    if (tb) { tb.refresh(); tb.setSelectedByPath('/pages/topic/topic'); }
    gate.refresh().then((r) => { if (r.changed) gate.applyToCurrentPage(); });
    this.load();
  },

  onPullDownRefresh() {
    this.load(true);
  },

  load(fromPull) {
    this.setData({ loading: true, error: '' });
    api.get('/topic/list', { auth: false })
      .then((res) => {
        const topics = res.topics || [];
        this.setData({ topics, shown: filterTopics(topics, this.data.searchText), loading: false });
      })
      .catch((e) => {
        this.setData({ loading: false, error: e.message || '加载失败' });
      })
      .then(() => {
        if (fromPull) wx.stopPullDownRefresh();
      });
  },

  onSearchInput(e) {
    const q = e.detail.value;
    this.setData({ searchText: q, shown: filterTopics(this.data.topics, q) });
  },

  clearSearch() {
    this.setData({ searchText: '', shown: this.data.topics });
  },

  openTopic(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/topic_view/topic_view?id=' + id });
  },
});
