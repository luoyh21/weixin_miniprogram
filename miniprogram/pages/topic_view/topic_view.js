const api = require('../../utils/api');

const FILTERS = [
  { key: '', label: '全部' },
  { key: 'region:国外', label: '国外' },
  { key: 'region:国内', label: '国内' },
  { key: 'aspect:总体设计', label: '总体设计' },
  { key: 'aspect:专业技术', label: '专业技术' },
];

function applyFilter(items, key) {
  if (!key) return items;
  const [field, val] = key.split(':');
  return (items || []).filter((it) => it[field] === val);
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
        this.setData({
          topic,
          shown: applyFilter(topic.items, this.data.active),
          loading: false,
        });
        if (topic.title) wx.setNavigationBarTitle({ title: topic.title });
      })
      .catch((e) => this.setData({ loading: false, error: e.message || '加载失败' }))
      .then(() => { if (fromPull) wx.stopPullDownRefresh(); });
  },

  switchFilter(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.active) return;
    this.setData({
      active: key,
      shown: applyFilter(this.data.topic.items, key),
    });
  },

  openItem(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/detail/detail?topic=' + this.data.id + '&id=' + id });
  },
});
