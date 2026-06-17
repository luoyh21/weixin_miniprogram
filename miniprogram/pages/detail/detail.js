const api = require('../../utils/api');

function toParagraphs(body) {
  if (!body) return [];
  // 去标签 + 反转义常见实体
  let t = String(body).replace(/<[^>]+>/g, '\n');
  t = t.replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
       .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
  return t.split(/\n+/).map((s) => s.trim()).filter((s) => s.length > 0);
}

Page({
  data: {
    item: null,
    paragraphs: [],
    loading: true,
    error: '',
  },

  onLoad(query) {
    const id = query.id;
    if (!id) {
      this.setData({ loading: false, error: '缺少参数' });
      return;
    }
    api.get('/news/item?id=' + id, { auth: false })
      .then((res) => {
        const item = res.item;
        this.setData({
          item,
          paragraphs: toParagraphs(item.body),
          loading: false,
        });
        wx.setNavigationBarTitle({ title: item.main_tag || '详情' });
      })
      .catch((e) => this.setData({ loading: false, error: e.message || '加载失败' }));
  },

  copyLink() {
    const link = this.data.item && this.data.item.link;
    if (!link) return;
    wx.setClipboardData({ data: link, success: () => wx.showToast({ title: '链接已复制', icon: 'none' }) });
  },

  copyShare() {
    const txt = this.data.item && this.data.item.share_text;
    if (!txt) return;
    wx.setClipboardData({ data: txt, success: () => wx.showToast({ title: '抖音口令已复制', icon: 'none' }) });
  },
});
