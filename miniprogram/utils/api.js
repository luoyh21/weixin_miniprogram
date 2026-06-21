const BASE = 'https://links.he-ting.com/api';

function request(path, options) {
  options = options || {};
  const method = options.method || 'GET';
  const needAuth = options.auth !== false;
  const header = { 'Content-Type': 'application/json' };
  if (needAuth) {
    const token = wx.getStorageSync('token');
    if (token) header['Authorization'] = 'Bearer ' + token;
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + path,
      method,
      data: options.data || {},
      header,
      timeout: options.timeout || 60000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
          return;
        }
        if (res.statusCode === 401) {
          // 登录态失效
          const app = getApp();
          if (app && app.clearAuth) app.clearAuth();
        }
        const msg = (res.data && (res.data.detail || res.data.error)) ||
          ('请求失败 (' + res.statusCode + ')');
        reject(new Error(msg));
      },
      fail(err) {
        reject(new Error((err && err.errMsg) || '网络错误，请稍后重试'));
      },
    });
  });
}

const get = (path, opt) => request(path, Object.assign({ method: 'GET' }, opt));
const post = (path, data, opt) =>
  request(path, Object.assign({ method: 'POST', data }, opt));

module.exports = { BASE, request, get, post };
