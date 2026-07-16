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
// 渐进加载（首屏小窗→每 15s 向后扩到 MAX_DAYS）仅用于内容量大/更新频繁的栏目；
// 值为「首屏起始天数」。未列出的栏目（公众号/航天视频/未来发射/碎片更新）内容少且
// 发布日期可能偏旧，直接用整窗首屏，避免首屏空态、十几秒后才突然刷出。
// key '' = 全部。每日发射(launch)/技术港(techport)从 7 天起扩到 15。
const PROGRESSIVE_START = { '': PAGE_DAYS, intl: PAGE_DAYS, launch: 7, techport: 7, social: PAGE_DAYS };
// 久置/隔天再回到页面时自动刷新的阈值
const REFRESH_IDLE_MS = 10 * 60 * 1000;
// 首屏本地缓存：冷启动先渲染上次内容（秒开），再后台静默刷新，缓解微信冷启动等待。
// 仅缓存默认「全部」分类的第一页。
const HOME_CACHE_KEY = 'news_home_cache_v1';

// 搜索：覆盖全部历史（后端 /news/search 会回补 15 天窗口之外的归档），输入停顿
// SEARCH_DEBOUNCE_MS 后自动触发，避免每敲一个字就发请求。
const SEARCH_DEBOUNCE_MS = 500;
const SEARCH_PAGE_SIZE = 20;

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

    // 搜索：与当前分类 tab 联动（active 变化时若在搜索中会自动换 kind 重搜），
    // 搜索的是全部历史（不受下面渐进加载的 14 天窗口限制）。
    searchText: '',
    searching: false,
    searchSort: 'time',  // time | score
    searchScope: 'all',  // all（标题+正文+来源） | title（只匹配标题，更精确）
    searchLoading: false,
    searchLoadingMore: false,
    searchError: '',
    searchResults: [],
    searchHasMore: false,
    searchTotal: 0,
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
  _searchOffset: 0,
  _searchReqSeq: 0,
  _searchTimer: null,

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
    if (this.data.searching) return; // 搜索中：不被后台刷新/渐进加载打扰
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
    clearTimeout(this._searchTimer);
  },

  onUnload() {
    this._stopExpandTimer();
    clearTimeout(this._searchTimer);
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
    if (this.data.searching) {
      this._runSearch(true);
      setTimeout(() => wx.stopPullDownRefresh(), 400);
      return;
    }
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
    // 渐进栏目（全部/国际要闻/每日发射/技术港/政要社媒）用配置的起始天数逐步扩窗；
    // 其余栏目内容少、发布日期可能偏旧 → 直接整窗首屏，避免首屏空态、十几秒后才刷出。
    const start = PROGRESSIVE_START[this.data.active];
    this._days = (start !== undefined) ? start : MAX_DAYS;
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
    if (this.data.searching) { this._loadMoreSearch(); return; }
    this._loadNext();
  },

  switchFilter(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.active) return;
    this.setData({ active: key }, () => {
      // 搜索中切 tab：保持搜索态，换个分类范围重搜；否则走常规分类加载
      if (this.data.searching) this._runSearch(true);
      else this.load();
    });
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

  // ---------------- 搜索：模糊匹配，覆盖全部历史（不受 14 天窗口限制） ----------------

  onSearchInput(e) {
    const v = e.detail.value;
    this.setData({ searchText: v });
    clearTimeout(this._searchTimer);
    if (!v.trim()) {
      if (this.data.searching) this._exitSearch();
      return;
    }
    this._searchTimer = setTimeout(() => this._runSearch(true), SEARCH_DEBOUNCE_MS);
  },

  onSearchConfirm() {
    clearTimeout(this._searchTimer);
    const v = this.data.searchText.trim();
    if (!v) { this._exitSearch(); return; }
    this._runSearch(true);
  },

  clearSearch() {
    clearTimeout(this._searchTimer);
    this.setData({ searchText: '' });
    this._exitSearch();
  },

  _exitSearch() {
    this.setData({
      searching: false, searchResults: [], searchError: '', searchTotal: 0, searchHasMore: false,
    });
    this.load();
  },

  switchSearchSort(e) {
    const sort = e.currentTarget.dataset.sort;
    if (sort === this.data.searchSort) return;
    this.setData({ searchSort: sort }, () => this._runSearch(true));
  },

  switchSearchScope(e) {
    const scope = e.currentTarget.dataset.scope;
    if (scope === this.data.searchScope) return;
    this.setData({ searchScope: scope }, () => this._runSearch(true));
  },

  // reset=true：新搜索/换 tab/换排序，从第一页开始；false：上拉加载下一页
  _runSearch(reset) {
    const q = this.data.searchText.trim();
    if (!q) return;
    this._stopExpandTimer(); // 搜索时不需要「全部」栏目的渐进扩窗定时器
    const seq = ++this._searchReqSeq;
    if (reset) {
      this._searchOffset = 0;
      this.setData({
        searching: true, searchLoading: true, searchError: '', searchResults: [], searchHasMore: false,
      });
    } else {
      this.setData({ searchLoadingMore: true });
    }
    const kind = this.data.active;
    let url = '/news/search?q=' + encodeURIComponent(q) +
      '&sort=' + this.data.searchSort +
      '&scope=' + this.data.searchScope +
      '&offset=' + this._searchOffset + '&limit=' + SEARCH_PAGE_SIZE;
    if (kind) url += '&kind=' + kind;
    api.get(url, { auth: false })
      .then((res) => {
        if (seq !== this._searchReqSeq) return;
        const items = res.items || [];
        this._searchOffset += items.length;
        const merged = reset ? items : this.data.searchResults.concat(items);
        this.setData({
          searching: true,
          searchLoading: false,
          searchLoadingMore: false,
          searchResults: merged,
          searchTotal: res.total || 0,
          searchHasMore: !!res.has_more,
          searchError: '',
        });
      })
      .catch((e) => {
        if (seq !== this._searchReqSeq) return;
        this.setData({
          searchLoading: false, searchLoadingMore: false, searchError: e.message || '搜索失败',
        });
      });
  },

  _loadMoreSearch() {
    if (this.data.searchLoadingMore || !this.data.searchHasMore) return;
    this._runSearch(false);
  },
});
