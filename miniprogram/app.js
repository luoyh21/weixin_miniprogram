App({
  globalData: {
    token: '',
    user: null,
  },

  onLaunch() {
    this.globalData.token = wx.getStorageSync('token') || '';
    this.globalData.user = wx.getStorageSync('user') || null;
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
});
