const api = require('../../utils/api');

const FILTERS = [
  { key: '', label: '全部' },
  { key: 'region:国外', label: '国外' },
  { key: 'region:国内', label: '国内' },
  { key: 'aspect:总体设计', label: '总体设计' },
  { key: 'aspect:专业技术', label: '专业技术' },
];

const SPLIT_RE = /[\s,，。;；/|]+/;

function applyFilter(items, key) {
  if (!key) return items;
  const [field, val] = key.split(':');
  return (items || []).filter((it) => it[field] === val);
}

// 专题条目数不多（单专题约 20~30 篇），搜索直接在已加载数据上做客户端模糊匹配 + 打分，
// 无需请求后端；多个词按 AND 匹配（子串），标题命中权重最高。
function matchScore(it, terms) {
  const title = (it.title || '').toLowerCase();
  const body = ((it.summary || '') + ' ' + (it.body_zh || '')).toLowerCase();
  const other = ((it.source || '') + ' ' + (it.tags || []).join(' ')).toLowerCase();
  let score = 0;
  for (let i = 0; i < terms.length; i++) {
    const t = terms[i];
    const inTitle = title.indexOf(t) >= 0;
    const inBody = body.indexOf(t) >= 0;
    const inOther = other.indexOf(t) >= 0;
    if (!inTitle && !inBody && !inOther) return -1; // 有词完全没命中 → 不算匹配
    if (inTitle) score += 5;
    if (inBody) score += 2;
    if (inOther) score += 1;
  }
  return score;
}

function applySearch(items, q, sort) {
  const terms = (q || '').trim().toLowerCase().split(SPLIT_RE).filter(Boolean);
  if (!terms.length) return items;
  const scored = [];
  (items || []).forEach((it) => {
    const s = matchScore(it, terms);
    if (s >= 0) scored.push([s, it]);
  });
  if (sort === 'score') scored.sort((a, b) => b[0] - a[0]);
  // sort === 'time'：items 传入前已按发布时间倒序，保持相对顺序即可
  return scored.map((p) => p[1]);
}

Page({
  data: {
    id: '',
    topic: null,
    filters: FILTERS,
    active: '',
    shown: [],
    loading: true,
    error: '',
    searchText: '',
    searchSort: 'time', // time | score
  },

  onLoad(query) {
    this.setData({ id: query.id || 'space-tug' });
    this.load();
  },

  onPullDownRefresh() {
    this.load(true);
  },

  load(fromPull) {
    this.setData({ loading: true, error: '' });
    api.get('/topic/get?id=' + this.data.id, { auth: false })
      .then((res) => {
        const topic = res.topic;
        this.setData({ topic, loading: false });
        this._recompute();
        if (topic.title) wx.setNavigationBarTitle({ title: topic.title });
      })
      .catch((e) => this.setData({ loading: false, error: e.message || '加载失败' }))
      .then(() => { if (fromPull) wx.stopPullDownRefresh(); });
  },

  // 分类筛选（区域/维度）与搜索叠加：先筛分类，再在结果里模糊搜索关键词。
  _recompute() {
    if (!this.data.topic) return;
    const base = applyFilter(this.data.topic.items, this.data.active);
    const q = this.data.searchText;
    const shown = q.trim() ? applySearch(base, q, this.data.searchSort) : base;
    this.setData({ shown });
  },

  switchFilter(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.active) return;
    this.setData({ active: key }, () => this._recompute());
  },

  onSearchInput(e) {
    this.setData({ searchText: e.detail.value }, () => this._recompute());
  },

  clearSearch() {
    this.setData({ searchText: '' }, () => this._recompute());
  },

  switchSearchSort(e) {
    const sort = e.currentTarget.dataset.sort;
    if (sort === this.data.searchSort) return;
    this.setData({ searchSort: sort }, () => this._recompute());
  },

  openItem(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/detail/detail?topic=' + this.data.id + '&id=' + id });
  },
});
