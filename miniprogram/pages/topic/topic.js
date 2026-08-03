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

const REQUEST_STATUS = {
  pending: '待超管审批',
  queued: '等待执行',
  running: '生成中',
  done: '已生成',
  rejected: '已拒绝',
  failed: '执行失败',
};

Page({
  data: {
    topics: [],
    shown: [],
    searchText: '',
    searchScope: 'all', // all | title
    loading: true,
    error: '',
    isAdmin: false,
    showApply: false,
    applyTitle: '',
    applyIntro: '',
    applyKeywords: '',
    applying: false,
    myRequests: [],
  },

  onShow() {
    if (gate.restricted()) { wx.reLaunch({ url: '/pages/calc/calc' }); return; }
    const tb = this.getTabBar && this.getTabBar();
    if (tb) { tb.refresh(); tb.setSelectedByPath('/pages/topic/topic'); }
    const user = getApp().globalData.user || {};
    this.setData({ isAdmin: !!user.is_admin });
    gate.refresh().then((r) => { if (r.changed) gate.applyToCurrentPage(); });
    this.load();
    if (user.is_admin) this.loadMyRequests();
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

  toggleApply() {
    this.setData({ showApply: !this.data.showApply });
  },

  onApplyInput(e) {
    this.setData({ [e.currentTarget.dataset.field]: e.detail.value });
  },

  loadMyRequests() {
    api.get('/topic/requests/mine')
      .then((res) => {
        const rows = (res.requests || []).map((row) => ({
          ...row,
          statusLabel: REQUEST_STATUS[row.status] || row.status,
        }));
        this.setData({ myRequests: rows });
      })
      .catch(() => {});
  },

  submitApply() {
    const title = (this.data.applyTitle || '').trim();
    if (title.length < 2) return wx.showToast({ title: '请输入专题名称', icon: 'none' });
    const keywords = (this.data.applyKeywords || '').split(/[\s,，;；]+/).filter(Boolean);
    this.setData({ applying: true });
    api.post('/topic/apply', {
      title,
      intro: (this.data.applyIntro || '').trim(),
      keywords: keywords.length ? keywords : [title],
    })
      .then((res) => {
        const request = res.request || {};
        wx.showModal({
          title: request.cost_tier === 'low' ? '已自动执行' : '已提交审批',
          content: (request.estimate && request.estimate.reason) || '申请已保存',
          showCancel: false,
        });
        this.setData({
          showApply: false, applyTitle: '', applyIntro: '', applyKeywords: '',
        });
        this.loadMyRequests();
      })
      .catch((e) => wx.showToast({ title: e.message || '提交失败', icon: 'none' }))
      .then(() => this.setData({ applying: false }));
  },

  openTopic(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/topic_view/topic_view?id=' + id });
  },
});
