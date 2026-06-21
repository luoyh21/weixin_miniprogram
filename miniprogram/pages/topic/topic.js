const api = require('../../utils/api');

Page({
  data: {
    topics: [],
    loading: true,
    error: '',
  },

  onShow() {
    this.load();
  },

  onPullDownRefresh() {
    this.load(true);
  },

  load(fromPull) {
    this.setData({ loading: true, error: '' });
    api.get('/topic/list', { auth: false })
      .then((res) => {
        this.setData({ topics: res.topics || [], loading: false });
      })
      .catch((e) => {
        this.setData({ loading: false, error: e.message || '加载失败' });
      })
      .then(() => {
        if (fromPull) wx.stopPullDownRefresh();
      });
  },

  openTopic(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/topic_view/topic_view?id=' + id });
  },
});
