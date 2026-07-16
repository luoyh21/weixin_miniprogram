const api = require('../../utils/api');
const gate = require('../../utils/gate');

// 专题数量少（个位数），搜索直接在已加载数据上做客户端模糊匹配，无需请求后端。
// scope: 'all'（标题+简介，默认） | 'title'（只匹配标题，更精确）。
function filterTopics(topics, q, scope) {
  q = (q || '').trim().toLowerCase();
  if (!q) return topics;
  return (topics || []).filter((t) => {
    const hay = scope === 'title' ? (t.title || '') : ((t.title || '') + ' ' + (t.intro || ''));
    return hay.toLowerCase().indexOf(q) >= 0;
  });
}

Page({
  data: {
    topics: [],
    shown: [],
    searchText: '',
    searchScope: 'all', // all | title
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
        this.setData({
          topics, shown: filterTopics(topics, this.data.searchText, this.data.searchScope), loading: false,
        });
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
    this.setData({ searchText: q, shown: filterTopics(this.data.topics, q, this.data.searchScope) });
  },

  clearSearch() {
    this.setData({ searchText: '', shown: this.data.topics });
  },

  switchSearchScope(e) {
    const scope = e.currentTarget.dataset.scope;
    if (scope === this.data.searchScope) return;
    this.setData({
      searchScope: scope, shown: filterTopics(this.data.topics, this.data.searchText, scope),
    });
  },

  openTopic(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/topic_view/topic_view?id=' + id });
  },
});
