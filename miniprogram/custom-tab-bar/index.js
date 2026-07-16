const gate = require('../utils/gate');

const NORMAL = [
  { pagePath: '/pages/news/news', text: '速递', icon: '🛰️' },
  { pagePath: '/pages/topic/topic', text: '专题', icon: '📚' },
  { pagePath: '/pages/ask/ask', text: '问答', icon: '💬' },
  { pagePath: '/pages/account/account', text: '我的', icon: '👤' },
];

const RESTRICTED = [
  { pagePath: '/pages/calc/calc', text: '计算', icon: '🧮' },
  { pagePath: '/pages/account/account', text: '我的', icon: '👤' },
];

Component({
  data: {
    selected: 0,
    list: NORMAL,
  },
  lifetimes: {
    attached() {
      this.refresh();
    },
  },
  pageLifetimes: {
    show() {
      this.refresh();
    },
  },
  methods: {
    refresh() {
      const list = gate.restricted() ? RESTRICTED : NORMAL;
      if (list !== this.data.list) this.setData({ list });
    },
    setSelectedByPath(path) {
      const idx = this.data.list.findIndex((i) => i.pagePath === path);
      this.setData({ selected: idx < 0 ? 0 : idx });
    },
    onTap(e) {
      const path = e.currentTarget.dataset.path;
      if (path === this.data.list[this.data.selected].pagePath) return;
      wx.switchTab({ url: path });
    },
  },
});
