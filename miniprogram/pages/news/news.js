const api = require('../../utils/api');

const FILTERS = [
  { key: '', label: '全部' },
  { key: 'intl', label: '国际要闻' },
  { key: 'gzh', label: '公众号' },
  { key: 'douyin', label: '航天视频' },
  { key: 'social', label: '政要社媒' },
];

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function fmtDate(d) {
  const y = d.getFullYear();
  const m = ('0' + (d.getMonth() + 1)).slice(-2);
  const day = ('0' + d.getDate()).slice(-2);
  return y + '-' + m + '-' + day;
}

function dateLabel(date) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return date || '更早';
  const today = fmtDate(new Date());
  const y = new Date();
  y.setDate(y.getDate() - 1);
  const yest = fmtDate(y);
  const dt = new Date(date.replace(/-/g, '/'));
  const md = (date.slice(5, 7)) + '月' + (date.slice(8, 10)) + '日';
  const wd = WEEKDAYS[dt.getDay()];
  if (date === today) return '今天 · ' + md + ' ' + wd;
  if (date === yest) return '昨天 · ' + md + ' ' + wd;
  return md + ' ' + wd;
}

function buildGroups(items) {
  const groups = [];
  const map = {};
  (items || []).forEach((it) => {
    const date = (it.published || '').slice(0, 10) || '更早';
    if (!map[date]) {
      map[date] = { date, label: dateLabel(date), items: [] };
      groups.push(map[date]);
    }
    map[date].items.push(it);
  });
  return groups;
}

Page({
  data: {
    filters: FILTERS,
    active: '',
    items: [],
    groups: [],
    kinds: { intl: 0, gzh: 0, douyin: 0, social: 0 },
    loading: true,
    error: '',
  },

  onLoad() {
    this.load();
  },

  onPullDownRefresh() {
    this.load(true);
  },

  load(fromPull) {
    this.setData({ loading: true, error: '' });
    api.get('/news/week?days=30' + (this.data.active ? '&kind=' + this.data.active : ''), { auth: false })
      .then((res) => {
        const items = res.items || [];
        this.setData({
          items,
          groups: buildGroups(items),
          kinds: res.kinds || this.data.kinds,
          loading: false,
        });
      })
      .catch((e) => {
        this.setData({ loading: false, error: e.message || '加载失败' });
      })
      .then(() => {
        if (fromPull) wx.stopPullDownRefresh();
      });
  },

  switchFilter(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.active) return;
    this.setData({ active: key }, () => this.load());
  },

  openDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/detail/detail?id=' + id });
  },
});
