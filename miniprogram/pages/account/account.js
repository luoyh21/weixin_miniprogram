const api = require('../../utils/api');

Page({
  data: {
    loggedIn: false,
    isAdmin: false,
    isSuper: false,
    user: null,

    authMode: 'login', // login | register
    fAccount: '',
    fName: '',
    fPwd: '',
    submitting: false,

    // admin
    users: [],
    usersLoading: false,
    dy: { ok: false, detail: '', recent: [] },
    dyLoading: false,
    dyCookie: '',
    dySubmitting: false,
  },

  onShow() {
    const app = getApp();
    const loggedIn = app.isLoggedIn();
    const u = app.globalData.user || {};
    this.setData({ loggedIn, isAdmin: !!u.is_admin, isSuper: !!u.is_super, user: app.globalData.user });
    if (loggedIn) this.refreshMe();
    if (loggedIn && u.is_admin) this.loadUsers();
    if (loggedIn && u.is_super) this.loadDyStatus();
  },

  refreshMe() {
    api.get('/auth/me').then((res) => {
      const app = getApp();
      app.setAuth(app.globalData.token, res.user);
      this.setData({ user: res.user, isAdmin: !!res.user.is_admin, isSuper: !!res.user.is_super });
      if (res.user.is_admin && !this.data.users.length) this.loadUsers();
      if (res.user.is_super) this.loadDyStatus();
    }).catch(() => {});
  },

  // ---------- 表单 ----------
  switchMode(e) {
    this.setData({ authMode: e.currentTarget.dataset.mode });
  },
  onInput(e) {
    this.setData({ [e.currentTarget.dataset.field]: e.detail.value });
  },

  doLogin() {
    const { fAccount, fPwd } = this.data;
    if (!fAccount || !fPwd) return wx.showToast({ title: '请输入账号和密码', icon: 'none' });
    this.setData({ submitting: true });
    api.post('/auth/login', { account: fAccount, password: fPwd }, { auth: false })
      .then((res) => this.afterAuth(res))
      .catch((e) => wx.showToast({ title: e.message, icon: 'none' }))
      .then(() => this.setData({ submitting: false }));
  },

  doRegister() {
    const { fAccount, fName, fPwd } = this.data;
    if (!fAccount || !fName || !fPwd) return wx.showToast({ title: '请填写完整信息', icon: 'none' });
    this.setData({ submitting: true });
    api.post('/auth/register', { account: fAccount, real_name: fName, password: fPwd }, { auth: false })
      .then((res) => this.afterAuth(res))
      .catch((e) => wx.showToast({ title: e.message, icon: 'none' }))
      .then(() => this.setData({ submitting: false }));
  },

  afterAuth(res) {
    const app = getApp();
    app.setAuth(res.token, res.user);
    this.setData({
      loggedIn: true, isAdmin: !!res.user.is_admin, isSuper: !!res.user.is_super, user: res.user,
      fPwd: '', fName: '',
    });
    wx.showToast({ title: '欢迎，' + res.user.real_name, icon: 'none' });
    if (res.user.is_admin) this.loadUsers();
    if (res.user.is_super) this.loadDyStatus();
  },

  logout() {
    wx.showModal({
      title: '退出登录', content: '确定要退出当前账号吗？',
      success: (r) => {
        if (!r.confirm) return;
        getApp().clearAuth();
        this.setData({ loggedIn: false, isAdmin: false, isSuper: false, user: null, users: [] });
      },
    });
  },

  changePassword() {
    wx.showModal({
      title: '修改密码', editable: true, placeholderText: '输入原密码',
      success: (r1) => {
        if (!r1.confirm) return;
        const oldPwd = r1.content;
        wx.showModal({
          title: '修改密码', editable: true, placeholderText: '输入新密码（≥6位）',
          success: (r2) => {
            if (!r2.confirm) return;
            api.post('/auth/change_password', { old_password: oldPwd, new_password: r2.content })
              .then(() => wx.showToast({ title: '修改成功', icon: 'success' }))
              .catch((e) => wx.showToast({ title: e.message, icon: 'none' }));
          },
        });
      },
    });
  },

  // ---------- 管理员：用户 ----------
  loadUsers() {
    this.setData({ usersLoading: true });
    api.get('/admin/users')
      .then((res) => this.setData({ users: res.users || [] }))
      .catch((e) => wx.showToast({ title: e.message, icon: 'none' }))
      .then(() => this.setData({ usersLoading: false }));
  },

  manageUser(e) {
    const u = e.currentTarget.dataset.u;
    const me = this.data.user;
    const isSelf = u.account.toLowerCase() === me.account.toLowerCase();
    const targetIsAdmin = (u.role === 'admin' || u.role === 'super_admin');
    const meSuper = !!me.is_super;

    const items = [];
    const acts = [];
    // 重置密码：超管可对任何人；普通管理员仅对普通用户或自己
    if (meSuper || !targetIsAdmin || isSelf) { items.push('重置密码'); acts.push('reset'); }
    // 角色切换：仅超管，且目标不是超管
    if (meSuper && u.role !== 'super_admin') {
      items.push(u.role === 'admin' ? '取消管理员' : '设为管理员');
      acts.push('role');
    }
    // 删除：不能删自己；超管可删除非自己，普通管理员仅删普通用户
    if (!isSelf && (meSuper || !targetIsAdmin)) { items.push('删除用户'); acts.push('del'); }

    if (!items.length) {
      wx.showToast({ title: '无权管理该管理员', icon: 'none' });
      return;
    }
    wx.showActionSheet({
      itemList: items,
      success: (r) => {
        const a = acts[r.tapIndex];
        if (a === 'reset') this.resetPwd(u);
        else if (a === 'role') this.setRole(u, u.role === 'admin' ? 'user' : 'admin');
        else if (a === 'del') this.delUser(u);
      },
    });
  },

  resetPwd(u) {
    wx.showModal({
      title: '重置「' + u.real_name + '」的密码', editable: true, placeholderText: '新密码（≥6位）',
      success: (r) => {
        if (!r.confirm) return;
        api.post('/admin/users/update', { account: u.account, new_password: r.content })
          .then(() => wx.showToast({ title: '已重置', icon: 'success' }))
          .catch((e) => wx.showToast({ title: e.message, icon: 'none' }));
      },
    });
  },

  setRole(u, role) {
    api.post('/admin/users/update', { account: u.account, role })
      .then(() => { wx.showToast({ title: '已更新', icon: 'success' }); this.loadUsers(); })
      .catch((e) => wx.showToast({ title: e.message, icon: 'none' }));
  },

  delUser(u) {
    wx.showModal({
      title: '删除用户', content: '确定删除「' + u.real_name + '」(' + u.account + ')？',
      success: (r) => {
        if (!r.confirm) return;
        api.post('/admin/users/delete', { account: u.account })
          .then(() => { wx.showToast({ title: '已删除', icon: 'success' }); this.loadUsers(); })
          .catch((e) => wx.showToast({ title: e.message, icon: 'none' }));
      },
    });
  },

  // ---------- 管理员：抖音 cookie ----------
  loadDyStatus() {
    this.setData({ dyLoading: true });
    api.get('/admin/douyin/status')
      .then((res) => this.setData({ dy: { ok: res.ok, detail: res.detail, recent: res.recent || [] } }))
      .catch((e) => this.setData({ dy: { ok: false, detail: e.message, recent: [] } }))
      .then(() => this.setData({ dyLoading: false }));
  },

  onCookieInput(e) { this.setData({ dyCookie: e.detail.value }); },

  submitCookie() {
    const ck = (this.data.dyCookie || '').trim();
    if (ck.length < 30) return wx.showToast({ title: 'Cookie 太短', icon: 'none' });
    wx.showModal({
      title: '更新抖音 Cookie', content: '将写入抓取容器并重启，约需 10 秒，确定？',
      success: (r) => {
        if (!r.confirm) return;
        this.setData({ dySubmitting: true });
        wx.showLoading({ title: '更新并重启中…', mask: true });
        api.post('/admin/douyin/cookie', { cookie: ck })
          .then((res) => {
            wx.hideLoading();
            this.setData({ dy: { ok: res.ok, detail: res.detail, recent: res.recent || [] }, dyCookie: '' });
            wx.showToast({ title: res.ok ? '更新成功' : '已更新(仍异常)', icon: 'none' });
          })
          .catch((e) => { wx.hideLoading(); wx.showToast({ title: e.message, icon: 'none' }); })
          .then(() => this.setData({ dySubmitting: false }));
      },
    });
  },
});
