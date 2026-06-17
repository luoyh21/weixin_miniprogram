const api = require('../../utils/api');

const SUGGESTIONS = [
  '最近有哪些火箭发射？',
  '总结一下本周国际航天要闻',
  '关于月球着陆器有什么新进展？',
  'NASA 最近发布了什么？',
];

function parseAnswer(md) {
  const links = [];
  const re = /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g;
  let text = String(md || '').replace(re, (m, t, u) => {
    links.push({ text: t, url: u });
    return t;
  });
  text = text.replace(/\*\*/g, '').replace(/^#+\s*/gm, '');
  return { text: text.trim(), links };
}

Page({
  data: {
    suggestions: SUGGESTIONS,
    messages: [],
    input: '',
    sending: false,
    loggedIn: false,
    scrollTo: '',
  },

  onShow() {
    const app = getApp();
    this.setData({ loggedIn: app.isLoggedIn() });
  },

  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  useSuggestion(e) {
    this.setData({ input: e.currentTarget.dataset.q }, () => this.send());
  },

  goLogin() {
    wx.switchTab({ url: '/pages/account/account' });
  },

  send() {
    const app = getApp();
    if (!app.isLoggedIn()) {
      this.setData({ loggedIn: false });
      wx.showToast({ title: '请先登录', icon: 'none' });
      return;
    }
    const q = (this.data.input || '').trim();
    if (!q || this.data.sending) return;

    const msgs = this.data.messages.concat([
      { id: 'm' + Date.now() + 'u', role: 'user', text: q },
      { id: 'm' + Date.now() + 'a', role: 'ai', text: '正在思考…', links: [], pending: true },
    ]);
    const lastId = msgs[msgs.length - 1].id;
    this.setData({ messages: msgs, input: '', sending: true, scrollTo: lastId });

    api.post('/qa/ask', { question: q })
      .then((res) => {
        const parsed = parseAnswer(res.answer);
        this.updateLast({ text: parsed.text, links: parsed.links, pending: false });
      })
      .catch((e) => {
        const msg = e.message || '回答失败';
        this.updateLast({ text: '抱歉，' + msg, links: [], pending: false });
        if (/登录/.test(msg)) this.setData({ loggedIn: false });
      })
      .then(() => this.setData({ sending: false }));
  },

  updateLast(patch) {
    const messages = this.data.messages.slice();
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'ai') {
        messages[i] = Object.assign({}, messages[i], patch);
        break;
      }
    }
    this.setData({ messages, scrollTo: messages[messages.length - 1].id });
  },

  copyLink(e) {
    const url = e.currentTarget.dataset.url;
    wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '链接已复制', icon: 'none' }) });
  },
});
