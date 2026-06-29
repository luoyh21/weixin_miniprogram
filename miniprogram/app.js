const gate = require('./utils/gate');

App({
  globalData: {
    token: '',
    user: null,
  },

  onLaunch() {
    this.globalData.token = wx.getStorageSync('token') || '';
    this.globalData.user = wx.getStorageSync('user') || null;
    // 启动即拉取「真实/计算器」总开关；若与缓存不同，对当前页重新路由
    gate.refresh().then((r) => { if (r.changed) gate.applyToCurrentPage(); });
  },

  setAuth(token, user) {
    this.globalData.token = token || '';
    this.globalData.user = user || null;
    if (token) {
      wx.setStorageSync('token', token);
    } else {
      wx.removeStorageSync('token');
    }
    if (user) {
      wx.setStorageSync('user', user);
    } else {
      wx.removeStorageSync('user');
    }
  },

  clearAuth() {
    this.setAuth('', null);
  },

  isLoggedIn() {
    return !!this.globalData.token;
  },

  isAdmin() {
    return !!(this.globalData.user && this.globalData.user.is_admin);
  },

  isSuper() {
    return !!(this.globalData.user && this.globalData.user.is_super);
  },
});
