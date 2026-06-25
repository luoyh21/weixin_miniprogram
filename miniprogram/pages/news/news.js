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

// 统一的分页大小：首屏与滚动加载用同一套「小页 + 按需拉取」逻辑，
// 不再做后台批量预取（那会和滚动加载抢资源、并触发整列表 setData 卡顿）。
const PAGE_SIZE = 10;
// 久置/隔天再回到页面时自动刷新的阈值
const REFRESH_IDLE_MS = 10 * 60 * 1000;

function fmtToday() {
  return fmtDate(new Date());
}

Page({
  data: {
    filters: FILTERS,
    active: '',
    hasMore: false,
    loadingMore: false,
    groups: [],
    kinds: { intl: 0, gzh: 0, douyin: 0, social: 0 },
    loading: true,
    error: '',
  },

  // 渲染分组的真源放实例上，配合「定点 setData」增量追加，避免每页重建整列表
  _groups: [],
  _offset: 0,
  _reqSeq: 0,        // 防止快速切换分类时旧请求把新结果覆盖
  _lastLoadAt: 0,    // 上次成功发起加载的时刻（onShow 判断是否需刷新）
  _loadDay: '',      // 上次加载所属日期

  onLoad() {
    this.load();
  },

  onShow() {
    // 冷启动由 onLoad 负责；这里只处理「切后台再回来 / 隔天再打开」时拉取今日新内容
    if (!this._lastLoadAt) return;
    const staleDay = this._loadDay !== fmtToday();
    const idleLong = Date.now() - this._lastLoadAt > REFRESH_IDLE_MS;
    if (staleDay || idleLong) this.load();
  },

  onPullDownRefresh() {
    this.load(true);
  },

  _url(offset, limit) {
    let u = '/news/week?days=14&offset=' + offset + '&limit=' + limit;
    if (this.data.active) u += '&kind=' + this.data.active;
    return u;
  },

  // 增量把一批新条目按日期分组追加进 data.groups：
  // 只「定点」更新边界分组的 items 和新出现的分组，已渲染的旧分组完全不动 → 不卡。
  _appendItems(items) {
    if (!items || !items.length) return;
    const preLen = this._groups.length;
    items.forEach((it) => {
      const date = (it.published || '').slice(0, 10) || '更早';
      let last = this._groups[this._groups.length - 1];
      if (!last || last.date !== date) {
        last = { date, label: dateLabel(date), items: [] };
        this._groups.push(last);
      }
      last.items.push(it);
    });
    const setObj = {};
    // 追加前已存在的最后一个分组可能"长大"了 → 只重发它的 items
    if (preLen > 0) {
      setObj['groups[' + (preLen - 1) + '].items'] = this._groups[preLen - 1].items;
    }
    // 本批新出现的分组 → 整组设置一次
    for (let gi = preLen; gi < this._groups.length; gi++) {
      setObj['groups[' + gi + ']'] = this._groups[gi];
    }
    this.setData(setObj);
  },

  // 首屏与刷新：清空后拉第一页
  load(fromPull) {
    this._groups = [];
    this._offset = 0;
    this._lastLoadAt = Date.now();
    this._loadDay = fmtToday();
    const seq = ++this._reqSeq;
    this.setData({ loading: true, error: '', groups: [], hasMore: false });
    api.get(this._url(0, PAGE_SIZE), { auth: false })
      .then((res) => {
        if (seq !== this._reqSeq) return;
        const items = res.items || [];
        this._offset = items.length;
        this.setData({
          kinds: res.kinds || this.data.kinds,
          hasMore: !!res.has_more,
          loading: false,
        });
        this._appendItems(items);
      })
      .catch((e) => {
        if (seq !== this._reqSeq) return;
        this._groups = [];
        this.setData({ loading: false, error: e.message || '加载失败', groups: [], hasMore: false });
      })
      .then(() => {
        if (fromPull) wx.stopPullDownRefresh();
      });
  },

  // 滚动到底再按需拉下一页（与首屏同样的小页逻辑）
  loadMore() {
    if (!this.data.hasMore || this.data.loadingMore) return;
    const seq = this._reqSeq;
    this.setData({ loadingMore: true });
    api.get(this._url(this._offset, PAGE_SIZE), { auth: false })
      .then((res) => {
        if (seq !== this._reqSeq) return;
        const items = res.items || [];
        this._offset += items.length;
        this.setData({ hasMore: !!res.has_more, loadingMore: false });
        this._appendItems(items);
      })
      .catch(() => {
        if (seq !== this._reqSeq) return;
        this.setData({ loadingMore: false });
      });
  },

  onReachBottom() {
    this.loadMore();
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
