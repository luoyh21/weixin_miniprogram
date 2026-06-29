const gate = require('../../utils/gate');

const OPS = '+-*/';

// 先乘除后加减，仅支持 + - * / 与小数；返回数值、'DIV0'（除零）或 null（无法计算）。
function evaluate(raw) {
  let e = String(raw || '');
  while (e && (OPS.indexOf(e.slice(-1)) >= 0 || e.slice(-1) === '.')) e = e.slice(0, -1);
  if (!e) return null;
  if (e[0] === '+') e = e.slice(1);
  if (e[0] === '-') e = '0' + e;
  const tokens = e.match(/(\d+(\.\d+)?)|[+\-*/]/g);
  if (!tokens) return null;
  const nums = [parseFloat(tokens[0])];
  if (isNaN(nums[0])) return null;
  for (let i = 1; i < tokens.length; i += 2) {
    const op = tokens[i];
    const n = parseFloat(tokens[i + 1]);
    if (isNaN(n)) return null;
    if (op === '*') nums.push(nums.pop() * n);
    else if (op === '/') { if (n === 0) return 'DIV0'; nums.push(nums.pop() / n); }
    else if (op === '+') nums.push(n);
    else if (op === '-') nums.push(-n);
  }
  let s = nums.reduce((a, b) => a + b, 0);
  if (!isFinite(s)) return 'DIV0';
  // 去除浮点误差尾巴
  s = Math.round(s * 1e10) / 1e10;
  return s;
}

Page({
  data: {
    expr: '',
    result: '0',
  },

  onLoad() {
    this._guard();
  },

  onShow() {
    if (this._guard()) return;
    const tb = this.getTabBar && this.getTabBar();
    if (tb) {
      tb.refresh();
      tb.setSelectedByPath('/pages/calc/calc');
    }
    gate.refresh().then((r) => { if (r.changed) gate.applyToCurrentPage(); });
  },

  // 开关切到真实态：计算器不再展示，回到速递。
  onGateChange(real) {
    if (real) wx.reLaunch({ url: '/pages/news/news' });
  },

  // 开关为真实态时，计算器不展示，回到速递。
  _guard() {
    if (!gate.restricted()) {
      wx.reLaunch({ url: '/pages/news/news' });
      return true;
    }
    return false;
  },

  _preview(expr) {
    const r = evaluate(expr);
    if (r === null) return '';
    if (r === 'DIV0') return '错误';
    return String(r);
  },

  tap(e) {
    const ds = e.currentTarget.dataset;
    if (ds.act === 'clear') {
      this.setData({ expr: '', result: '0' });
      return;
    }
    if (ds.act === 'back') {
      const ex = this.data.expr.slice(0, -1);
      this.setData({ expr: ex, result: this._preview(ex) || '0' });
      return;
    }
    if (ds.act === 'eq') {
      const r = evaluate(this.data.expr);
      if (r === null) return;
      if (r === 'DIV0') { this.setData({ result: '错误' }); return; }
      this.setData({ expr: String(r), result: String(r) });
      return;
    }

    const k = ds.k;
    let ex = this.data.expr;
    const isOp = OPS.indexOf(k) >= 0;
    if (isOp) {
      if (!ex) {
        if (k === '-') ex = '-'; // 允许以负号开头
        else return;
      } else if (OPS.indexOf(ex.slice(-1)) >= 0) {
        ex = ex.slice(0, -1) + k; // 连续运算符则替换
      } else {
        ex += k;
      }
    } else if (k === '.') {
      const tail = ex.split(/[+\-*/]/).pop();
      if (tail.indexOf('.') >= 0) return; // 当前数字已有小数点
      ex += tail ? '.' : '0.';
    } else {
      ex += k; // 数字
    }
    this.setData({ expr: ex, result: this._preview(ex) || '0' });
  },
});
