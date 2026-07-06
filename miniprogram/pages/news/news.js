const api = require('../../utils/api');
const gate = require('../../utils/gate');

const FILTERS = [
  { key: '', label: '全部' },
  { key: 'intl', label: '国际要闻' },
  { key: 'gzh', label: '公众号' },
  { key: 'douyin', label: '航天视频' },
  { key: 'techport', label: '技术港' },
  { key: 'launch', label: '每日发射' },
  { key: 'future', label: '未来发射' },
  { key: 'debris', label: '碎片更新' },
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

// 渐进式时间窗加载：首屏只取近 3 天（体量小、秒回），随后每 15s 自动向后
// 再扩 3 天，直到覆盖整个窗口（与后端 _WINDOW_DAYS 对齐）。这样弱网/微信冷启动
// 时也能先出内容、再慢慢补齐，避免首次打开长时间白屏。
const PAGE_DAYS = 3;
const MAX_DAYS = 15;
const EXPAND_MS = 15 * 1000;
// 首屏只渲染一小页（快出内容、setData 轻），随后每 15s 追加一整段（≈3 天）。
const FIRST_LIMIT = 15;
const EXPAND_LIMIT = 100; // 与后端单页上限对齐
// 久置/隔天再回到页面时自动刷新的阈值
const REFRESH_IDLE_MS = 10 * 60 * 1000;
// 首屏本地缓存：冷启动先渲染上次内容（秒开），再后台静默刷新，缓解微信冷启动等待。
// 仅缓存默认「全部」分类的第一页。
const HOME_CACHE_KEY = 'news_home_cache_v1';

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
    kinds: { intl: 0, gzh: 0, douyin: 0, social: 0, techport: 0, launch: 0, future: 0, debris: 0 },
    loading: true,
    error: '',
    imgErr: {}, // 加载失败的图（按条目 id 标记）→ 隐藏，不显示破图
  },

  // 渲染分组的真源放实例上，配合「定点 setData」增量追加，避免每页重建整列表
  _groups: [],
  _offset: 0,        // 当前时间窗内已加载的条数（即下次拉取的 offset）
  _days: PAGE_DAYS,  // 当前时间窗（天）：首屏 3 天，随后每 15s +3 天
  _windowHasMore: false, // 当前时间窗内是否还有下一页
  _expandTimer: null,    // 「每 15s 向后加载」的定时器
  _reqSeq: 0,        // 防止快速切换分类时旧请求把新结果覆盖
  _lastLoadAt: 0,    // 上次成功发起加载的时刻（onShow 判断是否需刷新）
  _loadDay: '',      // 上次加载所属日期

  onLoad() {
    // 审核受限期：速递不展示，直接跳到计算器。
    if (gate.restricted()) { wx.reLaunch({ url: '/pages/calc/calc' }); return; }
    // 冷启动先吃本地缓存秒开（仅默认分类），再静默拉最新覆盖；无缓存才走常规加载。
    const cached = this._readHomeCache();
    if (cached && cached.groups && cached.groups.length) {
      this._groups = cached.groups;
      this._offset = cached.offset || 0;
      this._lastLoadAt = 0; // 标记当前是缓存，触发后台刷新
      this.setData({
        groups: cached.groups,
        kinds: cached.kinds || this.data.kinds,
        hasMore: !!cached.hasMore,
        loading: false,
      });
      this.load(false, true); // 静默刷新
    } else {
      this.load();
    }
  },

  onShow() {
    if (gate.restricted()) { wx.reLaunch({ url: '/pages/calc/calc' }); return; }
    const tb = this.getTabBar && this.getTabBar();
    if (tb) { tb.refresh(); tb.setSelectedByPath('/pages/news/news'); }
    gate.refresh().then((r) => { if (r.changed) gate.applyToCurrentPage(); });
    // 切后台再回来 / 隔天再打开时拉取今日新内容（静默，不闪白屏）
    if (!this._lastLoadAt) return;
    const staleDay = this._loadDay !== fmtToday();
    const idleLong = Date.now() - this._lastLoadAt > REFRESH_IDLE_MS;
    if (staleDay || idleLong) { this.load(false, true); return; }
    // 未触发刷新但仍有未加载内容 → 继续「每 15s 向后加载」
    if (this.data.hasMore) this._startExpandTimer();
  },

  onHide() {
    this._stopExpandTimer();
  },

  onUnload() {
    this._stopExpandTimer();
  },

  _readHomeCache() {
    try {
      return wx.getStorageSync(HOME_CACHE_KEY) || null;
    } catch (e) {
      return null;
    }
  },

  _saveHomeCache(res, more) {
    if (this.data.active) return; // 只缓存默认「全部」
    try {
      wx.setStorageSync(HOME_CACHE_KEY, {
        ts: Date.now(),
        groups: this._groups,
        kinds: res.kinds || this.data.kinds,
        hasMore: !!more,
        offset: this._offset,
      });
    } catch (e) { /* 缓存失败忽略 */ }
  },

  // 按日期把一批条目构建成完整分组数组（首屏/刷新整体替换用）
  _buildGroupsFrom(items) {
    (items || []).forEach((it) => {
      const date = (it.published || '').slice(0, 10) || '更早';
      let last = this._groups[this._groups.length - 1];
      if (!last || last.date !== date) {
        last = { date, label: dateLabel(date), items: [] };
        this._groups.push(last);
      }
      last.items.push(it);
    });
  },

  onPullDownRefresh() {
    this.load(true);
  },

  _url(offset, limit) {
    let u = '/news/week?days=' + this._days + '&offset=' + offset + '&limit=' + limit;
    if (this.data.active) u += '&kind=' + this.data.active;
    return u;
  },

  // 当前是否还有可加载的内容：本窗还有下一页，或时间窗尚未扩到上限。
  _moreAvailable() {
    return this._windowHasMore || this._days < MAX_DAYS;
  },

  _startExpandTimer() {
    this._stopExpandTimer();
    this._expandTimer = setInterval(() => {
      if (!this.data.hasMore) { this._stopExpandTimer(); return; }
      if (this.data.loadingMore) return;
      this._loadNext();
    }, EXPAND_MS);
  },

  _stopExpandTimer() {
    if (this._expandTimer) { clearInterval(this._expandTimer); this._expandTimer = null; }
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

  // 首屏与刷新：拉第一页。quiet=true 时不清空、不显 loading（保留当前/缓存内容，
  // 等新数据回来再整体替换），用于「缓存秒开后的静默刷新」与「切回页面刷新」，避免闪白屏。
  load(fromPull, quiet) {
    this._lastLoadAt = Date.now();
    this._loadDay = fmtToday();
    this._days = PAGE_DAYS;         // 每次首屏/刷新都从近 3 天重新开始渐进加载
    this._windowHasMore = false;
    this._stopExpandTimer();
    const seq = ++this._reqSeq;
    if (!quiet) {
      this._groups = [];
      this._offset = 0;
      this.setData({ loading: true, error: '', groups: [], hasMore: false });
    }
    api.get(this._url(0, FIRST_LIMIT), { auth: false })
      .then((res) => {
        if (seq !== this._reqSeq) return;
        const items = res.items || [];
        // 首屏只渲染一小页（快出内容），后续扩窗走 _appendItems 增量
        this._groups = [];
        this._buildGroupsFrom(items);
        this._offset = items.length;
        this._windowHasMore = !!res.has_more;
        const more = this._moreAvailable();
        this.setData({
          kinds: res.kinds || this.data.kinds,
          hasMore: more,
          loading: false,
          error: '',
          groups: this._groups,
        });
        this._saveHomeCache(res, more);
        if (more) this._startExpandTimer();
      })
      .catch((e) => {
        if (seq !== this._reqSeq) return;
        if (quiet) return; // 静默刷新失败：保留已展示内容，不打扰
        this._groups = [];
        this.setData({ loading: false, error: e.message || '加载失败', groups: [], hasMore: false });
      })
      .then(() => {
        if (fromPull) wx.stopPullDownRefresh();
      });
  },

  // 加载下一段：优先取尽「当前时间窗」剩余分页；取尽后把窗口向后 +3 天再取。
  // 由 15s 定时器自动调用，也可被「滚动到底」立即触发。
  _loadNext() {
    if (this.data.loadingMore) return;
    if (!this._windowHasMore && this._days >= MAX_DAYS) {
      this._stopExpandTimer();
      if (this.data.hasMore) this.setData({ hasMore: false });
      return;
    }
    // 当前窗取尽、但还能向后扩展 → 先把窗口 +3 天（offset 不变即取到新出现的更早条目）
    if (!this._windowHasMore && this._days < MAX_DAYS) {
      this._days = Math.min(MAX_DAYS, this._days + PAGE_DAYS);
    }
    const seq = this._reqSeq;
    this.setData({ loadingMore: true });
    api.get(this._url(this._offset, EXPAND_LIMIT), { auth: false })
      .then((res) => {
        if (seq !== this._reqSeq) return;
        const items = res.items || [];
        this._offset += items.length;
        this._windowHasMore = !!res.has_more;
        this._appendItems(items);
        const more = this._moreAvailable();
        this.setData({ loadingMore: false, hasMore: more });
        if (!more) this._stopExpandTimer();
      })
      .catch(() => {
        if (seq !== this._reqSeq) return;
        this.setData({ loadingMore: false });
      });
  },

  onReachBottom() {
    this._loadNext();
  },

  switchFilter(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.active) return;
    this.setData({ active: key }, () => this.load());
  },

  // 图片加载失败 → 标记该条目隐藏缩略图（境内偶发拉不到的境外图不留破框）
  onImgError(e) {
    const id = e.currentTarget.dataset.id;
    if (id) this.setData({ ['imgErr.' + id]: true });
  },

  openDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/detail/detail?id=' + id });
  },
});
